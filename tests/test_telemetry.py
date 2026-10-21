"""swarm.telemetry: validated run lifecycle, bounded history, atomic live.json, stall episodes, stateless derivation and
the fail-open TaskStore observer. Deterministic: FakeClock everywhere, tmp_path state dirs, no sleeps."""
import importlib
import json
import time
from pathlib import Path

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh imports per test (other suites purge sys.modules) plus an isolated SWARM_DIR."""
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    monkeypatch.delenv("SWARM_STALL_AFTER_S", raising=False)
    monkeypatch.delenv("SWARM_MAX_PARALLEL", raising=False)

    class E:
        tel = importlib.import_module("swarm.telemetry")
        clock = importlib.import_module("swarm.clock")
        errors = importlib.import_module("swarm.errors")
        schema = importlib.import_module("swarm.schema")
        store_mod = importlib.import_module("swarm.taskstore")
        runlog = importlib.import_module("swarm.runlog")
        root = tmp_path
        sdir = tmp_path / ".swarm"

        @classmethod
        def new(cls, **kw):
            fake = kw.pop("fake", None) or cls.clock.FakeClock()
            kw.setdefault("tee", False)
            kw.setdefault("correlation_id", "corr-1")
            return cls.tel.Telemetry(root=tmp_path, clock=fake, **kw), fake

        @classmethod
        def events(cls, type_=None):
            f = cls.sdir / "events.jsonl"
            recs = [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
            return [r for r in recs if type_ is None or r["type"] == type_]

    return E


def _running(env, **kw):
    t, fake = env.new(**kw)
    t.run_queued("T1-be.a1", "a05-backend")
    t.run_started("T1-be.a1", sid="s1", runtime="omp")
    return t, fake


# ---- lifecycle validation -----------------------------------------------------------------------------------------
def test_illegal_event_raises_and_leaves_state_unchanged(env):
    t, _ = env.new()
    t.run_queued("T1-be.a1", "a05-backend")
    n_events = len(env.events())
    with pytest.raises(env.errors.SwarmError) as ei:
        t.transition("T1-be.a1", "finish_ok")
    assert ei.value.code is env.errors.ErrorCode.E_CONTRACT
    assert t.snapshot()["runs"][0]["state"] == "queued"
    assert len(env.events()) == n_events  # no swarm.run.state record for a refused event


def test_unknown_run_and_duplicate_run_are_rejected(env):
    t, _ = env.new()
    with pytest.raises(env.errors.SwarmError):
        t.transition("nope.a1", "cancel")
    t.run_queued("T1.a1", "a05-backend")
    with pytest.raises(env.errors.SwarmError):
        t.run_queued("T1.a1", "a05-backend")


def test_wait_kind_gates_exit_from_waiting(env):
    t, _ = _running(env)
    assert t.transition("T1-be.a1", "await_input", reason="need answer").value == "waiting"
    with pytest.raises(env.errors.SwarmError):  # an input wait is not a dependency wait
        t.transition("T1-be.a1", "deps_satisfied")
    assert t.transition("T1-be.a1", "input_received").value == "running"
    t.run_queued("T2-fe.a1", "a06-frontend", waiting_on_deps=True)
    assert t.transition("T2-fe.a1", "deps_satisfied").value == "queued"


def test_state_events_carry_from_to_event_and_wait_kind(env):
    t, _ = _running(env)
    t.transition("T1-be.a1", "await_input", reason="why")
    state = [r["payload"] for r in env.events("swarm.run.state")]
    assert [(p["from"], p["to"], p["event"]) for p in state] == [
        ("queued", "starting", "slot_acquired"), ("starting", "running", "process_started"),
        ("running", "waiting", "await_input")]
    assert state[-1]["wait_kind"] == "input" and state[-1]["reason"] == "why"


def test_all_ten_event_types_are_emitted_through_runlog(env):
    t, fake = _running(env, stall_after_s=1)
    t.activity("T1-be.a1", tool="edit", text="x")
    t.heartbeat()
    t.scheduler_tick(round=1, ready=0, running=1, queued=0, waiting=0, free_slots=3, max_parallel=4, effective_parallel=4)
    t.degraded("slow disk", mode="fallback")
    t.handover("T1-be.a1", to_agent="a08-qa", handover_id="h1", digest="sha256:" + "a" * 64, nbytes=10)
    fake.advance(5)
    t.stalled_runs()
    t.finish("T1-be.a1", "ok")
    assert {r["type"] for r in env.events()} == set(env.tel.EVENT_TYPES)
    assert {r["source"] for r in env.events()} == {"telemetry"}
    assert {r["correlation_id"] for r in env.events()} == {"corr-1"}


# ---- elapsed ------------------------------------------------------------------------------------------------------
def test_elapsed_is_monotonic_and_ignores_wall_clock_jumps(env):
    t, fake = _running(env)
    fake.advance(10)
    fake.jump_wall(-3600)  # NTP step backwards
    fake.advance(5)
    fake.jump_wall(+7200)
    row = t.snapshot()["runs"][0]
    assert row["elapsed_s"] == pytest.approx(15.0)
    assert row["state_elapsed_s"] == pytest.approx(15.0)
    assert t.heartbeat()[0]["elapsed_s"] == pytest.approx(15.0)
    t.finish("T1-be.a1", "ok")
    fake.advance(100)
    fin = env.events("swarm.run.finished")[0]["payload"]
    assert fin["duration_s"] == pytest.approx(15.0)
    assert t.snapshot()["runs"][0]["elapsed_s"] == pytest.approx(15.0)  # settled runs stop counting


def test_state_elapsed_resets_on_each_transition(env):
    t, fake = _running(env)
    fake.advance(20)
    t.transition("T1-be.a1", "await_input")
    fake.advance(7)
    row = t.snapshot()["runs"][0]
    assert row["elapsed_s"] == pytest.approx(27.0) and row["state_elapsed_s"] == pytest.approx(7.0)


# ---- stall ----------------------------------------------------------------------------------------------------------
def test_stall_flagged_once_per_episode_and_cleared_by_activity(env):
    t, fake = _running(env, stall_after_s=30)
    fake.advance(30)
    assert t.stalled_runs() == []  # threshold is strictly greater
    fake.advance(1)
    assert t.stalled_runs() == ["T1-be.a1"]
    assert t.stalled_runs() == ["T1-be.a1"]
    fake.advance(60)
    t.heartbeat()
    assert len(env.events("swarm.run.stalled")) == 1
    snap = t.snapshot()
    assert snap["swarm"]["stalled"] == 1 and snap["runs"][0]["stalled"] is True
    t.activity("T1-be.a1", tool="read")
    assert t.stalled_runs() == [] and t.snapshot()["runs"][0]["stalled"] is False
    fake.advance(31)
    assert t.stalled_runs() == ["T1-be.a1"]
    assert len(env.events("swarm.run.stalled")) == 2  # a new episode
    p = env.events("swarm.run.stalled")[-1]["payload"]
    assert p["threshold_s"] == 30 and p["since_activity_s"] == pytest.approx(31.0)


def test_only_running_runs_stall(env):
    t, fake = env.new(stall_after_s=5)
    t.run_queued("T1.a1", "a05-backend")
    fake.advance(100)
    assert t.stalled_runs() == []
    t.run_started("T1.a1")
    t.transition("T1.a1", "await_input")
    fake.advance(100)
    assert t.stalled_runs() == []


def test_stall_threshold_comes_from_env(env, monkeypatch):
    monkeypatch.setenv("SWARM_STALL_AFTER_S", "7")
    t, fake = _running(env, stall_after_s=None)
    fake.advance(8)
    assert t.stalled_runs() == ["T1-be.a1"]
    monkeypatch.setenv("SWARM_STALL_AFTER_S", "soon")
    with pytest.raises(env.errors.SwarmError):
        env.new()


# ---- heartbeat / activity / history ------------------------------------------------------------------------------------
def test_heartbeat_payload_fields(env):
    t, fake = _running(env)
    fake.advance(4)
    t.activity("T1-be.a1", tool="edit", text="edit swarm/x.py", tokens=900, turns=2)
    fake.advance(2.5)
    [hb] = t.heartbeat("T1-be.a1")
    assert hb == {"run_id": "T1-be.a1", "agent": "a05-backend", "state": "running", "elapsed_s": 6.5,
                  "state_elapsed_s": 6.5, "since_activity_s": 2.5, "stalled": False, "tool_calls": 1,
                  "last_tool": "edit", "activity": "edit swarm/x.py"}
    assert env.events("swarm.run.heartbeat")[-1]["payload"] == hb


def test_heartbeat_all_skips_settled_runs(env):
    t, _ = _running(env)
    t.run_queued("T2.a1", "a08-qa")
    t.finish("T1-be.a1", "error", error_code="E-TIMEOUT", error="boom")
    assert [p["run_id"] for p in t.heartbeat()] == ["T2.a1"]


def test_activity_event_is_throttled_to_one_per_second(env):
    t, fake = _running(env)
    for _ in range(5):
        t.activity("T1-be.a1", tool="read")
        fake.advance(0.1)
    assert len(env.events("swarm.run.activity")) == 1
    assert t.snapshot()["runs"][0]["tool_calls"] == 5  # state still counts every call
    fake.advance(1)
    t.activity("T1-be.a1", tool="edit")
    acts = env.events("swarm.run.activity")
    assert len(acts) == 2 and acts[-1]["payload"]["tool_calls"] == 6


def test_activity_after_finish_is_ignored(env):
    t, _ = _running(env)
    t.finish("T1-be.a1", "ok")
    n = len(env.events())
    t.activity("T1-be.a1", tool="late")
    assert len(env.events()) == n and t.snapshot()["runs"][0]["last_tool"] is None


def test_ring_buffer_is_bounded_and_keeps_the_newest(env):
    t, fake = env.new(history_max=5)
    for i in range(12):
        t.run_queued(f"T{i}.a1", "a05-backend")
    recent = t.recent_events()
    assert len(recent) == 5
    assert [r["payload"]["run_id"] for r in recent] == [f"T{i}.a1" for i in range(7, 12)]
    assert len(env.events()) == 12  # the log keeps everything; only the in-memory ring is bounded
    with pytest.raises(env.errors.SwarmError):
        env.new(history_max=0)


def test_subscribers_see_records_and_never_break_emit(env):
    t, _ = env.new()
    seen = []

    def boom(rec):
        raise RuntimeError("subscriber bug")

    t.subscribe(boom)
    unsub = t.subscribe(seen.append)
    t.run_queued("T1.a1", "a05-backend")
    assert [r["type"] for r in seen] == ["swarm.run.queued"]
    unsub()
    t.run_queued("T2.a1", "a05-backend")
    assert len(seen) == 1 and len(env.events("swarm.run.queued")) == 2


# ---- finish -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("outcome,state", [("ok", "completed"), ("error", "failed"), ("cancelled", "cancelled"),
                                           ("timeout", "timed_out")])
def test_finish_outcomes_map_to_terminal_states(env, outcome, state):
    t, _ = _running(env)
    assert t.finish("T1-be.a1", outcome, error_code="E-X", error="why").value == state
    fin = env.events("swarm.run.finished")[0]["payload"]
    assert fin["state"] == state and fin["error_code"] == "E-X"


def test_finish_from_handing_off_and_idempotent_finished_event(env):
    t, _ = _running(env)
    t.transition("T1-be.a1", "begin_handoff")
    assert t.finish("T1-be.a1", "ok").value == "completed"
    assert t.finish("T1-be.a1", "ok").value == "completed"
    assert len(env.events("swarm.run.finished")) == 1
    with pytest.raises(env.errors.SwarmError):
        t.finish("T1-be.a1", "error")  # terminal states have no exits
    with pytest.raises(env.errors.SwarmError):
        t.finish("T1-be.a1", "exploded")


def test_cancel_through_transition_then_finish_emits_finished_once(env):
    t, _ = _running(env)
    t.transition("T1-be.a1", "cancel", reason="user")
    t.finish("T1-be.a1", "cancelled")
    assert len(env.events("swarm.run.finished")) == 1


# ---- live.json ---------------------------------------------------------------------------------------------------------------
def _assert_valid(env, doc):
    assert env.schema.validate(doc, env.schema.load_schema("swarm.live.v1")) == []
    assert doc["schema"] == "swarm.live.v1"


def test_live_json_is_valid_atomic_and_counts_states(env):
    t, fake = _running(env)
    t.run_queued("T2-qa.a1", "a08-qa")
    t.run_queued("T3.a1", "a02-requirements")
    t.run_started("T3.a1")
    t.finish("T3.a1", "ok")
    t.run_queued("T4.a1", "a03-architect")
    t.run_started("T4.a1")
    t.finish("T4.a1", "error", error="exploded")
    t.flush_live(force=True)
    doc = json.loads((env.sdir / "live.json").read_text())
    _assert_valid(env, doc)
    s = doc["swarm"]
    assert (s["running"], s["queued"], s["completed"], s["failed"], s["state"]) == (1, 1, 1, 1, "running")
    assert [r["state"] for r in doc["runs"]][0] == "failed"  # problems first
    assert doc["runs"][0]["error"] == "exploded"
    assert doc["updated_wall"] == fake.wall() and doc["correlation_id"] == "corr-1"
    assert [p.name for p in env.sdir.iterdir() if p.name.endswith(".tmp")] == []


def test_a_swarm_with_cancelled_runs_and_no_failure_is_cancelled_not_completed(env):
    """The audit repro: a cancelled swarm's final live.json said `completed` and the bar painted a green check."""
    t, _ = env.new()
    for run_id, agent in (("T1.a1", "a05-backend"), ("T2.a1", "a06-frontend"), ("T3.a1", "a08-qa")):
        t.run_queued(run_id, agent)
        t.run_started(run_id)
    t.finish("T1.a1", "ok")
    t.finish("T2.a1", "cancelled")
    t.finish("T3.a1", "cancelled")
    t.flush_live(force=True)
    doc = json.loads((env.sdir / "live.json").read_text())
    _assert_valid(env, doc)
    assert doc["swarm"]["state"] == "cancelled" and (doc["swarm"]["completed"], doc["swarm"]["cancelled"]) == (1, 2)
    t.run_queued("T4.a1", "a09-reviewer")
    t.run_started("T4.a1")
    t.finish("T4.a1", "error", error="boom")
    t.flush_live(force=True)
    assert json.loads((env.sdir / "live.json").read_text())["swarm"]["state"] == "failed"  # a failure outranks a cancel


def test_live_json_runs_are_capped_at_64_with_problems_first(env):
    t, _ = env.new()
    for i in range(70):
        t.run_queued(f"T{i}.a1", "a05-backend")
    t.run_started("T69.a1")
    t.finish("T69.a1", "error", error="x")
    snap = t.snapshot()
    assert len(snap["runs"]) == 64 and snap["runs"][0]["run_id"] == "T69.a1"
    assert snap["swarm"]["queued"] == 69  # counts cover every run, not just the listed ones


def test_flush_throttle_skips_second_write_within_a_second_unless_forced(env):
    t, fake = _running(env)
    assert t.flush_live(force=True) is True
    seq1 = json.loads((env.sdir / "live.json").read_text())["seq"]
    fake.advance(0.5)
    assert t.flush_live() is False
    assert json.loads((env.sdir / "live.json").read_text())["seq"] == seq1
    assert t.flush_live(force=True) is True
    fake.advance(1.0)
    assert t.flush_live() is True
    assert json.loads((env.sdir / "live.json").read_text())["seq"] == seq1 + 2


def test_flush_never_leaves_a_partial_file(env, monkeypatch):
    t, fake = _running(env)
    t.flush_live(force=True)
    good = (env.sdir / "live.json").read_text()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(env.tel.os, "replace", boom)
    fake.advance(2)
    assert t.flush_live(force=True) is False  # reported, not raised
    assert (env.sdir / "live.json").read_text() == good  # the previous complete file is untouched
    assert [p.name for p in env.sdir.iterdir() if p.name.endswith(".tmp")] == []


def test_live_disabled_writes_nothing(env):
    t, _ = _running(env, live=False)
    assert t.flush_live(force=True) is False
    assert not (env.sdir / "live.json").exists()


def test_read_live_returns_none_for_missing_corrupt_or_offschema(env):
    assert env.tel.read_live(env.root) is None
    env.sdir.mkdir(parents=True)
    f = env.sdir / "live.json"
    f.write_text("{not json")
    assert env.tel.read_live(env.root) is None
    f.write_text("[]")
    assert env.tel.read_live(env.root) is None
    f.write_text(json.dumps({"schema": "swarm.live.v0", "runs": []}))
    assert env.tel.read_live(env.root) is None
    f.write_bytes(b"\xff\xfe\x00")
    assert env.tel.read_live(env.root) is None
    t, _ = _running(env)
    t.flush_live(force=True)
    doc = env.tel.read_live(env.root)
    assert doc is not None and doc["runs"][0]["run_id"] == "T1-be.a1"


def test_max_parallel_comes_from_tick_then_env(env, monkeypatch):
    monkeypatch.setenv("SWARM_MAX_PARALLEL", "6")
    t, _ = _running(env)
    assert t.snapshot()["swarm"]["max_parallel"] == 6
    t.scheduler_tick(round=1, ready=2, running=1, queued=2, waiting=0, free_slots=1, max_parallel=8, effective_parallel=3)
    s = t.snapshot()["swarm"]
    assert (s["max_parallel"], s["effective_parallel"]) == (8, 3)


def test_degraded_marker_is_in_the_snapshot(env):
    t, _ = _running(env)
    t.degraded("substrate unreachable", mode="local-only")
    assert t.snapshot()["swarm"]["degraded"] == {"reason": "substrate unreachable", "mode": "local-only"}


# ---- redaction ---------------------------------------------------------------------------------------------------------------------
def test_secrets_in_activity_text_are_redacted_in_live_json_and_events(env):
    secret = "ghp_" + "A1b2C3d4E5" * 3
    t, fake = _running(env)
    t.activity("T1-be.a1", tool="bash", text=f"curl -H 'Authorization: Bearer {'z' * 24}' && export API_KEY={secret}")
    fake.advance(2)
    t.activity("T1-be.a1", tool="bash", text=f"push with {secret}")
    t.flush_live(force=True)
    live = (env.sdir / "live.json").read_text()
    log = (env.sdir / "events.jsonl").read_text()
    for blob in (live, log):
        assert secret not in blob and "z" * 24 not in blob
    assert env.tel.clip(f"token {secret}") == "token [REDACTED]"
    assert len(env.tel.clip("x" * 500)) == 120


# ---- stateless derivation ----------------------------------------------------------------------------------------------------------
def _store(env):
    return env.store_mod.TaskStore(root=env.root)


def _task(store, tid, state_path, **kw):
    store.create(task_id=tid, correlation_id="c1", capability="code.backend", agent_id=kw.pop("agent_id", "a05-backend"), **kw)
    for st in state_path:
        store.transition(tid, st, reason=f"to {st}")


def _wallclock(env, offset=0.0):
    return env.clock.FakeClock(start_wall=time.time() + offset)


def test_derive_snapshot_maps_task_states_to_run_states(env):
    store = _store(env)
    _task(store, "T-run", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"])
    _task(store, "T-claim", ["VALIDATED", "PLANNED", "CLAIMED"])
    _task(store, "T-queue", ["VALIDATED", "PLANNED"])
    _task(store, "T-wait", ["VALIDATED", "PLANNED"], depends_on=["T-run"])
    _task(store, "T-blocked", ["VALIDATED", "BLOCKED"])
    _task(store, "T-done", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "FAILED"])
    _task(store, "T-cancel", ["CANCELLED"])
    _task(store, "T-review", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"])
    snap = env.tel.derive_snapshot(env.root, clock=_wallclock(env, 5))
    _assert_valid(env, snap)
    by = {r["task_id"]: r for r in snap["runs"]}
    assert {k: v["state"] for k, v in by.items()} == {
        "T-run": "running", "T-claim": "starting", "T-queue": "queued", "T-wait": "waiting", "T-blocked": "waiting",
        "T-done": "failed", "T-cancel": "cancelled", "T-review": "completed"}
    assert by["T-run"]["run_id"] == "T-run.a1" and by["T-run"]["agent"] == "a05-backend"
    assert by["T-run"]["elapsed_s"] >= 5 and by["T-done"]["error"] == "to FAILED"
    s = snap["swarm"]
    assert (s["running"], s["queued"], s["waiting"], s["failed"], s["cancelled"], s["completed"]) == (1, 2, 2, 1, 1, 1)
    assert s["state"] == "running" and snap["correlation_id"] == "c1"
    assert snap["runs"][0]["state"] == "failed"


def test_derive_snapshot_is_empty_and_idle_without_a_store(env):
    snap = env.tel.derive_snapshot(env.root)
    _assert_valid(env, snap)
    assert snap["runs"] == [] and snap["swarm"]["state"] == "idle"
    assert not (env.sdir / "tasks.db").exists()  # reading never creates the store


def test_derive_uses_events_tail_for_activity_and_stall(env):
    store = _store(env)
    _task(store, "T-run", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"])
    env.runlog.emit("swarm.run.activity", {"run_id": "T-run.a1", "agent": "a05-backend", "tool": "edit",
                                           "text": "edit swarm/x.py", "tool_calls": 12, "turns": 3, "tokens": 5},
                    source="telemetry", correlation_id="c1", task_id="T-run", root=env.root, tee=False)
    env.runlog.emit("swarm.run.activity", {"run_id": "OTHER.a1", "text": "not mine", "tool_calls": 99},
                    source="telemetry", correlation_id="c1", task_id="OTHER", root=env.root, tee=False)
    fresh = env.tel.derive_snapshot(env.root, clock=_wallclock(env, 2))
    r = fresh["runs"][0]
    assert (r["activity"], r["tool_calls"], r["last_tool"], r["stalled"]) == ("edit swarm/x.py", 12, "edit", False)
    assert 0 <= r["since_activity_s"] < 10
    silent = env.tel.derive_snapshot(env.root, clock=_wallclock(env, 600), stall_after_s=180)
    assert silent["runs"][0]["stalled"] is True and silent["swarm"]["stalled"] == 1


def test_derive_heartbeat_extends_the_activity_window(env):
    store = _store(env)
    _task(store, "T-run", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"])
    env.runlog.emit("swarm.run.heartbeat", {"run_id": "T-run.a1", "state": "running", "since_activity_s": 1.0,
                                            "tool_calls": 4, "last_tool": "read", "activity": "reading"},
                    source="telemetry", correlation_id="c1", task_id="T-run", root=env.root, tee=False)
    r = env.tel.derive_snapshot(env.root, clock=_wallclock(env, 3))["runs"][0]
    assert (r["activity"], r["tool_calls"], r["last_tool"]) == ("reading", 4, "read")


def test_derive_handles_a_torn_events_tail_and_timed_out_overrides(env):
    store = _store(env)
    _task(store, "T-to", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "FAILED"])
    env.runlog.emit("swarm.run.finished", {"run_id": "T-to.a1", "state": "timed_out", "outcome": "timed_out"},
                    source="telemetry", correlation_id="c1", task_id="T-to", root=env.root, tee=False)
    with (env.sdir / "events.jsonl").open("a") as fh:
        fh.write('{"type": "swarm.run.activity", "payload": {"run_')  # half-written record
    snap = env.tel.derive_snapshot(env.root)
    assert snap["runs"][0]["state"] == "timed_out" and snap["swarm"]["timed_out"] == 1


def test_derive_scopes_to_latest_correlation(env):
    store = _store(env)
    store.create(task_id="T-old", correlation_id="c-old", capability="x")
    store.create(task_id="T-new", correlation_id="c-new", capability="x")
    (env.sdir / "latest_correlation").write_text("c-old")
    assert [r["task_id"] for r in env.tel.derive_snapshot(env.root)["runs"]] == ["T-old"]
    assert [r["task_id"] for r in env.tel.derive_snapshot(env.root, correlation_id="c-new")["runs"]] == ["T-new"]


@pytest.mark.parametrize("task_id", ["sk-onboarding-flow-v2", "github_pat_" + "ABCDEFGHIJKLMNOPQRSTUV", "T-plain-0001"])
def test_derive_keeps_activity_handover_and_timeout_for_token_shaped_task_ids(env, task_id):
    """RV-J P2-1: events.jsonl is redacted, but run ids are join keys: a task id shaped like a token must not lose its state."""
    store = _store(env)
    _task(store, task_id, ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "FAILED"])
    t, _ = env.new(correlation_id="c1")
    run_id = f"{task_id}.a1"
    t.run_queued(run_id, "a05-backend", task_id=task_id)
    t.run_started(run_id, sid="s1", runtime="omp")
    t.activity(run_id, tool="bash", text="compiling module")
    t.handover(run_id, to_agent="a09-reviewer", handover_id=f"H-{task_id}", digest=None, nbytes=10)
    env.runlog.emit("swarm.run.finished", {"run_id": run_id, "state": "timed_out", "outcome": "timed_out"},
                    source="telemetry", correlation_id="c1", task_id=task_id, root=env.root, tee=False)
    row = env.tel.derive_snapshot(env.root)["runs"][0]
    assert (row["run_id"], row["state"]) == (run_id, "timed_out")
    assert (row["activity"], row["last_tool"], row["tool_calls"]) == ("compiling module", "bash", 1)
    assert row["handover_out"] == f"H-{task_id}"[:64]
    assert {e["payload"]["run_id"] for e in env.events("swarm.run.activity")} == {run_id}


def test_refresh_live_writes_valid_json_with_increasing_seq(env):
    store = _store(env)
    _task(store, "T-run", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"])  # every transition already refreshed
    doc = env.tel.read_live(env.root)
    assert doc is not None and doc["runs"][0]["state"] == "running"
    seq = doc["seq"]
    assert env.tel.refresh_live(env.root) is True
    assert env.tel.read_live(env.root)["seq"] == seq + 1


def test_refresh_live_flushes_an_owning_telemetry_instead_of_clobbering_it(env):
    t, fake = _running(env)
    t.activity("T1-be.a1", tool="edit", text="in-memory only")
    fake.advance(2)
    assert env.tel.refresh_live(env.root) is True  # no tasks.db here: a derived snapshot would have no runs
    doc = env.tel.read_live(env.root)
    assert doc["runs"][0]["activity"] == "in-memory only"


def test_refresh_live_is_fail_open(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("derive broke")

    monkeypatch.setattr(env.tel, "derive_snapshot", boom)
    assert env.tel.refresh_live(env.root) is False


# ---- TaskStore observer -------------------------------------------------------------------------------------------------------------------
def test_taskstore_observer_is_fail_open(env, monkeypatch):
    store = _store(env)
    store.create(task_id="T-x", correlation_id="c1", capability="x")

    def boom(*a, **k):
        raise RuntimeError("observer broke")

    monkeypatch.setattr(env.tel, "refresh_live", boom)
    assert store.transition("T-x", "VALIDATED")["state"] == "VALIDATED"
    assert store.get("T-x")["state"] == "VALIDATED"
    assert [h["to_state"] for h in store.history("T-x")] == ["CREATED", "VALIDATED"]


def test_taskstore_observer_runs_after_commit_not_inside_the_transaction(env, monkeypatch):
    store = _store(env)
    store.create(task_id="T-x", correlation_id="c1", capability="x")
    calls = []

    def spy(*a, **k):
        # a second connection sees the committed state, and the writer's lock is already released
        other = env.store_mod.TaskStore(store.path)
        calls.append((other.get("T-x")["state"], store._tx_depth, store.conn.in_transaction))

    monkeypatch.setattr(env.tel, "refresh_live", spy)
    store.transition("T-x", "VALIDATED")
    assert calls == [("VALIDATED", 0, False)]
    with store.transaction():
        store.transition("T-x", "PLANNED")
        assert len(calls) == 1  # nested in an uncommitted transaction: no refresh yet


def test_illegal_taskstore_transition_does_not_notify(env, monkeypatch):
    store = _store(env)
    store.create(task_id="T-x", correlation_id="c1", capability="x")
    calls = []
    monkeypatch.setattr(env.tel, "refresh_live", lambda *a, **k: calls.append(1))
    with pytest.raises(env.errors.SwarmError):
        store.transition("T-x", "DONE")
    assert calls == []


def test_taskstore_import_does_not_import_telemetry():
    import subprocess
    import sys
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run([sys.executable, "-c", "import sys, swarm.taskstore; print('swarm.telemetry' in sys.modules)"],
                         cwd=root, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_high_frequency_events_never_reach_the_substrate_tee(env, monkeypatch):
    seen = {}
    real = env.runlog.emit

    def spy(event_type, payload, **kw):
        seen[event_type] = kw["tee"]
        return real(event_type, payload, **kw)

    monkeypatch.setattr(env.runlog, "emit", spy)
    t, fake = env.new(tee=True)
    t.run_queued("T1.a1", "a05-backend")
    t.run_started("T1.a1")
    t.activity("T1.a1", tool="edit")
    t.heartbeat()
    assert seen["swarm.run.activity"] is False and seen["swarm.run.heartbeat"] is False
    assert seen["swarm.run.queued"] is True and seen["swarm.run.state"] is True


# ==== W1 review R1 fix-ups ==================================================================================================================
# ---- P1-2: transitions inside an enclosing transaction() refresh live.json after the OUTERMOST commit ----------------------------------
def test_transition_inside_a_transaction_refreshes_live_json_after_the_outermost_commit(env):
    store = _store(env)
    _task(store, "T1", ["VALIDATED", "PLANNED", "CLAIMED"])
    before = env.tel.read_live(env.root)
    assert before["runs"][0]["state"] == "starting" and before["seq"] == 3
    with store.transaction():
        store.transition("T1", "IN_PROGRESS")
        assert env.tel.read_live(env.root)["seq"] == 3  # not yet: nothing is committed
    after = env.tel.read_live(env.root)
    assert after["seq"] == 4 and after["runs"][0]["state"] == "running"  # the reviewer's repro: it stayed at seq 3 / starting


def test_nested_transactions_refresh_once_after_the_outermost_commit_and_never_inside_the_lock(env, monkeypatch):
    store = _store(env)
    _task(store, "T1", ["VALIDATED", "PLANNED"])
    calls = []

    def spy(*a, **k):
        other = env.store_mod.TaskStore(store.path)
        calls.append((other.get("T1")["state"], store._tx_depth, store.conn.in_transaction))

    monkeypatch.setattr(env.tel, "refresh_live", spy)
    with store.transaction():
        with store.transaction():
            store.transition("T1", "CLAIMED")
            store.transition("T1", "IN_PROGRESS")
        assert calls == []
    assert calls == [("IN_PROGRESS", 0, False)]  # one refresh, after the commit, lock released


def test_rolled_back_transaction_never_refreshes_and_clears_the_dirty_flag(env, monkeypatch):
    store = _store(env)
    _task(store, "T1", ["VALIDATED", "PLANNED"])
    calls = []
    monkeypatch.setattr(env.tel, "refresh_live", lambda *a, **k: calls.append(1))
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.transition("T1", "CLAIMED")
            raise RuntimeError("boom")
    assert calls == [] and store.get("T1")["state"] == "PLANNED"
    with store.transaction():  # no transition inside: the stale flag must not fire a refresh
        store.get("T1")
    assert calls == []


def test_read_only_transaction_does_not_refresh(env, monkeypatch):
    store = _store(env)
    store.create(task_id="T1", correlation_id="c1", capability="x")
    calls = []
    monkeypatch.setattr(env.tel, "refresh_live", lambda *a, **k: calls.append(1))
    with store.transaction():
        store.get("T1")
    assert calls == []


# ---- P2-5: live.json ownership --------------------------------------------------------------------------------------------------------
def _scheduler_doc(env, *, pid, updated_wall, seq=7):
    doc = env.tel.build_snapshot([], correlation_id="c1", seq=seq, updated_wall=updated_wall, elapsed_s=0.0,
                                 max_parallel=4, effective_parallel=4, degraded=None)
    doc.update(producer="scheduler", pid=pid, producer_updated=updated_wall)
    return doc


def _put_live(env, doc):
    env.sdir.mkdir(parents=True, exist_ok=True)
    (env.sdir / "live.json").write_text(json.dumps(doc))
    return (env.sdir / "live.json").read_text()


def _dead_pid():
    import subprocess
    import sys
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_scheduler_flush_stamps_producer_pid_and_producer_updated_and_stays_schema_valid(env):
    import os
    t, fake = _running(env)
    t.flush_live(force=True)
    doc = json.loads((env.sdir / "live.json").read_text())
    _assert_valid(env, doc)
    assert doc["producer"] == "scheduler" and doc["pid"] == os.getpid() and doc["producer_updated"] == doc["updated_wall"] == fake.wall()


def test_derived_writer_skips_while_a_live_foreign_scheduler_document_is_fresh(env):
    import os
    store = _store(env)
    _task(store, "T1", ["VALIDATED"])
    text = _put_live(env, _scheduler_doc(env, pid=os.getppid(), updated_wall=time.time()))  # the parent process is alive
    assert env.tel.refresh_live(env.root) is False
    assert (env.sdir / "live.json").read_text() == text  # untouched: the scheduler's richer, monotonic document wins
    store.transition("T1", "PLANNED")  # a derived writer committing a transition must not clobber it either
    assert (env.sdir / "live.json").read_text() == text


@pytest.mark.parametrize("case", ["stale", "dead_pid", "no_producer_fields", "same_pid_no_owner"])
def test_derived_writer_takes_over_when_the_producer_document_is_not_fresh(env, case):
    import os
    store = _store(env)
    _task(store, "T1", ["VALIDATED"])
    now = time.time()
    doc = {"stale": lambda: _scheduler_doc(env, pid=os.getppid(), updated_wall=now - 3 * env.tel.heartbeat_default() - 5),
           "dead_pid": lambda: _scheduler_doc(env, pid=_dead_pid(), updated_wall=now),
           "no_producer_fields": lambda: {k: v for k, v in _scheduler_doc(env, pid=1, updated_wall=now).items()
                                          if k not in ("producer", "pid", "producer_updated")},
           "same_pid_no_owner": lambda: _scheduler_doc(env, pid=os.getpid(), updated_wall=now)}[case]()
    _put_live(env, doc)
    assert env.tel.refresh_live(env.root) is True
    out = json.loads((env.sdir / "live.json").read_text())
    _assert_valid(env, out)
    assert out["producer"] == "derived" and out["pid"] == os.getpid() and out["seq"] == 8
    assert out["runs"][0]["task_id"] == "T1"


def test_freshness_window_is_three_heartbeats(env, monkeypatch):
    import os
    monkeypatch.setenv("SWARM_HEARTBEAT_S", "2")
    _put_live(env, _scheduler_doc(env, pid=os.getppid(), updated_wall=time.time() - 5))  # inside 3 * 2 s
    assert env.tel.refresh_live(env.root) is False
    _put_live(env, _scheduler_doc(env, pid=os.getppid(), updated_wall=time.time() - 7))  # outside it
    assert env.tel.refresh_live(env.root) is True


def test_derived_read_derive_write_cycle_runs_under_an_flock(env, monkeypatch):
    import fcntl
    store = _store(env)
    _task(store, "T1", ["VALIDATED"])
    seen = {}
    real = env.tel.derive_snapshot

    def probe(*a, **k):
        fd = open(env.sdir / "live.json.lock", "a")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["locked"] = False
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            seen["locked"] = True
        finally:
            fd.close()
        return real(*a, **k)

    monkeypatch.setattr(env.tel, "derive_snapshot", probe)
    assert env.tel.refresh_live(env.root) is True
    assert seen == {"locked": True}


def test_a_stale_derivation_cannot_overwrite_a_newer_one(env, monkeypatch):
    """Process A derives (slowly) while B commits the final state: B's refresh waits for A's lock and then derives the
    newer view, so the file ends up reflecting the DB rather than A's older snapshot."""
    import threading
    store_a = _store(env)
    _task(store_a, "T1", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"])
    real = env.tel.derive_snapshot
    entered, release = threading.Event(), threading.Event()
    first = {"n": 0}

    def slow(*a, **k):
        first["n"] += 1
        doc = real(*a, **k)
        if first["n"] == 1:
            entered.set()
            assert release.wait(5)
        return doc

    monkeypatch.setattr(env.tel, "derive_snapshot", slow)
    t = threading.Thread(target=lambda: env.tel.refresh_live(env.root))
    t.start()
    assert entered.wait(5)
    done = threading.Event()

    def commit_done():
        _store(env).transition("T1", "FAILED")  # (own connection, own thread) commits, then its own refresh queues behind A's lock
        done.set()

    tb = threading.Thread(target=commit_done)
    tb.start()
    time.sleep(0.2)
    release.set()
    t.join(5)
    tb.join(5)
    assert done.is_set()
    live = env.tel.read_live(env.root)
    assert live["runs"][0]["state"] == "failed"


# ---- P3-7: a relative db path ---------------------------------------------------------------------------------------------------------
def test_relative_db_path_derives_and_refreshes(env, monkeypatch):
    monkeypatch.chdir(env.root)
    store = env.store_mod.TaskStore("rel-tasks.db")
    store.create(task_id="T1", correlation_id="c1", capability="x", agent_id="a05-backend")
    store.transition("T1", "VALIDATED")  # observer: refresh_live(db_path=<relative>) used to die in as_uri()
    live = env.root / "live.json"
    assert live.exists() and json.loads(live.read_text())["runs"][0]["task_id"] == "T1"
    assert env.tel.derive_snapshot(db_path="rel-tasks.db")["runs"][0]["task_id"] == "T1"


# ---- P3-9: cost cuts keep the results identical -----------------------------------------------------------------------------------------
def test_parse_ts_matches_strptime_and_rejects_garbage(env):
    import calendar
    for ts in ("2026-10-19T02:37:00Z", "1999-12-31T23:59:59Z", "2024-02-29T00:00:00Z"):
        assert env.tel._parse_ts(ts) == float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
    for bad in ("2026-13-01T00:00:00Z", "2026-02-30T00:00:00Z", "2026-10-19 02:37:00Z", "2026-10-19T02:37:00", "", None, 5, {"a": 1}, ["x"]):
        assert env.tel._parse_ts(bad) is None


def test_unchanged_live_json_is_not_revalidated_on_the_next_refresh(env, monkeypatch):
    store = _store(env)
    _task(store, "T1", ["VALIDATED"])
    calls = []
    real = env.tel.validate
    monkeypatch.setattr(env.tel, "validate", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert env.tel.refresh_live(env.root) is True  # reads the file it wrote itself last time
    assert env.tel.refresh_live(env.root) is True
    assert calls == []
    (env.sdir / "live.json").write_text("{not json")  # an externally changed file is validated again (and rejected)
    assert env.tel.read_live(env.root) is None


def _emit_activity(env, run_id, text, **kw):
    env.runlog.emit("swarm.run.activity", {"run_id": run_id, "text": text, "tool_calls": 1}, source="telemetry",
                    correlation_id="c1", root=env.root, tee=False, **kw)


def test_events_tail_is_parsed_incrementally_and_matches_a_cold_read(env):
    path = env.sdir / "events.jsonl"
    _emit_activity(env, "T1.a1", "one")
    first = env.tel._read_tail(path)
    assert env.tel._read_tail(path) is first  # unchanged file: the very same parsed list, no re-parse
    _emit_activity(env, "T1.a1", "two")
    second = env.tel._read_tail(path)
    assert [r["payload"]["text"] for r in second] == ["one", "two"] and [r["payload"]["text"] for r in first] == ["one"]
    env.tel._TAIL_CACHE.clear()
    assert env.tel._read_tail(path) == second


def test_events_tail_waits_for_a_torn_last_line_and_takes_a_complete_unterminated_one(env):
    path = env.sdir / "events.jsonl"
    _emit_activity(env, "T1.a1", "one")
    rec = json.dumps({"type": "swarm.run.activity", "ts": "2026-10-19T00:00:00Z", "correlation_id": "c1",
                      "payload": {"run_id": "T1.a1", "text": "two"}})
    with path.open("a") as fh:
        fh.write(rec[:25])  # torn mid-record
    assert [r["payload"]["text"] for r in env.tel._read_tail(path)] == ["one"]
    with path.open("a") as fh:
        fh.write(rec[25:])  # now a complete record, still without its newline
    assert [r["payload"]["text"] for r in env.tel._read_tail(path)] == ["one", "two"]
    with path.open("a") as fh:
        fh.write("\n")
    _emit_activity(env, "T1.a1", "three")
    assert [r["payload"]["text"] for r in env.tel._read_tail(path)] == ["one", "two", "three"]


def test_events_tail_rereads_after_truncation_or_replacement(env):
    path = env.sdir / "events.jsonl"
    for i in range(5):
        _emit_activity(env, "T1.a1", f"old-{i}")
    assert len(env.tel._read_tail(path)) == 5
    path.write_text("")
    _emit_activity(env, "T1.a1", "new")
    assert [r["payload"]["text"] for r in env.tel._read_tail(path)] == ["new"]
    lines = path.read_text()
    path.unlink()
    path.write_text(lines + lines)
    assert [r["payload"]["text"] for r in env.tel._read_tail(path)] == ["new", "new"]


def test_events_tail_window_is_bounded_for_a_big_file(env):
    path = env.sdir / "events.jsonl"
    for i in range(400):
        _emit_activity(env, "T1.a1", f"event-{i:04d} " + "x" * 200)
    recs = env.tel._read_tail(path, max_bytes=20_000)
    texts = [r["payload"]["text"][:10] for r in recs]
    assert texts[-1] == "event-0399" and 30 < len(recs) < 60 and texts == sorted(texts)  # a window, whole lines only
    for i in range(400, 800):  # appended one by one the cached window grows to at most ~2x, then restarts
        _emit_activity(env, "T1.a1", f"event-{i:04d} " + "x" * 200)
        recs = env.tel._read_tail(path, max_bytes=20_000)
        assert len(recs) < 130 and recs[-1]["payload"]["text"].startswith(f"event-{i:04d}")


# ---- w3-fix-2: clear_degraded and handover_in ---------------------------------------------------------------------------------------------
def test_clear_degraded_drops_the_marker_and_emits_a_recovered_event(env):
    """RV-A: a recovered limiter left `degraded` in live.json for the rest of the run."""
    t, _ = _running(env)
    t.degraded("rate_limited", mode="reduced_parallelism", detail="effective_parallel 8 -> 4")
    assert t.snapshot()["swarm"]["degraded"]["mode"] == "reduced_parallelism"
    t.clear_degraded("reduced_parallelism")
    assert t.snapshot()["swarm"]["degraded"] is None
    assert env.tel.read_live(env.root)["swarm"]["degraded"] is None  # flushed, not only in memory
    rec = env.events("swarm.degraded")[-1]["payload"]
    assert rec["mode"] == "recovered" and rec["detail"] == "reduced_parallelism"
    n = len(env.events())
    t.clear_degraded()  # nothing left to clear: no second event
    assert len(env.events()) == n


def test_clear_degraded_with_a_mode_leaves_a_different_fallback_alone(env):
    t, _ = _running(env)
    t.degraded("cancel requested", mode="cancelling")
    t.clear_degraded("reduced_parallelism")
    assert t.snapshot()["swarm"]["degraded"] == {"reason": "cancel requested", "mode": "cancelling"}
    t.clear_degraded()  # no mode: whatever is there
    assert t.snapshot()["swarm"]["degraded"] is None


def test_derive_snapshot_honours_the_recovered_event(env):
    store = _store(env)
    _task(store, "T-run", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"])
    env.runlog.emit("swarm.degraded", {"reason": "rate_limited", "mode": "reduced_parallelism", "detail": None},
                    source="telemetry", correlation_id="c1", root=env.root, tee=False)
    assert env.tel.derive_snapshot(env.root)["swarm"]["degraded"]["mode"] == "reduced_parallelism"
    env.runlog.emit("swarm.degraded", {"reason": "recovered", "mode": "recovered", "detail": "sequential"},
                    source="telemetry", correlation_id="c1", root=env.root, tee=False)
    assert env.tel.derive_snapshot(env.root)["swarm"]["degraded"]["mode"] == "reduced_parallelism"  # another fallback
    env.runlog.emit("swarm.degraded", {"reason": "recovered", "mode": "recovered", "detail": "reduced_parallelism"},
                    source="telemetry", correlation_id="c1", root=env.root, tee=False)
    assert env.tel.derive_snapshot(env.root)["swarm"]["degraded"] is None


def test_derive_snapshot_fills_handover_in_from_a_handover_addressed_to_the_runs_agent(env):
    """RV-A/W3: `handover_in` was always null in the derived snapshot, whatever swarm.handover held."""
    from test_handover import base
    ho = importlib.import_module("swarm.handover")
    store = _store(env)
    _task(store, "T-be", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"], agent_id="a08-qa")  # A08 continues T-be
    _task(store, "T-fe", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"], agent_id="a05-backend")
    _task(store, "T-none", ["VALIDATED"], agent_id="a08-qa")
    ho.write_handover(base(correlation_id="c1", task_id="T-be", to_agent="A08"), root=env.root, emit=False)
    ho.write_handover(base(correlation_id="c1", task_id="T-fe", to_agent="A08"), root=env.root, emit=False)  # not A05's
    rows = {r["task_id"]: r for r in env.tel.derive_snapshot(env.root)["runs"]}
    assert rows["T-be"]["handover_in"] == "T-be.1"
    assert rows["T-fe"]["handover_in"] is None and rows["T-none"]["handover_in"] is None
    _assert_valid(env, env.tel.derive_snapshot(env.root))


def test_derive_snapshot_shows_no_handover_in_for_a_tampered_or_malformed_newest_handover(env):
    from test_handover import base
    ho = importlib.import_module("swarm.handover")
    store = _store(env)
    _task(store, "T-be", ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"], agent_id="a08-qa")
    ho.write_handover(base(correlation_id="c1", task_id="T-be", to_agent="A08"), root=env.root, emit=False)
    path = env.sdir / "handovers" / "c1" / "T-be.1.json"
    doc = json.loads(path.read_text())
    doc["goal"] = "edited after the digest was taken"
    path.write_text(json.dumps(doc))
    assert env.tel.derive_snapshot(env.root)["runs"][0]["handover_in"] is None
    path.write_text("{not json")
    assert env.tel.derive_snapshot(env.root)["runs"][0]["handover_in"] is None


# ---- the producer folds out-of-band reports (Greptile #6) -------------------------------------------------------------------------------
_SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _status(env, task="T1-be", *, wall, kind="progress", step="reading", percent=None, corr="corr-1"):
    """What shared_status_report leaves behind: `<swarm_dir>/status/<task_id>.json`, replaced atomically (a new inode)."""
    path = env.sdir / "status" / f"{task}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp-{time.monotonic_ns()}")
    tmp.write_text(json.dumps({"task_id": task, "correlation_id": corr, "agent": "A05", "kind": kind, "step": step,
                               "percent": percent, "note": None, "updated_wall": wall, "seq": 1}))
    tmp.replace(path)


def test_producer_folds_a_status_report_into_the_run_activity_and_the_live_file(env):
    """The derived refresh a report triggers never replaces a live scheduler's live.json, so the producer reads it."""
    t, fake = _running(env)
    fake.advance(30)
    assert t.snapshot()["runs"][0]["activity"] is None
    _status(env, wall=fake.wall(), step="reading the spec", percent=40)
    row = t.snapshot()["runs"][0]
    assert row["activity"] == "progress: reading the spec (40%)" and row["since_activity_s"] == 0.0
    t.flush_live(force=True)
    assert env.tel.read_live(env.root)["runs"][0]["activity"] == "progress: reading the spec (40%)"


def test_the_newest_of_a_status_report_and_tool_activity_wins(env):
    t, fake = _running(env)
    fake.advance(5)
    _status(env, wall=fake.wall(), step="plan")
    assert t.snapshot()["runs"][0]["activity"] == "progress: plan"
    fake.advance(2)
    t.activity("T1-be.a1", tool="edit")  # newer than the report
    assert t.snapshot()["runs"][0]["activity"] == "edit"  # the unchanged report file is not folded again over it
    fake.advance(2)
    _status(env, wall=fake.wall(), kind="blocked", step="waiting for the design")
    assert t.snapshot()["runs"][0]["activity"] == "blocked: waiting for the design"
    fake.advance(2)
    _status(env, wall=fake.wall() - 10, step="older than the last tool call")  # written late, stamped early: loses
    t.activity("T1-be.a1", tool="bash")
    assert t.snapshot()["runs"][0]["activity"] == "bash"


def test_a_status_report_ends_a_stall_episode(env):
    t, fake = _running(env, stall_after_s=60)
    fake.advance(120)
    assert t.stalled_runs() == ["T1-be.a1"]
    _status(env, wall=fake.wall(), step="still working")
    assert t.stalled_runs() == [] and t.snapshot()["runs"][0]["stalled"] is False


def test_a_report_of_an_earlier_attempt_or_another_task_or_swarm_is_not_shown(env):
    t, fake = _running(env)
    fake.advance(100)
    _status(env, wall=fake.wall() - 90, step="left over from attempt 1")  # before this attempt started
    _status(env, task="T9-other", wall=fake.wall(), step="not mine")
    _status(env, wall=fake.wall(), step="another correlation", corr="corr-2")
    assert t.snapshot()["runs"][0]["activity"] is None


def test_the_status_file_is_parsed_only_when_it_changed(env, monkeypatch):
    t, fake = _running(env)
    _status(env, wall=fake.wall(), step="one")
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self.name), real(self, *a, **k))[1])
    for _ in range(3):
        t.snapshot()
    assert reads.count("T1-be.json") == 1
    fake.advance(1)
    _status(env, wall=fake.wall(), step="two")
    assert t.snapshot()["runs"][0]["activity"] == "progress: two" and reads.count("T1-be.json") == 2


def test_a_folded_report_is_redacted_and_bounded(env):
    t, fake = _running(env)
    _status(env, wall=fake.wall(), step=f"token {_SECRET} " + "x" * 400)
    activity = t.snapshot()["runs"][0]["activity"]
    assert _SECRET not in activity and len(activity) <= env.tel.CLIP and "[REDACTED]" in activity
    _status(env, wall=fake.wall(), step="y" * (env.tel.STATUS_MAX_BYTES + 10))  # oversized file: ignored, never read
    fake.advance(1)
    assert t.snapshot()["runs"][0]["activity"] == activity


def test_settled_runs_are_not_updated_by_a_late_report(env):
    t, fake = _running(env)
    t.finish("T1-be.a1", "ok")
    _status(env, wall=fake.wall(), step="after the end")
    assert t.snapshot()["runs"][0]["activity"] is None


def test_producer_folds_handover_markers_from_the_handover_files(env):
    from test_handover import base
    ho = importlib.import_module("swarm.handover")
    t, fake = _running(env)  # T1-be.a1 on a05-backend
    t.run_queued("T9.a1", "a08-qa")
    t.run_started("T9.a1")
    rows = lambda: {r["task_id"]: r for r in t.snapshot()["runs"]}  # noqa: E731
    assert rows()["T1-be"]["handover_out"] is None and rows()["T9"]["handover_in"] is None
    ho.write_handover(base(correlation_id="corr-1", task_id="T1-be", from_agent="A05", to_agent="A08"), root=env.root, emit=False)
    ho.write_handover(base(correlation_id="corr-1", task_id="T9", from_agent="A05", to_agent="A08"), root=env.root, emit=False)
    snap = rows()
    assert snap["T1-be"]["handover_out"] == "T1-be.1" and snap["T1-be"]["handover_in"] is None
    assert snap["T9"]["handover_in"] == "T9.1" and snap["T9"]["handover_out"] is None
    t.flush_live(force=True)
    _assert_valid(env, env.tel.read_live(env.root))
