"""CORE-01/02/03: one task.result schema, one result path for headless and ingest."""
import ast
import json

from conftest import ROOT, run_script, stub_claude

ONE_TASK = {"tasks": [{"id": "one", "capability": "code.backend", "agent": "A05", "title": "one", "depends_on": [], "gates": []}]}
TWO_TASKS = {"tasks": [{"id": "one", "capability": "code.backend", "agent": "A05", "title": "one", "depends_on": [], "gates": []},
                       {"id": "two", "capability": "code.backend", "agent": "A05", "title": "two", "depends_on": [], "gates": []}]}

GATED = {"tasks": [{"id": "one", "capability": "code.backend", "agent": "A05", "title": "one", "depends_on": [], "risk_class": "low"}]}


def _plan(tmp_path, swarm, plan=ONE_TASK):
    env = {"SWARM_DIR": str(swarm)}
    f = tmp_path / "plan.json"
    f.write_text(json.dumps(plan))
    r = run_script("orch_plan.py", "--plan", str(f), "--prefix", "T", "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return env


def _history(env, tid):
    return json.loads(run_script("orch_status.py", "--history", tid, "--json", env=env).stdout)["history"]


def _ingest(tmp_path, env, result, name="r.json"):
    f = tmp_path / name
    f.write_text(json.dumps(result))
    return run_script("orch_status.py", "--ingest", str(f), "--json", env=env)


def _events(swarm, etype):
    p = swarm / "events.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if f'"{etype}"' in ln] if p.exists() else []


RESULT = {"task_id": "T-one", "state": "IN_REVIEW", "outputs": [{"kind": "code.backend", "uri": "file://x", "version": "1", "digest": ""}],
          "metrics": {"tests": 3}, "summary_md": "ok"}


def test_headless_and_ingest_same_transitions(tmp_path):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    env1 = _plan(tmp_path / "a", d1)
    stub = stub_claude(tmp_path, RESULT)
    r = run_script("swarm_run.py", "--claude-bin", str(stub), "--runtime", "claude", "--once", "--json", env=env1)
    assert r.returncode == 0, r.stdout + r.stderr
    env2 = _plan(tmp_path / "b", d2)
    r = _ingest(tmp_path, env2, RESULT)
    assert r.returncode == 0, r.stdout + r.stderr
    h1 = [h["to_state"] for h in _history(env1, "T-one")]
    h2 = [h["to_state"] for h in _history(env2, "T-one")]
    assert h1 == h2
    assert h1[-6:] == ["PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "APPROVED", "DONE"]


def test_single_result_path():
    tree = ast.parse((ROOT / "scripts" / "swarm_run.py").read_text())
    defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not defs & {"parse_result", "apply_result", "reconcile"}
    for s in ("swarm_run.py", "orch_status.py"):
        t = ast.parse((ROOT / "scripts" / s).read_text())
        imported = {a.name for n in ast.walk(t) if isinstance(n, ast.ImportFrom) and n.module == "swarm.results" for a in n.names}
        assert {"parse_result", "apply_result", "reconcile"} <= imported, s


def test_ingest_full_result(tmp_path, swarm_dir):
    env = _plan(tmp_path, swarm_dir, GATED)
    res = {"task_id": "T-one", "state": "IN_REVIEW", "outputs": [{"kind": "rca", "uri": "file://rca.md", "version": "2", "digest": "d"}],
           "metrics": {"m": 1}, "foo": {"bar": 1}}
    r = _ingest(tmp_path, env, res)
    assert r.returncode == 0, r.stdout
    from swarm.taskstore import TaskStore
    store = TaskStore()
    t = store.get("T-one")
    assert t["state"] == "IN_REVIEW"
    assert t["notes_json"]["result"]["foo"] == {"bar": 1}
    arts = store.conn.execute("SELECT uri FROM artifacts WHERE task_id='T-one'").fetchall()
    assert [a[0] for a in arts] == ["file://rca.md"]


def test_ingest_invalid_schema_exit2(tmp_path, swarm_dir):
    env = _plan(tmp_path, swarm_dir, GATED)
    before = _history(env, "T-one")
    r = _ingest(tmp_path, env, {"task_id": "T-one"})
    assert r.returncode == 2
    assert json.loads(r.stdout)["error"]["code"] == "E-CONTRACT"
    assert _history(env, "T-one") == before
    assert any(e["payload"]["mode"] == "ingest" for e in _events(swarm_dir, "task.result.rejected"))


def test_ingest_twice_rejected(tmp_path, swarm_dir):
    env = _plan(tmp_path, swarm_dir, GATED)
    res = {"task_id": "T-one", "state": "IN_REVIEW"}
    assert _ingest(tmp_path, env, res).returncode == 0
    n = len(_history(env, "T-one"))
    r = _ingest(tmp_path, env, res)
    assert r.returncode == 2 and "E-CONTRACT" in r.stdout
    assert len(_history(env, "T-one")) == n


def test_ingest_terminal_task_rejected(tmp_path, swarm_dir):
    env = _plan(tmp_path, swarm_dir)
    assert _ingest(tmp_path, env, RESULT).returncode == 0
    assert _history(env, "T-one")[-1]["to_state"] == "DONE"
    n, rej = len(_history(env, "T-one")), len(_events(swarm_dir, "task.result.rejected"))
    r = _ingest(tmp_path, env, RESULT)
    assert r.returncode == 2 and json.loads(r.stdout)["error"]["code"] == "E-CONTRACT"
    ev = _events(swarm_dir, "task.result.rejected")
    assert len(ev) == rej + 1 and ev[-1]["payload"]["mode"] == "ingest"
    assert len(_history(env, "T-one")) == n


def test_schema_single_source(swarm_dir):
    files = [p for p in ROOT.rglob("task.result*.json") if ".swarm" not in p.parts]
    assert len(files) == 1
    from swarm.schema import load_schema, validate
    import importlib.util
    spec = importlib.util.spec_from_file_location("swarm_run_mod", ROOT / "scripts" / "swarm_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from swarm.results import parse_result
    schema = load_schema("task.result.v1")
    agent = {"id": "A05"}
    gate = {"task_id": "T-rev", "capability": "review", "title": "r", "notes_json": {"gate": "review", "gate_for": ["T-be"]}}
    plain = {"task_id": "T-be", "capability": "code.backend", "title": "b", "notes_json": {}}
    for t in (gate, plain):
        assert validate(parse_result(mod.canned_result(t, agent)), schema) == []
    assert validate({"state": "IN_REVIEW"}, schema)
    assert validate({"task_id": "x", "state": "DONE"}, schema)
    errs = validate({"task_id": "x", "state": "IN_REVIEW", "outputs": "nope"}, schema)
    assert errs and any("/outputs" in e for e in errs)


def _headless(tmp_path, swarm, result, plan=ONE_TASK, once=False):
    env = _plan(tmp_path, swarm, plan)
    stub = stub_claude(tmp_path, result)
    args = ["--claude-bin", str(stub), "--runtime", "claude", "--json"] + (["--once"] if once else [])
    run_script("swarm_run.py", *args, env=env)
    return env


def test_invalid_result_fails_contract_and_retries(tmp_path, swarm_dir):
    env = _headless(tmp_path, swarm_dir, {"task_id": "T-one"})
    hist = _history(env, "T-one")
    failed = [h for h in hist if h["to_state"] == "FAILED"]
    assert len(failed) == 3 and all(h["reason"].startswith("E-CONTRACT") for h in failed)
    assert hist[-1]["to_state"] == "ESCALATED"
    ev = [e for e in _events(swarm_dir, "task.result.rejected") if e["payload"]["mode"] == "headless"]
    assert len(ev) >= 3


def test_headless_in_progress_is_contract_failure(tmp_path, swarm_dir):
    env = _headless(tmp_path, swarm_dir, {"task_id": "T-one", "state": "IN_PROGRESS"}, once=True)
    failed = [h for h in _history(env, "T-one") if h["to_state"] == "FAILED"]
    assert failed and "session ended in IN_PROGRESS" in failed[0]["reason"]


def test_task_id_mismatch_rejected(tmp_path, swarm_dir):
    plan = {"tasks": [TWO_TASKS["tasks"][0], {**TWO_TASKS["tasks"][1], "depends_on": ["one"]}]}
    env = _headless(tmp_path, swarm_dir, {"task_id": "T-two", "state": "IN_REVIEW"}, plan=plan, once=True)
    failed = [h for h in _history(env, "T-one") if h["to_state"] == "FAILED"]
    assert failed and failed[0]["reason"].startswith("E-CONTRACT") and "mismatch" in failed[0]["reason"]
    assert _history(env, "T-two")[-1]["to_state"] == "PLANNED"
