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
