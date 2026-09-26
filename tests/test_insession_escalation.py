"""G-2: the in-session rework ladder (real rev_gate → orch_status --ingest) escalates a target past the rework limit
and writes exactly one escalation.request for it to $SWARM_DIR/events.jsonl through the ingest's ctx.emit."""
import json

from test_ingest_parity import _fail_review, _lease, _script, _setup


def _escalations(swarm, task_id: str) -> list[dict]:
    p = swarm / "events.jsonl"
    events = [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []
    return [e for e in events if e["type"] == "escalation.request" and e["payload"].get("task_id") == task_id]


def test_in_session_rework_ladder_emits_escalation_request(tmp_path, swarm_dir):
    from swarm.taskstore import MAX_REWORK_LOOPS
    ts, swarm, work, corr = _setup(tmp_path)
    gate_ids = ["P-rev"] + [f"P-rev.r{n}" for n in range(1, MAX_REWORK_LOOPS + 1)]
    for n, gate_id in enumerate(gate_ids):
        if n:
            ts.transition("P-be", "IN_REVIEW")
            _lease(ts, gate_id)
        _fail_review(swarm, work, corr, gate_id)
        if n < MAX_REWORK_LOOPS:
            assert ts.get("P-be")["state"] == "IN_PROGRESS"
            assert _escalations(swarm, "P-be") == []

    h = _script(swarm, "orch_status.py", "--history", "P-be", "--json")
    assert h.returncode == 0, h.stdout + h.stderr
    states = [row["to_state"] for row in json.loads(h.stdout)["history"]]
    assert states[-1] == "ESCALATED"
    ladder = [s for s in states if s in ("CHANGES_REQUESTED", "IN_PROGRESS", "IN_REVIEW", "ESCALATED")]
    # after the first IN_REVIEW: each failure but the last is CHANGES_REQUESTED → IN_PROGRESS (rework) → IN_REVIEW
    rework = ["CHANGES_REQUESTED", "IN_PROGRESS", "IN_REVIEW"] * MAX_REWORK_LOOPS
    assert ladder[ladder.index("IN_REVIEW") + 1:] == rework + ["CHANGES_REQUESTED", "ESCALATED"]
    assert len(_escalations(swarm, "P-be")) == 1
