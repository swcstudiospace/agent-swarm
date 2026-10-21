"""End-to-end parallel run of the real `scripts/swarm_run.py` (v2.2 AC4, AC5, AC6, AC11).

The sessions are the fake `omp` of tests/test_scheduler_continuous.py (imported, not copied); the plan is one task per
agent of the thirty plus two extra A12 tasks (A12's ceiling is 2) in a mixed DAG: a diamond (A02 -> A03, A04 -> A05), two
independent lanes, one chain per new domain (cloud-native, cluster, web3) and one failing and one timed-out task. Facts are read from what the processes recorded themselves:
the fake's start/end timestamps, `events.jsonl`, `live.json` snapshots polled while the runner ran, and the Task Store.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import textwrap
import threading
import time

import pytest

import test_scheduler_continuous as sc
from conftest import ROOT, _scrubbed_base_env
from swarm.schema import load_schema, validate

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}
CAP = 4
A12_CEILING = 2

# agent id -> a capability of its manifest entry (the plan resolves the task's agent from this pair)
CAPABILITY = {
    "A01": "plan.decompose", "A02": "req.spec", "A03": "design.blueprint", "A04": "ux.spec", "A05": "code.backend",
    "A06": "code.frontend", "A07": "data.model", "A08": "test.unit", "A09": "review.code", "A10": "sec.sast",
    "A11": "deploy.env", "A12": "release.plan", "A13": "obs.slo", "A14": "maint.patch", "A15": "docs.api",
    "A16": "cloud.architecture", "A17": "platform.catalog", "A18": "supply.sbom", "A19": "event.topology",
    "A20": "finops.model", "A21": "cluster.lifecycle", "A22": "gitops.reconcile", "A23": "mesh.traffic",
    "A24": "kpol.admission", "A25": "resilience.chaos", "A26": "web3.contract", "A27": "audit.static",
    "A28": "chain.node", "A29": "dapp.wallet", "A30": "custody.keys",
}
# task id -> (agent, dependencies). Lane 1 holds the diamond, lane 2 is independent of it.
DAG = {
    "T-a01": ("A01", []), "T-a02": ("A02", []),
    "T-a03": ("A03", ["T-a02"]), "T-a04": ("A04", ["T-a02"]),               # diamond: fan out ...
    "T-a05": ("A05", ["T-a03", "T-a04"]),                                    # ... and join
    "T-a06": ("A06", ["T-a04"]), "T-a10": ("A10", ["T-a05"]),
    "T-a07": ("A07", []), "T-a08": ("A08", ["T-a07"]), "T-a09": ("A09", ["T-a08"]),   # lane 2
    "T-a11": ("A11", []), "T-a13": ("A13", ["T-a11"]),
    "T-a12": ("A12", []), "T-a12b": ("A12", []), "T-a12c": ("A12", []),
    # cloud-native: the architecture hangs off A03, events/golden path/cost fan out from it, the image waits for the backend
    "T-a16": ("A16", ["T-a03"]), "T-a19": ("A19", ["T-a16"]), "T-a17": ("A17", ["T-a16"]), "T-a20": ("A20", ["T-a16"]),
    "T-a18": ("A18", ["T-a05"]),
    # cluster: capacity first, traffic and admission policy side by side, then the manifests and resilience
    "T-a21": ("A21", []), "T-a23": ("A23", ["T-a21"]), "T-a24": ("A24", ["T-a21"]),
    "T-a22": ("A22", ["T-a23", "T-a24"]), "T-a25": ("A25", ["T-a22"]),
    # web3: custody policy before the contracts; the dapp, audit and chain infrastructure all read the contracts
    "T-a30": ("A30", ["T-a03"]), "T-a26": ("A26", ["T-a30"]), "T-a29": ("A29", ["T-a26"]),
    "T-a27": ("A27", ["T-a26"]), "T-a28": ("A28", ["T-a26"]),
    "T-a14": ("A14", []),   # fails
    "T-a15": ("A15", []),   # never finishes: killed by --task-timeout
}
FAILING, HANGING = "T-a14", "T-a15"


_BASE_FAKE = sc._FAKE


def fake_runtime_source(*, echo_env: bool = False) -> str:
    """The scheduler tests' fake `omp`, extended with two cfg keys: `echo_env` (names of env vars to echo as a bash tool
    call, a secret leaking into the session stream) and `summary` (the result's summary_md). Fails loudly when the base fake
    changes shape."""
    src = _BASE_FAKE
    probe = 'for tool in c.get("tools", []):'
    summary = '"summary_md": "ok"'
    assert probe in src and summary in src, "tests/test_scheduler_continuous.py: the fake omp changed; update fake_runtime_source"
    echo = ('for key in c.get("echo_env", []):\n'
            '    emit({"type": "tool_execution_start", "toolName": "bash", "args": {"command": "echo " + os.environ.get(key, "")}})\n')
    src = src.replace(probe, echo + probe, 1) if echo_env else src
    return src.replace(summary, '"summary_md": c.get("summary", "ok")', 1)


class Run:
    """One real `swarm_run.py` process over a planned workspace, with live.json polled while it runs."""

    def __init__(self, tmp_path, tasks, cfg, monkeypatch, **env):
        monkeypatch.setattr(sc, "_FAKE", fake_runtime_source(echo_env=bool(env.pop("echo_env", False))))
        self.rig = sc.Rig(tmp_path, tasks, cfg, **env)
        self.snapshots: list[dict] = []
        self.returncode: int | None = None
        self.wall_seconds = 0.0

    def start(self, *extra, python_path: str | None = None) -> subprocess.Popen:
        rig = self.rig
        env = {**_scrubbed_base_env(), **rig.env}
        # module-scoped fixtures start the runner BEFORE the function-scoped `ephemeral_signing_key` autouse fixture runs, so a
        # host without a signing key (CI) would make the runner fail closed: give it its own throwaway key, never the host's
        for host_key in ("SWARM_ED25519_KEY", "SWARM_ED25519_KEY_FILE", "SWARM_REQUIRE_KEY"):
            env.pop(host_key, None)
        env.setdefault("SWARM_SIGNING_KEY", secrets.token_hex(32))
        if python_path:
            env["PYTHONPATH"] = python_path
        self._out = open(rig.tmp / "runner.out", "w")
        self._err = open(rig.tmp / "runner.err", "w")
        self.proc = subprocess.Popen(rig.command(*extra), stdout=self._out, stderr=self._err, text=True, cwd=ROOT, env=env,
                                     start_new_session=True)
        self._started = time.monotonic()
        return self.proc

    def poll_until_exit(self, timeout: float = 90.0, on_tick=None) -> int:
        live_path = self.rig.swarm / "live.json"
        deadline = time.monotonic() + timeout
        last_seq = -1
        while self.proc.poll() is None:
            assert time.monotonic() < deadline, "the runner did not finish"
            try:
                doc = json.loads(live_path.read_text())
            except (OSError, ValueError):
                doc = None
            if doc is not None and doc["seq"] != last_seq:
                last_seq = doc["seq"]
                self.snapshots.append(doc)
            if on_tick is not None:
                on_tick()
            time.sleep(0.03)
        self.wall_seconds = time.monotonic() - self._started
        self.returncode = self.proc.returncode
        self._out.close()
        self._err.close()
        return self.returncode

    def kill(self) -> None:
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()

    def live(self) -> dict:
        return json.loads((self.rig.swarm / "live.json").read_text())

    @property
    def stdout(self) -> str:
        return (self.rig.tmp / "runner.out").read_text()

    @property
    def stderr(self) -> str:
        return (self.rig.tmp / "runner.err").read_text()


def agent_peak(spans, tasks, agent: str) -> int:
    return sc._max_concurrency({t: s for t, s in spans.items() if tasks[t][0] == agent})


# ------------------------------------------------------------------------------------------------ the 32-task DAG run
@pytest.fixture(scope="module")
def dag_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e-dag")
    monkeypatch = pytest.MonkeyPatch()
    tasks = [sc._task(tid[2:], agent, CAPABILITY[agent], [d[2:] for d in deps]) for tid, (agent, deps) in DAG.items()]  # the plan prefixes "T-"
    cfg = {"*": {"sleep": 0.35}, FAILING: {"fail": True}, HANGING: {"sleep": 60}}
    run = Run(tmp, tasks, cfg, monkeypatch, SWARM_HEARTBEAT_S="0.2")
    try:
        store = run.rig.store()
        for tid in (FAILING, HANGING):
            store.update(tid, max_attempts=1)  # one structured failure each, not a retry ladder
        for tid in ("T-a12", "T-a12b", "T-a12c"):
            run.rig.set_priority(tid, "P0")  # dispatched in the first pass: the third must wait for A12's ceiling
        for tid in ("T-a02", "T-a07"):
            run.rig.set_priority(tid, "P1")  # the roots of both lanes start in the first pass too
        run.start("--max-parallel", str(CAP), "--task-timeout", "2")
        run.poll_until_exit()
        yield run
    finally:
        monkeypatch.undo()
        if hasattr(run, "proc"):
            run.kill()


def test_the_plan_names_every_agent_of_the_thirty():
    from swarm.manifest import load_manifest

    manifest = {a["id"]: a["capabilities"] for a in load_manifest()}
    assert {agent for agent, _ in DAG.values()} == set(CAPABILITY) == set(manifest) and len(CAPABILITY) == 30
    assert all(cap in manifest[agent] for agent, cap in CAPABILITY.items())


def test_independent_tasks_ran_at_the_same_time(dag_run):
    spans = dag_run.rig.spans()
    started = {r["tid"] for r in dag_run.rig.fake_log() if r["ev"] == "start"}
    assert started == set(DAG), "a task was never dispatched"
    assert set(spans) == set(DAG) - {HANGING}, "the hanging session has no end record: it was killed"
    # lane 1 (A02 chain) and lane 2 (A07 chain) start together: both roots were dispatched in the first pass
    (a02,), (a07,) = spans["T-a02"], spans["T-a07"]
    assert max(a02[0], a07[0]) < min(a02[1], a07[1]), "the two roots of the independent lanes did not overlap"
    assert sc._max_concurrency(spans) >= 3
    # sessions of different agents were alive at once: count the distinct agents inside the busiest instant
    edges = sorted([(s, 1, t) for t, ps in spans.items() for s, _ in ps] + [(e, -1, t) for t, ps in spans.items() for _, e in ps])
    live, peak = set(), 0
    for _, step, tid in edges:
        live.add(tid) if step == 1 else live.discard(tid)
        peak = max(peak, len({DAG[t][0] for t in live}))
    assert peak >= 3, peak


def test_dependencies_start_only_after_their_upstream_finished(dag_run):
    spans = dag_run.rig.spans()
    for tid, (_agent, deps) in DAG.items():
        for dep in deps:
            if dep in spans and tid in spans:
                assert spans[tid][0][0] >= spans[dep][-1][1] - 0.05, f"{tid} started before {dep} ended"
    assert spans["T-a05"][0][0] >= max(spans["T-a03"][0][1], spans["T-a04"][0][1]) - 0.05  # the join waits for both


def test_the_global_cap_holds_and_the_overflow_queue_drains(dag_run):
    spans = dag_run.rig.spans()
    assert sc._max_concurrency(spans) == CAP, "never reached the cap, so the test did not exercise it"
    first = dag_run.rig.events("swarm.scheduler.tick")[0]["payload"]
    assert first["max_parallel"] == CAP and first["running"] == CAP and first["queued"] >= 1
    assert "no-free-slot" in first["skipped"].values()
    # polled snapshots from the runner itself never show more than CAP running
    assert dag_run.snapshots and max(s["swarm"]["running"] for s in dag_run.snapshots) == CAP
    # every task of the plan was dispatched in the end: nothing stayed queued
    states = dag_run.rig.states()
    assert set(states) == set(DAG) and "PLANNED" not in states.values() and "CLAIMED" not in states.values()


def test_a_per_agent_ceiling_is_honoured_with_free_global_slots(dag_run):
    spans = dag_run.rig.spans()
    assert agent_peak(spans, DAG, "A12") == A12_CEILING
    ticks = [e["payload"] for e in dag_run.rig.events("swarm.scheduler.tick")]
    assert any(t["skipped"] and "class-ceiling:A12" in t["skipped"].values() for t in ticks)
    in_flight = [sum(1 for r in s["runs"] if r["agent"] == "a12-release" and r["state"] in ("starting", "running"))
                 for s in dag_run.snapshots]
    assert max(in_flight) <= A12_CEILING
    assert all(t["per_agent"].get("a12-release", {"running": 0})["running"] <= A12_CEILING for t in ticks)


def test_a_failed_and_a_timed_out_task_do_not_stop_the_other_runs(dag_run):
    states = dag_run.rig.states()
    assert states[FAILING] == "ESCALATED" and states[HANGING] == "ESCALATED"
    siblings = {t: s for t, s in states.items() if t not in (FAILING, HANGING)}
    assert set(siblings.values()) == {"DONE"}, siblings
    finished = {e["task_id"]: e["payload"] for e in dag_run.rig.events("swarm.run.finished")}
    assert finished[FAILING]["state"] == "failed" and finished[FAILING]["error_code"] == "E-CONTRACT"
    assert finished[HANGING]["state"] == "timed_out" and finished[HANGING]["error_code"] == "E-TIMEOUT"
    for field in ("error", "duration_s", "outcome"):
        assert finished[FAILING][field] not in (None, "") and finished[HANGING][field] not in (None, "")
    assert dag_run.rig.reasons(HANGING) == ["E-TIMEOUT: task_timeout exceeded"]
    assert dag_run.returncode == 1 and json.loads(dag_run.stdout)["escalated"] == sorted([FAILING, HANGING])


def test_the_final_live_snapshot_is_valid_and_every_run_is_terminal(dag_run):
    schema = load_schema("swarm.live.v1")
    live = dag_run.live()
    assert validate(live, schema) == []
    assert all(validate(s, schema) == [] for s in dag_run.snapshots)
    assert live["correlation_id"] == dag_run.rig.corr
    assert {r["task_id"] for r in live["runs"]} == set(DAG)
    assert {r["state"] for r in live["runs"]} <= TERMINAL
    counts = live["swarm"]
    assert (counts["running"], counts["queued"], counts["waiting"]) == (0, 0, 0)
    assert (counts["completed"], counts["failed"], counts["timed_out"]) == (len(DAG) - 2, 1, 1)
    states = {r["task_id"]: r["state"] for r in live["runs"]}
    assert states[FAILING] == "failed" and states[HANGING] == "timed_out"
    # one terminal record per run in the log, nothing left open
    finished = [e["task_id"] for e in dag_run.rig.events("swarm.run.finished")]
    assert sorted(finished) == sorted(DAG)


# --------------------------------------------------------------------------------------------- SIGTERM mid-run (AC6)
def test_sigterm_mid_run_leaves_no_orphan_and_cleans_the_cancel_flag(tmp_path, monkeypatch):
    tasks = [sc._task(f"s{i}", agent, CAPABILITY[agent]) for i, agent in enumerate(("A05", "A06", "A07", "A08", "A09"))]
    cfg = {"*": {"sleep": 60, "child": True}}
    run = Run(tmp_path, tasks, cfg, monkeypatch, SWARM_CANCEL_GRACE_S="1", SWARM_HEARTBEAT_S="0.2")
    try:
        run.start("--max-parallel", "3", "--task-timeout", "100")
        rig = run.rig
        flag = rig.swarm / "cancel" / rig.corr
        seen_flag: list[bool] = []
        sent = threading.Event()

        def tick():
            children = [r for r in rig.fake_log() if r["ev"] == "child"]
            if not sent.is_set() and len(children) == 3:
                sent.set()
                os.kill(run.proc.pid, signal.SIGTERM)
            seen_flag.append(flag.exists())

        run.poll_until_exit(timeout=60, on_tick=tick)
    finally:
        run.kill()
    assert sent.is_set(), "the three sessions never got going"
    assert run.returncode == 128 + signal.SIGTERM
    log = rig.fake_log()
    pids = [r["pid"] for r in log if r["ev"] == "start"] + [r["child"] for r in log if r["ev"] == "child"]
    assert len([r for r in log if r["ev"] == "start"]) == 3 and len(pids) == 6
    time.sleep(0.3)
    assert [p for p in pids if sc._alive(p)] == [], "a session or its tool process outlived the runner"
    assert any(seen_flag), "the cancel flag was never written"
    assert not flag.exists(), "the cancel flag outlived the run"
    # the two tasks that never got a slot and the three that did each have a terminal record; live.json agrees
    finished = {e["task_id"]: e["payload"]["state"] for e in rig.events("swarm.run.finished")}
    assert sorted(finished) == sorted(f"T-{t['id']}" for t in tasks) and set(finished.values()) <= TERMINAL
    live = run.live()
    assert validate(live, load_schema("swarm.live.v1")) == []
    assert {r["state"] for r in live["runs"]} <= TERMINAL
    assert (live["swarm"]["running"], live["swarm"]["queued"], live["swarm"]["waiting"]) == (0, 0, 0)
    assert not any(t["state"] in ("IN_PROGRESS", "CLAIMED") for t in rig.store().list(correlation_id=rig.corr))


# ------------------------------------------------------------------------------- wall clock skew (AC11, runner level)
_SKEW = textwrap.dedent('''
    """Test-only sitecustomize: in the runner process (and only there) time.time() jumps back an hour one second after
    start and forward two hours at two seconds, as a changed system clock would. time.monotonic() is untouched."""
    import os, sys, time
    if os.path.basename(sys.argv[0]) == "swarm_run.py":
        _real, _mono, _t0 = time.time, time.monotonic, time.monotonic()

        def _skewed():
            dt = _mono() - _t0
            return _real() + (0 if dt < 1.0 else -3600.0 if dt < 2.0 else 7200.0)

        time.time = _skewed
''')


def test_elapsed_in_live_json_comes_from_monotonic_differences_not_the_wall_clock(tmp_path, monkeypatch):
    skew_dir = tmp_path / "skew"
    skew_dir.mkdir()
    (skew_dir / "sitecustomize.py").write_text(_SKEW)
    tasks = [sc._task("c1", "A05"), sc._task("c2", "A06", "code.frontend", ["c1"])]
    cfg = {"*": {"sleep": 0.8}}
    run = Run(tmp_path, tasks, cfg, monkeypatch, SWARM_HEARTBEAT_S="0.2")
    try:
        run.start("--max-parallel", "2", "--task-timeout", "30", python_path=str(skew_dir))
        run.poll_until_exit(timeout=60)
    finally:
        run.kill()
    assert run.returncode == 0, run.stdout + run.stderr
    schema = load_schema("swarm.live.v1")
    live = run.live()
    # the skew was in effect inside the runner: its wall stamps are off by about two hours from ours
    assert abs(live["updated_wall"] - time.time()) > 3600, "the wall clock skew did not reach the runner"
    # nothing ever went negative or far above the real duration, in any snapshot (the schema rejects elapsed_s < 0)
    assert run.snapshots and all(validate(s, schema) == [] for s in run.snapshots)
    ceiling = run.wall_seconds + 0.5
    for snap in run.snapshots:
        assert 0 <= snap["swarm"]["elapsed_s"] <= ceiling
        assert all(0 <= r["elapsed_s"] <= ceiling and 0 <= r["state_elapsed_s"] <= ceiling for r in snap["runs"])
    spans = run.rig.spans()
    final = {r["task_id"]: r for r in live["runs"]}
    for tid, (span,) in spans.items():
        real = span[1] - span[0]  # the fake's own (unskewed) clock; the runner adds its spawn and wind-down time
        assert real - 0.1 <= final[tid]["elapsed_s"] <= real + 1.5, (tid, real, final[tid]["elapsed_s"])
    finished = {e["task_id"]: e["payload"]["duration_s"] for e in run.rig.events("swarm.run.finished")}
    assert all(real - 0.1 <= finished[t] <= real + 1.5 for t, (s,) in spans.items() for real in [s[1] - s[0]]), finished
    # heartbeats never run backwards inside one run
    beats: dict[str, list[float]] = {}
    for e in run.rig.events("swarm.run.heartbeat"):
        beats.setdefault(e["task_id"], []).append(e["payload"]["elapsed_s"])
    assert beats and all(seq == sorted(seq) and seq[0] >= 0 for seq in beats.values())
    # the second task started after the first ended on the real clock, and live.json's total is the process's lifetime
    assert 0 < live["swarm"]["elapsed_s"] <= ceiling and live["swarm"]["elapsed_s"] >= spans["T-c2"][0][1] - spans["T-c1"][0][0] - 0.2


# ---------------------------------------------------- live.json sample for the bar contract (tests/test_live_contract)
def test_a_mid_run_snapshot_with_every_row_kind_is_captured_for_the_contract_fixture(dag_run):
    """The run above polled live.json while it ran: pick the snapshot with running, queued and completed rows together
    (the bar's input shape). `SWARM_WRITE_LIVE_FIXTURE=1` rewrites tests/fixtures/live_runner_sample.json from it."""
    rich = [s for s in dag_run.snapshots
            if s["swarm"]["running"] >= 2 and s["swarm"]["queued"] >= 1 and s["swarm"]["completed"] >= 1
            and {"running", "queued", "completed"} <= {r["state"] for r in s["runs"]}]
    assert rich, "no mid-run snapshot with running, queued and completed rows"
    best = max(rich, key=lambda s: (len({r["agent"] for r in s["runs"]}), s["swarm"]["completed"], -s["seq"]))
    assert validate(best, load_schema("swarm.live.v1")) == []
    if os.environ.get("SWARM_WRITE_LIVE_FIXTURE") == "1":
        # the bar contract (omp/test/live_contract.test.ts) needs every row kind and all 30 agents in one snapshot
        kinds = {"running", "queued", "completed", "waiting", "failed"}
        full = [s for s in rich if kinds <= {r["state"] for r in s["runs"]} and len({r["agent"] for r in s["runs"]}) == 30]
        assert full, "no snapshot with every row kind and all 30 agents: run it again"
        pick = max(full, key=lambda s: (s["swarm"]["completed"], -s["seq"]))
        target = ROOT / "tests" / "fixtures" / "live_runner_sample.json"
        target.write_text(json.dumps(pick, indent=2, sort_keys=True) + "\n")
