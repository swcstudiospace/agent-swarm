"""CORE-06: gate scripts are the only verdict writers; targets come from the gate task's notes.gate_for."""
import json
import sqlite3

import pytest

from conftest import run_script


def _plan(env, prefix="X", pattern="feature"):
    r = run_script("orch_plan.py", "--brief-text", "x", "--pattern", pattern, "--prefix", prefix, env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def _rows(swarm, task_id=None):
    con = sqlite3.connect(swarm / "tasks.db")
    con.row_factory = sqlite3.Row
    q, a = ("SELECT * FROM verdicts WHERE task_id=? ORDER BY id", (task_id,)) if task_id else ("SELECT * FROM verdicts ORDER BY id", ())
    rows = [dict(r) for r in con.execute(q, a)]
    con.close()
    return rows


def _events(swarm, etype):
    p = swarm / "events.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if f'"{etype}"' in ln] if p.exists() else []


def _notes(swarm, tid):
    con = sqlite3.connect(swarm / "tasks.db")
    (n,) = con.execute("SELECT notes FROM tasks WHERE task_id=?", (tid,)).fetchone()
    con.close()
    return json.loads(n or "{}")


def _store_task(swarm, tid, **notes):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    ts.create(task_id=tid, correlation_id="c", capability="qa.test", notes=notes)
    return ts


def test_gate_script_records_on_gate_for(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    targets = _notes(swarm_dir, "X-qa")["gate_for"]
    assert set(targets) == {"X-be", "X-fe", "X-data"}
    r = run_script("qa_gate.py", "--dry-run", "--task-id", "X-qa", "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    for t in targets:
        rows = _rows(swarm_dir, t)
        assert [(x["gate"], x["verdict"]) for x in rows] == [("quality", "pass")]
    assert _rows(swarm_dir, "X-qa") == []
    env_file = json.loads((swarm_dir / "verdicts" / "X-qa.quality.json").read_text())
    assert env_file["type"] == "gate.verdict"


def test_non_gate_task_id_records_nothing(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    r = run_script("qa_gate.py", "--dry-run", "--task-id", "X-be", "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (swarm_dir / "verdicts" / "X-be.quality.json").exists()
    assert _rows(swarm_dir) == []
    assert _events(swarm_dir, "gate.verdict.unrecorded")


def test_gate_mismatch_e_policy(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    r = run_script("rev_gate.py", "--dry-run", "--task-id", "X-qa", "--json", env=env)
    assert r.returncode == 2 and "E-POLICY" in r.stdout
    assert _rows(swarm_dir) == []


def test_dryrun_fail_inside_script(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir), "SWARM_DRYRUN_FAIL": "X-be:quality"}
    _plan(env)
    run_script("qa_gate.py", "--dry-run", "--task-id", "X-qa", "--json", env=env)
    (row,) = _rows(swarm_dir, "X-be")
    assert row["verdict"] == "fail" and [f["id"] for f in json.loads(row["findings"])] == ["SIM-1"]
    assert _rows(swarm_dir, "X-fe")[0]["verdict"] == "pass"
    fb = _notes(swarm_dir, "X-be")["feedback"]
    assert fb[-1]["gate"] == "quality" and fb[-1]["source"] == "script"
    assert "feedback" not in _notes(swarm_dir, "X-fe")


def test_two_gates_same_target_separate_rows(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.verdicts import record_gate_verdicts
    ts = TaskStore()
    ts.create(task_id="T-be", correlation_id="c", capability="code.backend")
    ts.create(task_id="G-q", correlation_id="c", capability="qa.test", notes={"gate": "quality", "gate_for": ["T-be"], "gates": []})
    ts.create(task_id="G-r", correlation_id="c", capability="review.code", notes={"gate": "review", "gate_for": ["T-be"], "gates": []})
    kw = dict(findings=[], runs={}, correlation_id="c", expires_s=60, emit=lambda *a, **k: None)
    record_gate_verdicts(ts, gate_task_id="G-q", gate="quality", agent_id="A08@local", **kw)
    record_gate_verdicts(ts, gate_task_id="G-r", gate="review", agent_id="A09@local", **kw)
    latest = ts.latest_verdicts("T-be")
    assert set(latest) == {"quality", "review"}
    assert latest["quality"]["agent_id"] == "A08@local" and latest["review"]["agent_id"] == "A09@local"


def test_rerun_supersedes_by_id(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_finding
    from swarm.verdicts import record_gate_verdicts
    ts = TaskStore()
    ts.create(task_id="T-be", correlation_id="c", capability="code.backend")
    ts.create(task_id="G-q", correlation_id="c", capability="qa.test", notes={"gate": "quality", "gate_for": ["T-be"], "gates": []})
    kw = dict(gate_task_id="G-q", gate="quality", agent_id="A08@local", runs={}, correlation_id="c", expires_s=60,
              emit=lambda *a, **k: None)
    record_gate_verdicts(ts, findings=[make_finding("F", "major", "functional", "x")], **kw)
    record_gate_verdicts(ts, findings=[], **kw)
    assert ts.latest_verdicts("T-be")["quality"]["verdict"] == "pass"


def test_empty_gate_for_records_nothing(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir)}
    _store_task(swarm_dir, "G-empty", gate="quality", gate_for=[], gates=[])
    r = run_script("qa_gate.py", "--dry-run", "--task-id", "G-empty", "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(swarm_dir) == []
    assert _events(swarm_dir, "gate.verdict.unrecorded")


def test_record_verdict_binds_envelope_task(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    from swarm.errors import SwarmError
    ts = TaskStore()
    ts.create(task_id="T-a", correlation_id="c", capability="code.backend")
    ts.create(task_id="T-b", correlation_id="c", capability="code.backend")
    env = make_verdict(gate="review", task_id="T-a", agent_id="A09@local")
    with pytest.raises(SwarmError) as e:
        ts.record_verdict("T-b", env)
    assert e.value.code.value == "E-CONTRACT"


def test_runner_inserts_no_verdicts(swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    r = run_script("swarm_run.py", "--dry-run", "--json", env=env)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-800:]
    con = sqlite3.connect(swarm_dir / "tasks.db")
    gates = {tid: json.loads(n) for tid, n in con.execute("SELECT task_id, notes FROM tasks")}
    con.close()
    gate_tasks = {t: n for t, n in gates.items() if n.get("gate")}
    rows = _rows(swarm_dir)
    assert rows and {x["agent_id"] for x in rows} <= {"A08@dry", "A09@dry", "A10@dry", "A12@dry"}
    assert len(rows) == sum(len(n["gate_for"]) for n in gate_tasks.values())
    for x in rows:
        assert any(x["task_id"] in n["gate_for"] and n["gate"] == x["gate"] for n in gate_tasks.values())
    for gid, n in gate_tasks.items():
        payload = json.loads((swarm_dir / "verdicts" / f"{gid}.{n['gate']}.json").read_text())["payload"]
        assert payload["task_id"] == gid


def test_ingest_gate_result_no_rows(tmp_path, swarm_dir):
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    targets = _notes(swarm_dir, "X-qa")["gate_for"]
    fnd = [{"id": "Q-1", "severity": "major", "kind": "functional", "summary": "broken"}]
    res = {"task_id": "X-qa", "gate": "quality", "state": "IN_REVIEW",
           "verdicts": {targets[0]: {"verdict": "fail", "findings": fnd}, targets[1]: {"verdict": "pass", "findings": []},
                        "X-rel": {"verdict": "pass", "findings": []}}}
    f = tmp_path / "r.json"
    f.write_text(json.dumps(res))
    r = run_script("orch_status.py", "--ingest", str(f), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(swarm_dir) == []
    assert _notes(swarm_dir, targets[0])["feedback"] == [{"gate": "quality", "source": "agent", "findings": fnd}]
    assert "feedback" not in _notes(swarm_dir, targets[1])
    assert _notes(swarm_dir, "X-qa")["result"]["verdicts"] == res["verdicts"]
