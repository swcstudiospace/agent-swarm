"""In-session parity: a review gate task's ingest (orch_status --ingest) reaches the same end states as the headless
runner, and an agent verdicts{} that fails a target whose lease-bound review row is not `fail` is refused."""
import json
import subprocess
import sys
from pathlib import Path

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


def _script(swarm: Path, name: str, *args) -> subprocess.CompletedProcess:
    env = _clean_env(SWARM_DIR=str(swarm), SWARM_SIGNING_KEY=KEY)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *args], capture_output=True, text=True,
                          env=env, cwd=ROOT)


def _setup(base: Path):
    """Plan P-be (low risk) + review gate task P-rev on an empty repo; P-be IN_REVIEW, P-rev leased by A01.
    Returns (store, state dir, repo, correlation id)."""
    from swarm.taskstore import TaskStore
    work, swarm = base / "work", base / ".swarm"
    work.mkdir(parents=True)
    plan = base / "plan.json"
    plan.write_text(json.dumps({"tasks": _PLAN}))
    p = _script(swarm, "orch_plan.py", "--plan", str(plan), "--prefix", "P", "--risk-class", "low",
                "--repo", str(work), "--json")
    assert p.returncode == 0, p.stdout + p.stderr
    ts = TaskStore(swarm / "tasks.db")
    assert ts.required_gates("P-be") == ["review"]
    _lease(ts, "P-be")
    ts.transition("P-be", "IN_REVIEW")
    _lease(ts, "P-rev")
    return ts, swarm, work, json.loads(p.stdout)["correlation_id"]


def _rev_gate(swarm: Path, work: Path, corr: str, per_target: dict) -> None:
    f = swarm.parent / "findings.json"
    f.write_text(json.dumps(per_target))
    g = _script(swarm, "rev_gate.py", "--task-id", "P-rev", "--correlation-id", corr, "--root", str(work),
                "--per-target-findings", str(f), "--json")
    assert g.returncode in (0, 1), g.stdout + g.stderr
    assert json.loads(g.stdout)["recorded"] == ["P-be"]


def _ingest(swarm: Path, verdicts: dict) -> subprocess.CompletedProcess:
    f = swarm.parent / "result.json"
    f.write_text(json.dumps({"task_id": "P-rev", "state": "IN_REVIEW", "gate": "review", "verdicts": verdicts}))
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


def test_consistent_pass_done(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    r = _ingest(swarm, {"P-be": {"verdict": "pass", "findings": []}})
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("P-be")["state"] == "DONE"


def _headless(base: Path, monkeypatch, verdicts: dict):
    """The headless runner's gate path for P-rev: review_findings_file → run_gate_script → apply_result(headless) →
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
    sr.run_gate_script(task, work, swarm, dry_run=False, per_target_findings=sr.review_findings_file(task, text, swarm, emit))
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
