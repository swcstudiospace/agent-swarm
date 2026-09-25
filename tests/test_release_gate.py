"""CORE-07 / CORE-06: the release gate (A12 rel_plan) judges each target on verified gate evidence only."""
import json
import sqlite3
import time

from conftest import run_script


def _in_review(ts, tid, risk="medium"):
    ts.create(task_id=tid, correlation_id="c", capability="code.backend", risk_class=risk)
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        ts.transition(tid, s)


def _raw_row(ts, tid, gate, verdict, envelope_json=None):
    """A legacy/forged verdict row written straight into the table (no signed envelope)."""
    ts.conn.execute("INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts, envelope_json)"
                    " VALUES (?,?,?,?,?,?,?,?)", (tid, gate, verdict, "A09", "[]", time.time() + 999, time.time(), envelope_json))
    ts.conn.commit()


def _signed_passes(ts, tid, gates=("review", "quality")):
    from swarm.gates import make_verdict
    for g in gates:
        ts.record_verdict(tid, make_verdict(gate=g, task_id=tid, agent_id="A08", correlation_id="c"))


def _release_task(ts, tid, gate_for):
    """A release gate task leased by A01 (IN_PROGRESS): only then does rel_plan record rows on its targets."""
    ts.create(task_id=tid, correlation_id="c", capability="release.plan",
              notes={"gate": "release", "gate_for": list(gate_for), "gates": []})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"):
        ts.transition(tid, s)


def _rel_plan(swarm_dir, task_id="H-rel"):
    r = run_script("rel_plan.py", "--task-id", task_id, "--correlation-id", "c", "--json",
                   env={"SWARM_DIR": str(swarm_dir)})
    assert r.returncode in (0, 1), r.stdout + r.stderr
    return json.loads(r.stdout)


def _release_row(swarm_dir, tid):
    con = sqlite3.connect(swarm_dir / "tasks.db")
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM verdicts WHERE task_id=? AND gate='release' ORDER BY id", (tid,))]
    con.close()
    assert len(rows) == 1, rows
    row = rows[0]
    row["findings"] = json.loads(row["findings"])
    return row


def test_release_gate_ignores_unsigned_rows(swarm_dir):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    _in_review(ts, "H-be")
    _raw_row(ts, "H-be", "review", "pass")
    _raw_row(ts, "H-be", "quality", "pass")
    _release_task(ts, "H-rel", ["H-be"])
    out = _rel_plan(swarm_dir)
    assert out["verdict"] == "fail", out["tasks"]
    assert {"review:unsigned", "quality:unsigned"} <= set(out["tasks"]["H-be"]["problems"])
    plan = json.loads((swarm_dir / "releases" / "REL-H-be.plan.json").read_text())
    assert plan["gate_verdict"] == "fail"
    assert _release_row(swarm_dir, "H-be")["verdict"] == "fail"


def test_release_gate_passes_on_verified_rows(swarm_dir):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    _in_review(ts, "H-be")
    _signed_passes(ts, "H-be")
    _release_task(ts, "H-rel", ["H-be"])
    out = _rel_plan(swarm_dir)
    assert out["verdict"] == "pass", out["tasks"]
    assert out["tasks"]["H-be"]["problems"] == []
    assert _release_row(swarm_dir, "H-be")["verdict"] == "pass"
