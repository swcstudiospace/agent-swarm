"""In-session parity: a review gate task's ingest (orch_status --ingest) reaches the same end states as the headless
runner, and an agent verdicts{} that fails a target whose lease-bound review row is not `fail` is refused."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_runner_gates import _clean_env, _lease, _load_swarm_run

ROOT = Path(__file__).resolve().parent.parent
KEY = "runner-secret"
_PLAN = [
    {"id": "be", "capability": "code.backend", "agent": "A05"},  # risk low: requires only the review gate
    {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be"],
     "gates": {"gate": "review", "for": ["be"]}},
]
_MAJOR = [{"severity": "major", "kind": "agent-verdict", "summary": "gate agent reported 'request_changes' without findings"}]
_REQUEST_CHANGES = {"verdict": "request_changes", "findings": []}
_PASS = {"verdict": "pass", "findings": []}


def _script(swarm: Path, name: str, *args) -> subprocess.CompletedProcess:
    env = _clean_env(SWARM_DIR=str(swarm), SWARM_SIGNING_KEY=KEY)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          env=env, cwd=ROOT)


def _setup(base: Path, plan: list | None = None, risk: str = "low", targets=("P-be",), leased=("P-rev",)):
    """Plan `plan` (default: P-be, low risk, + review gate task P-rev) on an empty repo; `targets` IN_REVIEW, the
    `leased` gate tasks leased by A01. Returns (store, state dir, repo, correlation id)."""
    from swarm.taskstore import GATES_BY_RISK, TaskStore
    work, swarm = base / "work", base / ".swarm"
    work.mkdir(parents=True)
    plan_file = base / "plan.json"
    plan_file.write_text(json.dumps({"tasks": plan or _PLAN}))
    p = _script(swarm, "orch_plan.py", "--plan", str(plan_file), "--prefix", "P", "--risk-class", risk,
                "--repo", str(work), "--json")
    assert p.returncode == 0, p.stdout + p.stderr
    ts = TaskStore(swarm / "tasks.db")
    for t in targets:
        assert ts.required_gates(t) == GATES_BY_RISK[risk]
        _lease(ts, t)
        ts.transition(t, "IN_REVIEW")
    for g in leased:
        _lease(ts, g)
    return ts, swarm, work, json.loads(p.stdout)["correlation_id"]


def _rev_gate(swarm: Path, work: Path, corr: str, per_target: dict, gate_id: str = "P-rev") -> None:
    f = swarm.parent / "findings.json"
    f.write_text(json.dumps(per_target))
    g = _script(swarm, "rev_gate.py", "--task-id", gate_id, "--correlation-id", corr, "--root", str(work),
                "--per-target-findings", str(f), "--json")
    assert g.returncode in (0, 1), g.stdout + g.stderr
    assert json.loads(g.stdout)["recorded"] == sorted(per_target)


def _gate_script(swarm: Path, work: Path, corr: str, name: str, gate_id: str, *args) -> dict:
    """Run a non-review gate script for leased gate task `gate_id`; returns its --json output."""
    g = _script(swarm, name, "--task-id", gate_id, "--correlation-id", corr, "--root", str(work), *args, "--json")
    assert g.returncode in (0, 1), g.stdout + g.stderr
    return json.loads(g.stdout)


def _ingest(swarm: Path, verdicts: dict, gate_id: str = "P-rev", gate: str = "review") -> subprocess.CompletedProcess:
    f = swarm.parent / "result.json"
    f.write_text(json.dumps({"task_id": gate_id, "state": "IN_REVIEW", "gate": gate, "verdicts": verdicts}))
    return _script(swarm, "orch_status.py", "--ingest", str(f), "--json")


def _assert_mismatch_refused(ts, r) -> None:
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)["error"]
    assert err["code"] == "E-CONTRACT"
    assert err["message"].startswith("E-CONTRACT: review verdict mismatch:") and "P-be" in err["message"]
    assert err["details"]["targets"] == ["P-be"]
    assert ts.get("P-rev")["state"] == "IN_PROGRESS"
    assert ts.get("P-be")["state"] == "IN_REVIEW"


def test_contradicting_agent_verdict_refused(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    _assert_mismatch_refused(ts, _ingest(swarm, {"P-be": _REQUEST_CHANGES}))


def test_unattributed_failing_entry_refused(tmp_path, swarm_dir):
    """A failing entry under a key that is not a gate_for id ("be" for P-be) applies to every target."""
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    _assert_mismatch_refused(ts, _ingest(swarm, {"be": _REQUEST_CHANGES}))


def test_consistent_fail_starts_rework(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": _MAJOR})
    r = _ingest(swarm, {"P-be": _REQUEST_CHANGES})
    assert r.returncode == 0, r.stdout + r.stderr
    be = ts.get("P-be")
    assert (be["state"], be["rework_loops"]) == ("IN_PROGRESS", 1)
    assert [h["to_state"] for h in ts.history("P-be")][-2:] == ["CHANGES_REQUESTED", "IN_PROGRESS"]
    assert ts.get("P-rev.r1")["state"] == "PLANNED"


def _fail_review(swarm: Path, work: Path, corr: str, gate_id: str) -> None:
    _rev_gate(swarm, work, corr, {"P-be": _MAJOR}, gate_id)
    r = _ingest(swarm, {"P-be": _REQUEST_CHANGES}, gate_id)
    assert r.returncode == 0, r.stdout + r.stderr


def test_repeated_rework_creates_one_rerun_per_gate_lineage(tmp_path, swarm_dir):
    """Each rework loop adds exactly one <gate>.rN (never a rerun of a rerun); the failure past the rework limit
    escalates the target without another rerun."""
    ts, swarm, work, corr = _setup(tmp_path)

    def reruns() -> dict:
        return {t["task_id"]: t["state"] for t in ts.list(correlation_id=corr) if t["task_id"] not in ("P-be", "P-rev")}

    _fail_review(swarm, work, corr, "P-rev")
    assert ts.get("P-be")["rework_loops"] == 1
    assert reruns() == {"P-rev.r1": "PLANNED"}

    ts.transition("P-be", "IN_REVIEW")
    _lease(ts, "P-rev.r1")
    _fail_review(swarm, work, corr, "P-rev.r1")
    assert ts.get("P-be")["rework_loops"] == 2
    after_second = reruns()
    assert after_second.keys() == {"P-rev.r1", "P-rev.r2"} and after_second["P-rev.r2"] == "PLANNED"
    assert not [tid for tid in after_second if re.search(r"\.r\d+\.r\d+", tid)]

    ts.transition("P-be", "IN_REVIEW")
    _lease(ts, "P-rev.r2")
    _fail_review(swarm, work, corr, "P-rev.r2")
    assert ts.get("P-be")["state"] == "ESCALATED"
    assert reruns().keys() == {"P-rev.r1", "P-rev.r2"}


def test_consistent_pass_done(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    r = _ingest(swarm, {"P-be": {"verdict": "pass", "findings": []}})
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("P-be")["state"] == "DONE"


_PLAN_MEDIUM = [
    {"id": "be", "capability": "code.backend", "agent": "A05"},  # risk medium: requires the review and quality gates
    {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be"],
     "gates": {"gate": "review", "for": ["be"]}},
    {"id": "qa", "capability": "gate.quality", "agent": "A08", "depends_on": ["be"],
     "gates": {"gate": "quality", "for": ["be"]}},
]


def _qa_pass(swarm: Path, work: Path, corr: str, gate_id: str, targets: list[str]) -> None:
    """qa_gate on the empty repo at --risk-class low (only a minor no-runner finding: pass), then its ingest."""
    out = _gate_script(swarm, work, corr, "qa_gate.py", gate_id, "--risk-class", "low")
    assert (out["verdict"], out["recorded"]) == ("pass", sorted(targets))
    r = _ingest(swarm, {t: _PASS for t in targets}, gate_id, gate="quality")
    assert r.returncode == 0, r.stdout + r.stderr


def test_refused_review_holds_target_until_review_accepted(tmp_path, swarm_dir):
    """CR-01: the pass row of a refused review ingest (verdict mismatch) must not approve the target when another
    gate's ingest reconciles; the re-run review's accepted failure then reworks it."""
    ts, swarm, work, corr = _setup(tmp_path, _PLAN_MEDIUM, "medium", leased=("P-rev", "P-qa"))
    _rev_gate(swarm, work, corr, {"P-be": []})
    _assert_mismatch_refused(ts, _ingest(swarm, {"P-be": _REQUEST_CHANGES}))
    _qa_pass(swarm, work, corr, "P-qa", ["P-be"])
    assert ts.get("P-qa")["state"] == "DONE"
    assert ts.get("P-be")["state"] == "IN_REVIEW"
    _fail_review(swarm, work, corr, "P-rev")  # the review agent re-runs swarm_gate with the major finding
    be = ts.get("P-be")
    assert (be["state"], be["rework_loops"]) == ("IN_PROGRESS", 1)


_LEAVE_LEASE = {"FAILED": {"error": {"code": "E-CONTRACT", "message": "missing yield"}}, "BLOCKED": {"needs": "human-approval"}}


def _ingest_state(swarm: Path, gate_id: str, state: str) -> subprocess.CompletedProcess:
    """Ingest a FAILED or BLOCKED task.result for `gate_id` (skill step 5: a missing yield or a stated need)."""
    f = swarm.parent / "result.json"
    f.write_text(json.dumps({"task_id": gate_id, "state": state, **_LEAVE_LEASE[state]}))
    return _script(swarm, "orch_status.py", "--ingest", str(f), "--json")


def _escalations(swarm: Path, task_id: str) -> list[dict]:
    events = [json.loads(ln) for ln in (swarm / "events.jsonl").read_text().splitlines()]
    return [e for e in events if e["type"] == "escalation.request" and e["payload"]["task_id"] == task_id]


@pytest.mark.parametrize("state", ["FAILED", "BLOCKED"])
def test_refused_review_holds_target_after_gate_task_leaves_lease(tmp_path, swarm_dir, state):
    """T-05-11: the refused review's pass row never approves the target, also once its gate task leaves the lease
    through a FAILED or BLOCKED ingest; that gate task is still live (step 7 retries it, A01 releases it), so no
    escalation either."""
    ts, swarm, work, corr = _setup(tmp_path, _PLAN_MEDIUM, "medium", leased=("P-rev", "P-qa"))
    _rev_gate(swarm, work, corr, {"P-be": []})
    _assert_mismatch_refused(ts, _ingest(swarm, {"P-be": _REQUEST_CHANGES}))
    _qa_pass(swarm, work, corr, "P-qa", ["P-be"])
    r = _ingest_state(swarm, "P-rev", state)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("P-rev")["state"] == state
    assert ts.get("P-be")["state"] == "IN_REVIEW"
    assert _escalations(swarm, "P-be") == []


def test_refused_review_holds_target_after_failed_retry(tmp_path, swarm_dir):
    """T-05-11: a refused review ingested FAILED and moved FAILED → RETRY by A01 (skill step 7) still holds the
    target when the quality pass is ingested afterwards; the retried review's accepted failure then reworks it."""
    ts, swarm, work, corr = _setup(tmp_path, _PLAN_MEDIUM, "medium", leased=("P-rev", "P-qa"))
    _rev_gate(swarm, work, corr, {"P-be": []})
    _assert_mismatch_refused(ts, _ingest(swarm, {"P-be": _REQUEST_CHANGES}))
    assert _ingest_state(swarm, "P-rev", "FAILED").returncode == 0
    t = _script(swarm, "orch_status.py", "--transition", "P-rev", "RETRY", "--reason", "retry", "--json")
    assert t.returncode == 0, t.stdout + t.stderr
    _qa_pass(swarm, work, corr, "P-qa", ["P-be"])
    assert (ts.get("P-rev")["state"], ts.get("P-be")["state"]) == ("RETRY", "IN_REVIEW")
    assert _escalations(swarm, "P-be") == []
    _lease(ts, "P-rev")
    _fail_review(swarm, work, corr, "P-rev")
    be = ts.get("P-be")
    assert (be["state"], be["rework_loops"]) == ("IN_PROGRESS", 1)


def test_leased_review_fail_reworks_with_review_rerun(tmp_path, swarm_dir):
    """WR-04: the review records a fail while leased and the quality result is ingested first, so reconcile reworks
    the target before the review is ingested: the leased review lineage still gets its rerun, and the reworked
    target waits for it (no review:absent stall or escalation) until it passes."""
    ts, swarm, work, corr = _setup(tmp_path, _PLAN_MEDIUM, "medium", leased=("P-rev", "P-qa"))
    _rev_gate(swarm, work, corr, {"P-be": _MAJOR})
    _qa_pass(swarm, work, corr, "P-qa", ["P-be"])
    be = ts.get("P-be")
    assert (be["state"], be["rework_loops"]) == ("IN_PROGRESS", 1)
    assert ts.get("P-rev.r1")["state"] == "PLANNED"
    assert _ingest(swarm, {"P-be": _REQUEST_CHANGES}).returncode == 0
    assert ts.get("P-rev")["state"] == "DONE"

    ts.transition("P-be", "IN_REVIEW")
    _lease(ts, "P-qa.r1")
    _qa_pass(swarm, work, corr, "P-qa.r1", ["P-be"])
    be = ts.get("P-be")
    assert (be["state"], be["rework_loops"]) == ("IN_REVIEW", 1)
    assert not be["notes_json"].get("gate_stall")
    assert _escalations(swarm, "P-be") == []

    _lease(ts, "P-rev.r1")
    _rev_gate(swarm, work, corr, {"P-be": []}, "P-rev.r1")
    assert _ingest(swarm, {"P-be": _PASS}, "P-rev.r1").returncode == 0
    assert ts.get("P-be")["state"] == "DONE"


_PLAN_TWO_TARGETS = [
    {"id": "be", "capability": "code.backend", "agent": "A05"},
    {"id": "fe", "capability": "code.frontend", "agent": "A06"},
    {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be", "fe"],
     "gates": {"gate": "review", "for": ["be", "fe"]}},
    {"id": "qa", "capability": "gate.quality", "agent": "A08", "depends_on": ["be", "fe"],
     "gates": {"gate": "quality", "for": ["be", "fe"]}},
]


def test_target_first_failing_at_rerun_gets_next_rerun(tmp_path, swarm_dir):
    """WR-01: a multi-target review gate fails P-be, then its rerun P-rev.r1 fails P-fe for the first time: P-fe
    gets P-rev.r2 (the lineage's next rerun), so once reworked it waits for a review instead of stalling."""
    ts, swarm, work, corr = _setup(tmp_path, _PLAN_TWO_TARGETS, "medium", targets=("P-be", "P-fe"))

    def review_lineage() -> dict:
        return {t["task_id"]: t["state"] for t in ts.list(correlation_id=corr) if t["task_id"].startswith("P-rev")}

    _rev_gate(swarm, work, corr, {"P-be": _MAJOR, "P-fe": []})
    assert _ingest(swarm, {"P-be": _REQUEST_CHANGES, "P-fe": _PASS}).returncode == 0
    assert ts.get("P-be")["rework_loops"] == 1 and review_lineage()["P-rev.r1"] == "PLANNED"

    ts.transition("P-be", "IN_REVIEW")
    _lease(ts, "P-rev.r1")
    _rev_gate(swarm, work, corr, {"P-be": [], "P-fe": _MAJOR}, "P-rev.r1")
    assert _ingest(swarm, {"P-be": _PASS, "P-fe": _REQUEST_CHANGES}, "P-rev.r1").returncode == 0
    fe = ts.get("P-fe")
    assert (fe["state"], fe["rework_loops"]) == ("IN_PROGRESS", 1)
    assert review_lineage().get("P-rev.r2") == "PLANNED"

    ts.transition("P-fe", "IN_REVIEW")
    _lease(ts, "P-qa")
    _qa_pass(swarm, work, corr, "P-qa", ["P-be", "P-fe"])
    assert ts.get("P-be")["state"] == "DONE"
    assert ts.get("P-fe")["state"] == "IN_REVIEW"
    assert not ts.get("P-fe")["notes_json"].get("gate_stall")
    events = (swarm / "events.jsonl").read_text().splitlines()
    assert not [e for e in map(json.loads, events)
                if e["type"] == "escalation.request" and e["payload"]["task_id"] == "P-fe"]


@pytest.mark.parametrize("rel_deps", [["rev", "qa", "sec"], ["be"]], ids=["after-gates", "after-build"])
def test_release_rerun_waits_for_other_gate_reruns(tmp_path, swarm_dir, rel_deps):
    """WR-03: after a release-gate failure reworks a high-risk target, the release rerun depends on the review,
    quality and security reruns of that rework, so it is not ready before them."""
    plan: list[dict] = [{"id": "be", "capability": "code.backend", "agent": "A05"}]
    plan += [{"id": s, "capability": cap, "agent": a, "depends_on": ["be"], "gates": {"gate": gate, "for": ["be"]}}
             for s, cap, a, gate in (("rev", "gate.review", "A09", "review"), ("qa", "gate.quality", "A08", "quality"),
                                     ("sec", "gate.security", "A10", "security"))]
    plan.append({"id": "rel", "capability": "release.plan", "agent": "A12", "depends_on": rel_deps,
                 "gates": {"gate": "release", "for": ["be"]}})
    ts, swarm, work, corr = _setup(tmp_path, plan, "high", leased=())
    for g in ("P-rev", "P-qa", "P-sec"):  # these gate tasks ran; their rows are superseded by the rework anyway
        _lease(ts, g)
        for s in ("IN_REVIEW", "APPROVED", "DONE"):
            ts.transition(g, s)
    _lease(ts, "P-rel")
    assert _gate_script(swarm, work, corr, "rel_plan.py", "P-rel")["verdict"] == "fail"
    assert _ingest(swarm, {"P-be": {"verdict": "fail", "findings": []}}, "P-rel", gate="release").returncode == 0
    assert ts.get("P-be")["rework_loops"] == 1

    others = {"P-rev.r1", "P-qa.r1", "P-sec.r1"}
    assert others <= set(ts.get("P-rel.r1")["depends_on"])
    ts.transition("P-be", "IN_REVIEW")
    ready = {t["task_id"] for t in ts.ready(corr)}
    assert others <= ready and "P-rel.r1" not in ready


def _headless(base: Path, monkeypatch, verdicts: dict):
    """The headless runner's gate path for P-rev: gate_findings_file → run_gate_script → apply_result(headless) →
    reconcile. Returns (store, apply_result outcome)."""
    monkeypatch.setenv("SWARM_SIGNING_KEY", KEY)
    for k in ("SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY", "SWARM_AGENT_SESSION", "SWARM_CHILD", "SWARM_TASK_ID",
              "SWARM_CORRELATION_ID", "SWARM_DRYRUN_FAIL"):
        monkeypatch.delenv(k, raising=False)
    sr = _load_swarm_run(base, monkeypatch)
    from swarm.results import apply_result, parse_result, reconcile, validate_result
    ts, swarm, work, corr = _setup(base)
    (swarm / "results").mkdir(exist_ok=True)
    text = "done\n```json\n" + json.dumps({"task_id": "P-rev", "state": "IN_REVIEW", "gate": "review",
                                             "verdicts": verdicts}) + "\n```"

    def emit(t, p):
        return None

    task = ts.get("P-rev")
    sr.run_gate_script(task, work, swarm, dry_run=False, per_target_findings=sr.gate_findings_file(task, text, swarm, emit))
    result = validate_result(parse_result(text), task_id="P-rev")
    outcome = apply_result(ts, task, agent_id="A09", result=result, meta={}, emit=emit, mode="headless")
    reconcile(ts, corr, emit)
    return ts, outcome


def test_headless_and_in_session_reach_same_end_state(tmp_path, monkeypatch):
    """A failing agent verdict without findings: the headless runner (synthesized finding → rev_gate → apply_result →
    reconcile) and the in-session path (rev_gate with the equivalent major finding → ingest) agree."""
    verdicts = {"P-be": _REQUEST_CHANGES}
    hts, outcome = _headless(tmp_path / "headless", monkeypatch, verdicts)
    assert outcome == "IN_REVIEW"

    sts, sswarm, swork, scorr = _setup(tmp_path / "session")
    _rev_gate(sswarm, swork, scorr, {"P-be": _MAJOR})
    r = _ingest(sswarm, verdicts)
    assert r.returncode == 0, r.stdout + r.stderr

    headless, session = hts.get("P-be"), sts.get("P-be")
    assert (headless["state"], headless["rework_loops"]) == (session["state"], session["rework_loops"]) == ("IN_PROGRESS", 1)


def test_headless_failing_verdict_with_only_minor_findings_reworks_target(tmp_path, monkeypatch):
    """A failing agent entry whose findings are all below major still fails its target (a synthesized major finding),
    so the target goes to rework instead of the gate task failing on a review verdict mismatch."""
    minor = {"verdict": "request_changes", "findings": [{"severity": "Minor", "kind": "style", "summary": "naming nit"}]}
    ts, outcome = _headless(tmp_path, monkeypatch, {"P-be": minor})
    assert outcome == "IN_REVIEW"
    assert ts.get("P-rev")["state"] != "FAILED"
    be = ts.get("P-be")
    assert (be["state"], be["rework_loops"]) == ("IN_PROGRESS", 1)


def test_headless_quality_agent_failure_reaches_its_target_only(tmp_path, monkeypatch):
    """A quality agent's failing target reaches qa_gate through --per-target-findings: that target's recorded
    quality verdict fails although the script's own checks pass (low risk, empty repo), the other target passes."""
    monkeypatch.setenv("SWARM_SIGNING_KEY", KEY)
    for k in ("SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY", "SWARM_AGENT_SESSION", "SWARM_CHILD", "SWARM_TASK_ID",
              "SWARM_CORRELATION_ID", "SWARM_DRYRUN_FAIL"):
        monkeypatch.delenv(k, raising=False)
    sr = _load_swarm_run(tmp_path, monkeypatch)
    plan = [{"id": "be", "capability": "code.backend", "agent": "A05"},
            {"id": "fe", "capability": "code.frontend", "agent": "A06"},
            {"id": "qa", "capability": "gate.quality", "agent": "A08", "depends_on": ["be", "fe"],
             "gates": {"gate": "quality", "for": ["be", "fe"]}}]
    ts, swarm, work, _ = _setup(tmp_path, plan, targets=("P-be", "P-fe"), leased=("P-qa",))
    (swarm / "results").mkdir(exist_ok=True)
    text = "done\n```json\n" + json.dumps({"task_id": "P-qa", "state": "IN_REVIEW", "gate": "quality",
                                             "verdicts": {"P-be": _REQUEST_CHANGES, "P-fe": _PASS}}) + "\n```"
    task = ts.get("P-qa")
    findings = sr.gate_findings_file(task, text, swarm, lambda t, p: None)
    sr.run_gate_script(task, work, swarm, dry_run=False, per_target_findings=findings)
    assert ts.latest_verdicts("P-be")["quality"]["verdict"] == "fail"
    assert ts.latest_verdicts("P-fe")["quality"]["verdict"] == "pass"


@pytest.mark.parametrize("script,gate,gate_id", [("qa_gate.py", "quality", "P-qa"), ("sec_gate.py", "security", "P-sec")])
def test_gate_findings_for_one_target_leave_omitted_target_clean(tmp_path, script, gate, gate_id):
    """In-session swarm_gate: findings listed for one target only never fail a target the agent omitted, and the
    script's overall status fails because one recorded target verdict failed."""
    plan = [{"id": "be", "capability": "code.backend", "agent": "A05"},
            {"id": "fe", "capability": "code.frontend", "agent": "A06"},
            {"id": gate_id[2:], "capability": f"gate.{gate}", "agent": "A08" if gate == "quality" else "A10",
             "depends_on": ["be", "fe"], "gates": {"gate": gate, "for": ["be", "fe"]}}]
    ts, swarm, work, corr = _setup(tmp_path, plan, targets=("P-be", "P-fe"), leased=(gate_id,))
    f = tmp_path / "findings.json"
    f.write_text(json.dumps({"P-be": _MAJOR}))
    low = ["--risk-class", "low"] if gate == "quality" else []  # low risk: the empty repo's own checks pass
    g = _script(swarm, script, "--task-id", gate_id, "--correlation-id", corr, "--root", str(work),
                "--per-target-findings", str(f), *low, "--json")
    assert g.returncode == 1, g.stdout + g.stderr
    assert json.loads(g.stdout)["verdict"] == "fail"
    assert ts.latest_verdicts("P-be")[gate]["verdict"] == "fail"
    assert ts.latest_verdicts("P-fe")[gate]["verdict"] == "pass"


def test_qa_gate_runs_at_highest_risk_of_gate_and_targets(tmp_path, monkeypatch):
    """Without --risk-class, qa_gate (runner and in-session swarm_gate alike) runs at the highest risk class of the
    gate task and its targets, so a high-risk target requires the e2e and perf tiers; an unknown target is skipped."""
    from swarm.verdicts import gate_risk_class
    plan = [{"id": "be", "capability": "code.backend", "agent": "A05"},
            {"id": "fe", "capability": "code.frontend", "agent": "A06"},
            {"id": "qa", "capability": "gate.quality", "agent": "A08", "depends_on": ["be", "fe"],
             "gates": {"gate": "quality", "for": ["be", "fe"]}}]
    ts, swarm, work, corr = _setup(tmp_path, plan, targets=("P-be", "P-fe"), leased=("P-qa",))
    monkeypatch.setenv("SWARM_DIR", str(swarm))
    assert gate_risk_class("P-qa", work) == "low"
    with ts.conn:
        ts.conn.execute("UPDATE tasks SET risk_class='high' WHERE task_id='P-fe'")
    ts.set_notes("P-qa", gate_for=["P-be", "P-fe", "P-gone"])
    assert gate_risk_class("P-qa", work) == "high"
    assert gate_risk_class("P-be", work) is None  # not a gate task
    ts.set_notes("P-qa", gate_for=["P-be", "P-fe"])
    out = _gate_script(swarm, work, corr, "qa_gate.py", "P-qa")
    assert out["risk_class"] == "high"
    assert out["runs"]["e2e"] == "skipped:infra" and out["runs"]["perf"] == "skipped:infra"
