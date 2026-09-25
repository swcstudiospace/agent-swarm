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


# ---------------------------------------------------------------- CORE-07 / D-14: verified verdict rows
def _in_review(ts, tid, gates=("review",)):
    ts.create(task_id=tid, correlation_id="c", capability="code.backend", notes={"gates": list(gates)})
    for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW"):
        ts.transition(tid, s)


def _raw_row(ts, tid, gate, verdict, envelope_json=None):
    import time
    ts.conn.execute("INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts, envelope_json)"
                    " VALUES (?,?,?,?,?,?,?,?)", (tid, gate, verdict, "A09", "[]", time.time() + 999, time.time(), envelope_json))
    ts.conn.commit()


def test_migration_idempotent_legacy_rows_missing(swarm_dir):
    from swarm.taskstore import TaskStore
    swarm_dir.mkdir(parents=True, exist_ok=True)
    db = swarm_dir / "tasks.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE verdicts (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, gate TEXT, verdict TEXT,"
                " agent_id TEXT, findings TEXT, expires_at REAL, ts REAL)")
    con.commit()
    con.close()
    ts = TaskStore(db)
    ts.conn.close()
    ts = TaskStore(db)
    cols = [r[1] for r in ts.conn.execute("PRAGMA table_info(verdicts)")]
    assert "envelope_json" in cols and "sig" in cols
    assert not (swarm_dir / "tasks.db.pre-v2").exists()  # option-a: no backup
    _in_review(ts, "L-1")
    ts.conn.execute("INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts)"
                    " VALUES ('L-1','review','pass','A09','[]',9e12,9e9)")
    ts.conn.commit()
    assert ts.missing_gates("L-1") == ["review"]
    assert ts.missing_gate_reasons("L-1") == {"review": "unsigned"}


def test_forged_or_unsigned_verdict_never_approves(swarm_dir, monkeypatch):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    from swarm.errors import SwarmError
    ts = TaskStore()
    # (a) unsigned legacy-style row
    _in_review(ts, "F-a")
    _raw_row(ts, "F-a", "review", "pass")
    # (b) payload.verdict flipped fail→pass after signing, row edited to match
    _in_review(ts, "F-b")
    ts.record_verdict("F-b", make_verdict(gate="review", task_id="F-b", agent_id="A09", verdict="fail"))
    (eid, ej) = ts.conn.execute("SELECT id, envelope_json FROM verdicts WHERE task_id='F-b'").fetchone()
    env = json.loads(ej)
    env["payload"]["verdict"] = "pass"
    ts.conn.execute("UPDATE verdicts SET verdict='pass', envelope_json=? WHERE id=?", (json.dumps(env), eid))
    ts.conn.commit()
    # (c) signed with k1, verified with k2
    _in_review(ts, "F-c")
    monkeypatch.setenv("SWARM_SIGNING_KEY", "k1")
    ts.record_verdict("F-c", make_verdict(gate="review", task_id="F-c", agent_id="A09"))
    monkeypatch.setenv("SWARM_SIGNING_KEY", "k2")
    for tid in ("F-a", "F-b", "F-c"):
        with pytest.raises(SwarmError) as e:
            ts.transition(tid, "APPROVED")
        assert e.value.code.value == "E-POLICY"
    reasons = []
    for tid in ("F-a", "F-b", "F-c"):
        r = run_script("orch_status.py", "--history", tid, "--json",
                       env={"SWARM_DIR": str(swarm_dir), "SWARM_SIGNING_KEY": "k2"})
        assert r.returncode == 0, r.stdout + r.stderr
        reasons.append(json.loads(r.stdout)["missing_gate_reasons"]["review"])
    assert reasons == ["unsigned", "bad-sig", "bad-sig"]


def test_row_verdict_mismatch(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    ts = TaskStore()
    _in_review(ts, "M-1")
    ts.record_verdict("M-1", make_verdict(gate="review", task_id="M-1", agent_id="A09", verdict="fail"))
    ts.conn.execute("UPDATE verdicts SET verdict='pass' WHERE task_id='M-1'")
    ts.conn.commit()
    assert ts.missing_gate_reasons("M-1") == {"review": "mismatch"}


def test_expiry_boundary(swarm_dir, monkeypatch):
    import time
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    ts = TaskStore()
    _in_review(ts, "E-1")
    env = make_verdict(gate="review", task_id="E-1", agent_id="A09", expires_s=10)
    ts.record_verdict("E-1", env)
    assert ts.missing_gate_reasons("E-1") == {}
    boundary = env["payload"]["issued_at"] + 10
    monkeypatch.setattr(time, "time", lambda: boundary)
    assert ts.missing_gate_reasons("E-1") == {"review": "expired"}


def test_absent_and_no_required_gates(swarm_dir):
    from swarm.taskstore import TaskStore
    ts = TaskStore()
    _in_review(ts, "N-1")
    assert ts.missing_gate_reasons("N-1") == {"review": "absent"}
    _in_review(ts, "N-2", gates=())
    assert ts.missing_gate_reasons("N-2") == {}


def test_forged_fail_does_not_force_rework(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.results import reconcile
    ts = TaskStore()
    _in_review(ts, "R-1")
    _raw_row(ts, "R-1", "review", "fail")
    reconcile(ts, "c", lambda *a, **k: None)
    assert ts.get("R-1")["state"] == "IN_REVIEW"


def test_dev_key_event_and_require_key(swarm_dir, monkeypatch):
    from swarm.taskstore import TaskStore
    from swarm.gates import make_verdict
    from swarm.errors import SwarmError
    monkeypatch.delenv("SWARM_SIGNING_KEY", raising=False)
    monkeypatch.delenv("SWARM_ED25519_KEY", raising=False)
    ts = TaskStore()
    _in_review(ts, "K-1")
    ts.record_verdict("K-1", make_verdict(gate="review", task_id="K-1", agent_id="A09"))
    ev = _events(swarm_dir, "security.dev_key")
    assert ev and ev[-1]["payload"] == {"msg_type": "gate.verdict", "source": "A09"}
    monkeypatch.setenv("SWARM_REQUIRE_KEY", "1")
    with pytest.raises(SwarmError, match="SWARM_REQUIRE_KEY=1 but no signing key configured"):
        ts.transition("K-1", "APPROVED")
    monkeypatch.setenv("SWARM_SIGNING_KEY", "real")
    n = len(_events(swarm_dir, "security.dev_key"))
    ts.record_verdict("K-1", make_verdict(gate="review", task_id="K-1", agent_id="A09"))
    assert len(_events(swarm_dir, "security.dev_key")) == n
    assert ts.transition("K-1", "APPROVED")["state"] == "APPROVED"
