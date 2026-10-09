"""T-05-30 and T-05-31: a gate task's result can only be accepted on a verdict row from its own lease, and A01 cannot
transition around a refused gate result.

T-05-30: an ingest that claims a PLANNED/RETRY gate task used to measure "the lease" before it made it, so a row the
PREVIOUS lease's script issued satisfied the check and the target went DONE without any gate script running.
T-05-31: `orch_status --transition` accepted every legal move, so A01 could push a refused gate task to IN_REVIEW
(skipping the script-ran and verdict-parity checks) or approve a target whose issuing gate task was never accepted.

Both are exercised through the real CLI, as A01 and the omp tools reach them.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from test_ingest_parity import KEY, _PASS, _REQUEST_CHANGES, _assert_mismatch_refused, _ingest, _rev_gate, _script, _setup
from test_runner_gates import _lease

_PLAN_WITH_FE = [
    {"id": "be", "capability": "code.backend", "agent": "A05"},
    {"id": "fe", "capability": "code.frontend", "agent": "A06"},
    {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be"], "gates": {"gate": "review", "for": ["be"]}},
]


@pytest.fixture()
def runner_key(monkeypatch):
    """Rows the gate scripts (subprocesses) signed carry the runner's key; the in-process store must verify with it."""
    monkeypatch.setenv("SWARM_SIGNING_KEY", KEY)


def _transition(swarm: Path, task: str, state: str):
    return _script(swarm, "orch_status.py", "--transition", task, state, "--json")


def _error(r) -> dict:
    assert r.returncode == 2, r.stdout + r.stderr
    return json.loads(r.stdout)["error"]


def _not_run(r) -> None:
    err = _error(r)
    assert err["code"] == "E-CONTRACT" and "gate script not run for ['P-be']" in err["message"], err


def _failed_then_retry(ts, swarm: Path) -> None:
    """The gate task's first lease recorded a passing row; the session then died and A01 re-queued it."""
    ts.transition("P-rev", "FAILED", reason="session died")
    assert _transition(swarm, "P-rev", "RETRY").returncode == 0


# ---------------------------------------------------------------------------------------------------------------------
# T-05-30


def test_ingest_into_a_retried_gate_task_without_a_new_lease_is_refused(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})  # lease 1: the script ran and passed P-be
    _failed_then_retry(ts, swarm)
    _not_run(_ingest(swarm, {"P-be": _PASS}))
    assert ts.get("P-rev")["state"] == "RETRY"  # not claimed by the refused ingest
    assert ts.get("P-be")["state"] == "IN_REVIEW"
    assert [h["to_state"] for h in ts.history("P-rev")][-2:] == ["FAILED", "RETRY"]


def test_ingest_into_a_gate_task_released_from_blocked_without_a_new_lease_is_refused(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    ts.transition("P-rev", "BLOCKED", reason="needs: human-approval")
    assert _transition(swarm, "P-rev", "PLANNED").returncode == 0
    _not_run(_ingest(swarm, {"P-be": _PASS}))
    assert ts.get("P-rev")["state"] == "PLANNED" and ts.get("P-be")["state"] == "IN_REVIEW"


def test_the_prescribed_re_lease_still_works(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    _failed_then_retry(ts, swarm)
    _lease(ts, "P-rev")  # RETRY -> CLAIMED -> IN_PROGRESS, as the orchestrate skill prescribes
    _rev_gate(swarm, work, corr, {"P-be": []})  # lease 2: the script ran again
    r = _ingest(swarm, {"P-be": _PASS})
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("P-be")["state"] == "DONE"
    assert ts.get("P-rev")["state"] == "DONE"  # a gate task requires no gates itself, so reconcile completes it


def test_a_re_lease_without_a_new_script_run_is_still_refused(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    _failed_then_retry(ts, swarm)
    _lease(ts, "P-rev")
    _not_run(_ingest(swarm, {"P-be": _PASS}))  # unchanged behaviour: the row is from the previous lease


def _re_lease_after_the_script_check(monkeypatch):
    """The race window: the first script check still sees this lease's row, then A01 re-leases before the locked recheck."""
    from swarm import results
    original = results._gate_script_missing

    def after_the_check(store, task):
        missing = original(store, task)
        if not after_the_check.done:
            assert missing == [], missing  # this lease's row verified; the refusal is the locked recheck's
            after_the_check.done = True
            store.transition("P-rev", "FAILED", reason="session died")
            store.transition("P-rev", "RETRY", reason="requeue")
            _lease(store, "P-rev")  # a new lease, and the script has not run in it
        return missing

    after_the_check.done = False
    monkeypatch.setattr(results, "_gate_script_missing", after_the_check)


_REVIEW_PASS = {"task_id": "P-rev", "state": "IN_REVIEW", "gate": "review", "verdicts": {"P-be": _PASS}}


def test_a_re_lease_that_lands_after_the_script_check_is_still_refused(tmp_path, swarm_dir, runner_key, monkeypatch):
    """The check used to run on the caller's snapshot. A retry that reached IN_PROGRESS again before the transition
    made that transition legal, and the previous lease's row then approved the target. Ingest is a subprocess, so
    the interleaving is injected where the check returns, which is the window the race uses."""
    from swarm import results
    from swarm.errors import SwarmError

    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    snapshot = ts.get("P-rev")
    _re_lease_after_the_script_check(monkeypatch)
    with pytest.raises(SwarmError, match=r"gate script not run for \['P-be'\]"):
        results.apply_result(ts, snapshot, agent_id="A09", result=_REVIEW_PASS, meta={}, emit=lambda *a: None, mode="ingest")
    assert ts.get("P-rev")["state"] == "IN_PROGRESS"  # the new lease stands; the result was not accepted
    assert ts.get("P-be")["state"] == "IN_REVIEW"
    assert "IN_REVIEW" not in [h["to_state"] for h in ts.history("P-rev")]


def test_a_re_lease_that_lands_after_the_script_check_does_not_fail_the_new_attempt(tmp_path, swarm_dir, runner_key, monkeypatch):
    """Headless twin of the race: the older result is rejected, and the replacement attempt stays IN_PROGRESS."""
    from swarm import results

    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    snapshot = ts.get("P-rev")
    _re_lease_after_the_script_check(monkeypatch)
    events = []
    outcome = results.apply_result(ts, snapshot, agent_id="A09", result=_REVIEW_PASS, meta={},
                                   emit=lambda t, p: events.append((t, p)), mode="headless")
    assert outcome == "IN_PROGRESS"
    assert ts.get("P-rev")["state"] == "IN_PROGRESS"  # not FAILED
    assert ts.get("P-rev")["attempt"] == snapshot["attempt"] + 1
    assert ts.history("P-rev")[-1]["to_state"] == "IN_PROGRESS"
    assert ts.get("P-be")["state"] == "IN_REVIEW"
    assert "IN_REVIEW" not in [h["to_state"] for h in ts.history("P-rev")]
    assert [p for t, p in events if t == "task.result.rejected"] == [
        {"task_id": "P-rev", "mode": "headless", "reason": "E-CONTRACT: gate script not run for ['P-be']"}]


def test_a_same_attempt_headless_refusal_still_fails(tmp_path, swarm_dir, runner_key):
    """A gate result whose script did not run for this attempt still ends FAILED."""
    from swarm import results

    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})  # lease 1 recorded a passing row
    _failed_then_retry(ts, swarm)
    _lease(ts, "P-rev")  # this attempt: the script has not run
    snapshot = ts.get("P-rev")
    events = []
    outcome = results.apply_result(ts, snapshot, agent_id="A09", result=_REVIEW_PASS, meta={},
                                   emit=lambda t, p: events.append((t, p)), mode="headless")
    assert outcome == "FAILED"
    assert ts.get("P-rev")["state"] == "FAILED"
    assert ts.get("P-rev")["attempt"] == snapshot["attempt"]
    assert ts.history("P-rev")[-1]["to_state"] == "FAILED"
    assert ts.history("P-rev")[-1]["reason"].startswith("E-CONTRACT: gate script not run")
    assert ts.get("P-be")["state"] == "IN_REVIEW"
    assert [p["mode"] for t, p in events if t == "task.result.rejected"] == ["headless"]


def test_gate_script_missing_counts_every_target_before_a_lease_exists(tmp_path, swarm_dir, runner_key):
    from swarm import results
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    assert results._gate_script_missing(ts, ts.get("P-rev")) == []  # leased, row issued during this lease
    _failed_then_retry(ts, swarm)
    assert results._gate_script_missing(ts, ts.get("P-rev")) == ["P-be"]  # RETRY: no lease, so the old row does not count
    ts.transition("P-rev", "PLANNED")
    assert results._gate_script_missing(ts, ts.get("P-rev")) == ["P-be"]


# ---------------------------------------------------------------------------------------------------------------------
# T-05-31


def test_a_gate_task_cannot_be_moved_to_in_review_by_hand(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    _assert_mismatch_refused(ts, _ingest(swarm, {"P-be": _REQUEST_CHANGES}))  # the review is refused
    err = _error(_transition(swarm, "P-rev", "IN_REVIEW"))
    assert err["code"] == "E-POLICY" and "gate task" in err["message"] and "ingest" in err["message"]
    assert ts.get("P-rev")["state"] == "IN_PROGRESS"
    assert [h["to_state"] for h in ts.history("P-rev")][-1] == "IN_PROGRESS"


def test_other_manual_transitions_of_a_gate_task_and_in_review_of_other_tasks_still_work(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path, _PLAN_WITH_FE)
    _lease(ts, "P-fe")
    assert _transition(swarm, "P-fe", "IN_REVIEW").returncode == 0  # a non-gate task: unchanged
    assert ts.get("P-fe")["state"] == "IN_REVIEW"
    assert _transition(swarm, "P-rev", "BLOCKED").returncode == 0  # a gate task to any other state: unchanged
    assert ts.get("P-rev")["state"] == "BLOCKED"


@pytest.mark.parametrize("issuer_state", ["IN_PROGRESS", "FAILED", "BLOCKED", "RETRY"])
def test_a_target_cannot_be_approved_by_hand_while_its_gate_task_is_unaccepted(tmp_path, swarm_dir, issuer_state):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})  # a passing row, issued by P-rev, whose result was never accepted
    if issuer_state != "IN_PROGRESS":
        ts.transition("P-rev", "FAILED" if issuer_state in ("FAILED", "RETRY") else "BLOCKED")
    if issuer_state == "RETRY":
        ts.transition("P-rev", "RETRY")
    err = _error(_transition(swarm, "P-be", "APPROVED"))
    assert err["code"] == "E-POLICY" and "unaccepted gates ['review']" in err["message"], err
    assert ts.get("P-be")["state"] == "IN_REVIEW"


def test_a_target_can_be_approved_by_hand_once_its_gate_task_is_accepted(tmp_path, swarm_dir):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    f = swarm.parent / "result.json"
    f.write_text(json.dumps({"task_id": "P-rev", "state": "IN_REVIEW", "gate": "review", "verdicts": {"P-be": _PASS}}))
    r = _script(swarm, "orch_status.py", "--ingest", str(f), "--advisory", "--json")  # accepted, approval not attempted
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("P-rev")["state"] == "IN_REVIEW" and ts.get("P-be")["state"] == "IN_REVIEW"
    assert _transition(swarm, "P-be", "APPROVED").returncode == 0
    assert ts.get("P-be")["state"] == "APPROVED"


def test_a_row_naming_an_unknown_gate_task_never_approves(tmp_path, swarm_dir):
    from swarm.errors import SwarmError
    from swarm.gates import make_verdict
    ts, swarm, work, corr = _setup(tmp_path, leased=())
    env = make_verdict(gate="review", task_id="P-be", agent_id="A09@test", findings=[], expires_s=3600,
                       correlation_id=corr, extra={"gate_task": "NOPE"})
    ts.record_verdict("P-be", env)
    assert ts.missing_gates("P-be") == []  # the row verifies...
    assert ts.unaccepted_gates("P-be") == {"review": "unaccepted"}  # ...but its issuer does not exist: fail closed
    with pytest.raises(SwarmError, match="unaccepted gates"):
        ts.transition("P-be", "APPROVED")
    assert ts.get("P-be")["state"] == "IN_REVIEW"


def test_a_row_without_an_issuer_is_accepted_as_before(tmp_path, swarm_dir):
    from swarm.gates import make_verdict
    ts, swarm, work, corr = _setup(tmp_path, leased=())
    ts.record_verdict("P-be", make_verdict(gate="review", task_id="P-be", agent_id="A09@test", findings=[], expires_s=3600,
                                           correlation_id=corr))
    assert ts.unaccepted_gates("P-be") == {}
    ts.transition("P-be", "APPROVED")
    assert ts.get("P-be")["state"] == "APPROVED"


def test_unaccepted_gates_skips_gates_that_fail_for_another_reason(tmp_path, swarm_dir, runner_key):
    ts, swarm, work, corr = _setup(tmp_path)
    _rev_gate(swarm, work, corr, {"P-be": []})
    assert ts.unaccepted_gates("P-be") == {"review": "unaccepted"}
    assert ts.unaccepted_gates("P-be", {"review": "expired"}) == {}  # already reported as missing; not double-counted
    con = sqlite3.connect(swarm / "tasks.db")
    con.execute("UPDATE verdicts SET envelope_json='{broken' WHERE task_id='P-be'")
    con.commit()
    con.close()
    assert ts.missing_gate_reasons("P-be") == {"review": "bad-sig"}
    assert ts.unaccepted_gates("P-be") == {}


# ---------------------------------------------------------------------------------------------------------------------
# Batch-01 rank 1: apply_result is atomic — the state transition and its provenance (notes, artifact
# rows, gate-target feedback) commit in one transaction, so a crash at any point leaves a
# fully-applied or fully-rolled-back result, never a new state with stale notes/missing artifacts.


def _atomic_task(ts):
    """A non-gate task leased to IN_PROGRESS, ready for an IN_REVIEW result."""
    ts.create(task_id="A-1", correlation_id="c", capability="code.backend", notes={"gates": []})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"):
        ts.transition("A-1", s)
    return "A-1"


def _atomic_result(tid):
    return {"task_id": tid, "state": "IN_REVIEW",
            "outputs": [{"kind": "code.backend", "uri": "file://x", "version": "1", "digest": ""}]}


@pytest.mark.parametrize("fail_at", ["set_notes", "add_artifact"])
def test_apply_result_rolls_back_transition_when_a_provenance_write_fails(swarm_dir, monkeypatch, fail_at):
    """Failure injected at either provenance write rolls back the transition too: no half-state."""
    from swarm import results
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    tid = _atomic_task(ts)
    before = ts.history(tid)

    def _boom(*a, **k):
        raise RuntimeError(f"injected crash at {fail_at}")
    monkeypatch.setattr(ts, fail_at, _boom)

    with pytest.raises(RuntimeError, match=f"injected crash at {fail_at}"):
        results.apply_result(ts, ts.get(tid), agent_id="A05", result=_atomic_result(tid), meta={"argv": ["x"]},
                             emit=lambda *a: None, mode="headless")
    after = ts.get(tid)
    assert after["state"] == "IN_PROGRESS"  # the transition did not survive the crash
    assert "result" not in after["notes_json"]  # no new-state-with-stale-notes
    assert after["outputs"] == []
    assert ts.history(tid) == before  # no audit row for the rolled-back move
    assert ts.conn.execute("SELECT COUNT(*) FROM artifacts WHERE task_id=?", (tid,)).fetchone()[0] == 0


def test_apply_result_rolls_back_a_real_artifact_write(swarm_dir, monkeypatch):
    """Failure after the first artifact row still rolls back: the row, the outputs update, notes, and transition."""
    from swarm import results
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    tid = _atomic_task(ts)
    before = ts.history(tid)
    real_add = ts.add_artifact
    calls = {"n": 0}
    def _flaky(*a, **k):
        out = real_add(*a, **k)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected crash after first artifact")
        return out
    monkeypatch.setattr(ts, "add_artifact", _flaky)
    result = {"task_id": tid, "state": "IN_REVIEW",
              "outputs": [{"kind": "code.backend", "uri": "file://x", "version": "1", "digest": ""},
                          {"kind": "docs.bundle", "uri": "file://y", "version": "1", "digest": ""}]}
    with pytest.raises(RuntimeError, match="injected crash after first artifact"):
        results.apply_result(ts, ts.get(tid), agent_id="A05", result=result, meta={"argv": ["x"]},
                             emit=lambda *a: None, mode="headless")
    assert calls["n"] == 1  # one row really landed before the crash
    after = ts.get(tid)
    assert after["state"] == "IN_PROGRESS"
    assert "result" not in after["notes_json"]
    assert after["outputs"] == []
    assert ts.history(tid) == before
    assert ts.conn.execute("SELECT COUNT(*) FROM artifacts WHERE task_id=?", (tid,)).fetchone()[0] == 0


def _atomic_gate_task(ts, tid="G-1", targets=("T-a", "T-b")):
    """A leased review gate task over two targets, with a failing script row per target from this lease,
    so the gate checks pass and the result reaches target feedback."""
    from swarm.gates import make_verdict
    for t in targets:
        ts.create(task_id=t, correlation_id="c", capability="code.backend", notes={"gates": []})
    ts.create(task_id=tid, correlation_id="c", capability="gate.review",
              notes={"gate": "review", "gate_for": list(targets)})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"):
        ts.transition(tid, s)
    for t in targets:
        ts.record_verdict(t, make_verdict(gate="review", task_id=t, agent_id="A09@test", verdict="fail",
                                          findings=[{"severity": "major", "kind": "k", "summary": "s"}],
                                          correlation_id="c", extra={"gate_task": tid}))
    return tid


def test_apply_result_rolls_back_first_target_feedback_on_a_gate_task(swarm_dir, monkeypatch):
    """Failure after the first target's feedback rolls back every earlier write: the transition, notes,
    the artifact row, and the feedback already appended to that first target."""
    from swarm import results
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    targets = ("T-a", "T-b")
    tid = _atomic_gate_task(ts, targets=targets)
    before = ts.history(tid)
    real_feedback = ts.append_feedback
    calls = {"n": 0}
    def _flaky(target, entry):
        out = real_feedback(target, entry)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("injected crash after first target feedback")
        return out
    monkeypatch.setattr(ts, "append_feedback", _flaky)
    result = {"task_id": tid, "state": "IN_REVIEW",
              "outputs": [{"kind": "code.backend", "uri": "file://x", "version": "1", "digest": ""}],
              "verdicts": {t: {"verdict": "request_changes", "findings": []} for t in targets}}
    with pytest.raises(RuntimeError, match="injected crash after first target feedback"):
        results.apply_result(ts, ts.get(tid), agent_id="A09", result=result, meta={"argv": ["x"]},
                             emit=lambda *a: None, mode="headless")
    assert calls["n"] == 1  # T-a's feedback really landed before the crash
    after = ts.get(tid)
    assert after["state"] == "IN_PROGRESS"
    assert "result" not in after["notes_json"]
    assert after["outputs"] == []
    assert ts.history(tid) == before
    assert ts.conn.execute("SELECT COUNT(*) FROM artifacts WHERE task_id=?", (tid,)).fetchone()[0] == 0
    for t in targets:
        assert ts.get(t)["notes_json"].get("feedback", []) == []


def test_apply_result_success_path_writes_state_notes_and_artifacts(swarm_dir):
    """No behavior change on success: same final state, notes, and artifact rows as before the move."""
    from swarm import results
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    tid = _atomic_task(ts)
    result = _atomic_result(tid)
    out = results.apply_result(ts, ts.get(tid), agent_id="A05", result=result, meta={"argv": ["x"]},
                               emit=lambda *a: None, mode="headless")
    assert out == "IN_REVIEW"
    after = ts.get(tid)
    assert after["state"] == "IN_REVIEW"
    assert after["notes_json"]["result"] == result
    assert after["notes_json"]["meta"] == {"argv": ["x"]}
    assert [dict(a) for a in ts.conn.execute(
        "SELECT kind, uri, version, digest, producer FROM artifacts WHERE task_id=?", (tid,))] == [
        {"kind": "code.backend", "uri": "file://x", "version": "1", "digest": "", "producer": "A05"}]
    assert after["outputs"] == [{"kind": "code.backend", "uri": "file://x", "version": "1", "digest": ""}]
    assert [h["to_state"] for h in ts.history(tid)][-1] == "IN_REVIEW"
