"""CORE-05: safe re-planning — prefix reuse/reject, derived prefixes, atomic plans, runner ambiguity."""
import json
import os
import re
import sqlite3
import subprocess
import sys

from conftest import ROOT, run_script


def _env(tmp_path):
    return {"SWARM_DIR": str(tmp_path / ".swarm")}


def _plan(env, *args):
    return run_script("orch_plan.py", "--json", *args, env=env)


def _counts(tmp_path):
    con = sqlite3.connect(tmp_path / ".swarm" / "tasks.db")
    try:
        return tuple(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("tasks", "transitions"))
    finally:
        con.close()


def _corrs(tmp_path, like):
    con = sqlite3.connect(tmp_path / ".swarm" / "tasks.db")
    try:
        return {r[0] for r in con.execute("SELECT correlation_id FROM tasks WHERE task_id LIKE ?", (like,))}
    finally:
        con.close()


def test_same_prefix_same_brief_reuses(tmp_path):
    env = _env(tmp_path)
    first = _plan(env, "--brief-text", "billing", "--prefix", "R")
    assert first.returncode == 0, first.stdout + first.stderr
    corr = json.loads(first.stdout)["correlation_id"]
    before = _counts(tmp_path)
    latest = (tmp_path / ".swarm" / "latest_correlation").stat().st_mtime_ns
    again = _plan(env, "--brief-text", "billing", "--prefix", "R")
    assert again.returncode == 0, again.stdout + again.stderr
    out = json.loads(again.stdout)
    assert out["correlation_id"] == corr and out["reused"] is True
    assert len(out["tasks"]) == 13
    assert _counts(tmp_path) == before
    assert (tmp_path / ".swarm" / "latest_correlation").stat().st_mtime_ns == latest


def test_same_prefix_other_brief_exit2(tmp_path):
    env = _env(tmp_path)
    assert _plan(env, "--brief-text", "billing", "--prefix", "R").returncode == 0
    before = _counts(tmp_path)
    r = _plan(env, "--brief-text", "something else", "--prefix", "R")
    assert r.returncode == 2
    assert json.loads(r.stdout)["error"]["code"] == "E-CONTRACT"
    assert "--prefix" in r.stdout
    assert "IntegrityError" not in r.stdout + r.stderr
    assert _counts(tmp_path) == before


def test_same_prefix_other_pattern_exit2(tmp_path):
    env = _env(tmp_path)
    assert _plan(env, "--brief-text", "billing", "--prefix", "R", "--pattern", "feature").returncode == 0
    r = _plan(env, "--brief-text", "billing", "--prefix", "R", "--pattern", "hotfix")
    assert r.returncode == 2
    assert json.loads(r.stdout)["error"]["code"] == "E-CONTRACT"


def test_default_prefix_unique(tmp_path):
    env = _env(tmp_path)
    outs = []
    for _ in range(2):
        r = _plan(env, "--brief-text", "billing")
        assert r.returncode == 0, r.stdout + r.stderr
        outs.append(json.loads(r.stdout))
    assert outs[0]["correlation_id"] != outs[1]["correlation_id"]
    for o in outs:
        prefix = "T" + o["correlation_id"].replace("-", "")[:4]
        assert all(re.match(r"^T[0-9a-f]{4}-", t["task_id"]) for t in o["tasks"])
        assert all(t["task_id"].startswith(prefix + "-") for t in o["tasks"])
    assert _counts(tmp_path)[0] == 26


def test_plan_atomic_rollback(tmp_path):
    env = _env(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"tasks": [
        {"id": "a", "capability": "req.spec", "agent": "A02"},
        {"id": "b", "capability": "code.backend", "agent": "A05", "depends_on": ["a"]},
        {"id": "a", "capability": "req.spec", "agent": "A02"},
    ]}))
    corr = "11111111-2222-3333-4444-555555555555"
    r = _plan(env, "--plan", str(plan), "--pattern", "custom", "--prefix", "C", "--correlation-id", corr)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "IntegrityError" not in r.stdout + r.stderr
    assert _corrs(tmp_path, "%") == set()
    assert not (tmp_path / ".swarm" / "plans" / f"{corr}.json").exists()
    assert not (tmp_path / ".swarm" / "latest_correlation").exists()


def test_concurrent_same_prefix_no_integrityerror(tmp_path):
    env = {**os.environ, **_env(tmp_path)}
    # initialise the DB schema first so both racers contend only on plan rows
    assert _plan(_env(tmp_path), "--brief-text", "warmup", "--prefix", "W").returncode == 0
    cmd = [sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--json", "--brief-text", "race", "--prefix", "P"]
    procs = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=ROOT)
             for _ in range(2)]
    results = [(p.wait(timeout=60), *p.communicate()) for p in procs]
    for rc, out, err in results:
        assert rc in (0, 2), out + err
        assert "IntegrityError" not in out + err and "Traceback" not in out + err
    assert len(_corrs(tmp_path, "P-%")) == 1
    assert _counts(tmp_path)[0] == 26


def test_insert_integrityerror_mapped(tmp_path):
    env = _env(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"tasks": [{"id": "be", "capability": "code.backend", "agent": "A05"}]}))
    assert _plan(env, "--plan", str(plan), "--pattern", "custom", "--prefix", "Q").returncode == 0
    r = _plan(env, "--brief-text", "billing", "--prefix", "Q")
    assert r.returncode == 2
    assert json.loads(r.stdout)["error"]["code"] == "E-CONTRACT"
    assert "IntegrityError" not in r.stdout + r.stderr
    assert _counts(tmp_path)[0] == 1


def test_run_ambiguous_correlation_exit2(tmp_path):
    env = _env(tmp_path)
    corrs = [json.loads(_plan(env, "--brief-text", "billing", "--prefix", p).stdout)["correlation_id"] for p in ("A", "B")]
    r = run_script("swarm_run.py", "--dry-run", "--json", env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    err = json.loads(r.stdout)["error"]
    assert err["code"] == "E-INPUT"
    assert all(c in err["message"] for c in corrs)
    r = run_script("swarm_run.py", "--dry-run", "--json", "--correlation-id", corrs[0], env=env)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert json.loads(r.stdout)["correlation_id"] == corrs[0]
    con = sqlite3.connect(tmp_path / ".swarm" / "tasks.db")
    states = {s for (s,) in con.execute("SELECT DISTINCT state FROM tasks WHERE task_id LIKE 'B-%'")}
    con.close()
    assert states == {"PLANNED"}
