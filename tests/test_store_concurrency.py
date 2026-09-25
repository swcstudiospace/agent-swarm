"""CORE-01/CORE-06 (WR-02/WR-03): Task Store writes stay atomic when several processes share one tasks.db."""
import os
import subprocess
import sys
import time

import pytest

from conftest import ROOT

# Child prelude: open the store, signal ready, then spin until the parent drops the go-file so all
# children hit the database at the same moment. argv: db ready go *rest.
_PRELUDE = f"""
import os, sys, time
sys.path.insert(0, {str(ROOT)!r})
from swarm.taskstore import TaskStore
from swarm.errors import SwarmError
db, ready, go, *rest = sys.argv[1:]
store = TaskStore(db)
open(ready, "w").close()
while not os.path.exists(go):
    time.sleep(0.0002)
"""


def _race(tmp_path, db, body, argv_per_proc, timeout=120):
    """Run one child per argv list, release them together, return their stripped stdouts."""
    go = tmp_path / f"go-{time.monotonic_ns()}"
    readies = [tmp_path / f"{go.name}.ready{k}" for k in range(len(argv_per_proc))]
    env = {**os.environ, "SWARM_DIR": str(db.parent)}
    procs = [subprocess.Popen([sys.executable, "-c", _PRELUDE + body, str(db), str(ready), str(go), *map(str, argv)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
             for ready, argv in zip(readies, argv_per_proc)]
    deadline = time.monotonic() + timeout
    while not all(r.exists() for r in readies) and time.monotonic() < deadline \
            and all(p.poll() is None for p in procs):
        time.sleep(0.005)
    go.touch()
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=timeout)
        assert p.returncode == 0, err
        outs.append(out.strip())
    return outs


def _planned(db, tid, **notes):
    from swarm.taskstore import TaskStore
    store = TaskStore(db)
    store.create(task_id=tid, correlation_id="c", capability="code.backend", notes=notes)
    store.transition(tid, "VALIDATED")
    store.transition(tid, "PLANNED")
    return store


# ------------------------------------------------------------------ WR-02: atomic, conditional transition
CLAIM = """
try:
    store.transition(rest[0], "CLAIMED")
    print("ok")
except SwarmError as e:
    print(e.code.value)
"""


def test_concurrent_claim_single_winner(tmp_path, swarm_dir):
    db = swarm_dir / "tasks.db"
    for rnd in range(5):
        tid = f"C-{rnd}"
        store = _planned(db, tid)
        outs = _race(tmp_path, db, CLAIM, [[tid]] * 4)
        assert sorted(outs) == ["E-CONTRACT"] * 3 + ["ok"], (rnd, outs)
        assert [h["to_state"] for h in store.history(tid)].count("CLAIMED") == 1
        assert store.get(tid)["attempt"] == 1


def _in_review_at_cap(store, tid):
    for s in ("CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        store.transition(tid, s)
    store.update(tid, rework_loops=2)


@pytest.mark.parametrize("case", ["claim", "rework-cap"])
def test_state_change_and_audit_row_atomic(swarm_dir, monkeypatch, case):
    from swarm.taskstore import TaskStore
    db = swarm_dir / "tasks.db"
    store = _planned(db, "A-1")
    if case == "rework-cap":  # IN_REVIEW → CHANGES_REQUESTED → ESCALATED: crash on the second audit row
        _in_review_at_cap(store, "A-1")
        to, fail_on = "CHANGES_REQUESTED", "ESCALATED"
    else:
        to, fail_on = "CLAIMED", "CLAIMED"
    before, row = store.history("A-1"), store.get("A-1")
    real_log = TaskStore._log

    def crashing_log(self, task_id, from_state, to_state, actor, reason):
        if to_state.value == fail_on:
            raise RuntimeError("crash between the state write and its audit row")
        return real_log(self, task_id, from_state, to_state, actor, reason)

    monkeypatch.setattr(TaskStore, "_log", crashing_log)
    with pytest.raises(RuntimeError):
        store.transition("A-1", to)
    monkeypatch.undo()
    after = TaskStore(db).get("A-1")
    assert (after["state"], after["attempt"], after["rework_loops"]) == (row["state"], row["attempt"], row["rework_loops"])
    assert TaskStore(db).history("A-1") == before


# ------------------------------------------------------------------ WR-03: notes read-modify-write, gate rows
FEEDBACK = """
for i in range(25):
    store.append_feedback(rest[0], {"p": int(rest[1]), "n": i})
print("ok")
"""

NOTES = """
for i in range(25):
    store.set_notes(rest[0], **{rest[1]: i})
print("ok")
"""


def test_append_feedback_no_lost_updates(tmp_path, swarm_dir):
    db = swarm_dir / "tasks.db"
    store = _planned(db, "F-1")
    assert _race(tmp_path, db, FEEDBACK, [["F-1", p] for p in range(4)]) == ["ok"] * 4
    fb = store.get("F-1")["notes_json"]["feedback"]
    assert len(fb) == 100
    assert sorted((e["p"], e["n"]) for e in fb) == [(p, i) for p in range(4) for i in range(25)]


def test_set_notes_keeps_concurrent_keys(tmp_path, swarm_dir):
    db = swarm_dir / "tasks.db"
    store = _planned(db, "N-1", keep="x")
    assert _race(tmp_path, db, NOTES, [["N-1", "a"], ["N-1", "b"]]) == ["ok", "ok"]
    notes = store.get("N-1")["notes_json"]
    assert (notes.get("a"), notes.get("b"), notes.get("keep")) == (24, 24, "x")


def test_record_gate_verdicts_all_or_nothing(swarm_dir):
    import sqlite3
    from swarm.errors import SwarmError
    from swarm.taskstore import TaskStore
    from swarm.verdicts import SIM_FINDING, record_gate_verdicts
    db = swarm_dir / "tasks.db"
    store = TaskStore(db)
    store.create(task_id="T-be", correlation_id="c", capability="code.backend")
    store.create(task_id="G-q", correlation_id="c", capability="qa.test",
                 notes={"gate": "quality", "gate_for": ["T-be", "T-missing"], "gates": []})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"):  # leased gate task
        store.transition("G-q", s)
    with pytest.raises(SwarmError):
        record_gate_verdicts(store, gate_task_id="G-q", gate="quality", agent_id="A08", findings=[dict(SIM_FINDING)],
                             runs={}, correlation_id="c", expires_s=3600, emit=lambda *a, **k: None)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM verdicts WHERE task_id='T-be'").fetchone()[0] == 0
    finally:
        con.close()
    assert "feedback" not in TaskStore(db).get("T-be")["notes_json"]
