"""WR-12 / WR-08: headless agent sessions get no signing keys, the key-holding runner runs each gate script,
and a gate task cannot finish without its script's verdicts."""
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import run_script

ROOT = Path(__file__).resolve().parent.parent
KEY_VARS = ("SWARM_SIGNING_KEY", "SWARM_ED25519_KEY", "SWARM_REQUIRE_KEY")
# inherited vars that would change what the runner or its gate scripts do
_ENV_NOISE = (*KEY_VARS, "SWARM_AGENT_SESSION", "SWARM_CHILD", "SWARM_TASK_ID", "SWARM_CORRELATION_ID",
              "SWARM_DRYRUN_FAIL", "SWARM_RUNTIME", "ANTHROPIC_API_KEY")


def _clean_env(**extra) -> dict:
    return {**{k: v for k, v in os.environ.items() if k not in _ENV_NOISE}, **extra}


def _load_swarm_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    spec = importlib.util.spec_from_file_location("swarm_run_under_test", ROOT / "scripts" / "swarm_run.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(mod)
    return mod


def _plan(env, prefix="X"):
    r = run_script("orch_plan.py", "--brief-text", "x", "--pattern", "feature", "--prefix", prefix, env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def _lease(ts, tid, **notes):
    for s in ("CLAIMED", "IN_PROGRESS"):
        ts.transition(tid, s)
    if notes:
        ts.set_notes(tid, **notes)


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


# ---------------------------------------------------------------- WR-12: no key material in agent sessions
@pytest.mark.parametrize("runtime", ["claude", "grok"])
def test_headless_child_env_has_no_keys(tmp_path, monkeypatch, runtime):
    monkeypatch.setenv("SWARM_SIGNING_KEY", "s")
    monkeypatch.setenv("SWARM_ED25519_KEY", "11" * 32)
    monkeypatch.setenv("SWARM_REQUIRE_KEY", "1")
    mod = _load_swarm_run(tmp_path, monkeypatch)
    seen = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        return SimpleNamespace(stdout='{"result":"ok"}', stderr="", returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    args = SimpleNamespace(runtime=runtime, claude_bin="claude", grok_bin="grok", permission_mode="bypassPermissions",
                           max_turns=5, model="", allowed_tools="", task_timeout=10)
    mod.run_agent_headless({"slug": "a09-reviewer", "id": "A09"}, "review the thing", tmp_path, args)
    env = seen["env"]
    assert not set(KEY_VARS) & set(env)
    assert env["SWARM_AGENT_SESSION"] == "1"
    assert env["SWARM_CHILD"] == "1"
    assert os.environ["SWARM_SIGNING_KEY"] == "s"  # the runner keeps its own key


_STUB = r'''#!@PY@
import json, os, re, sys
if sys.argv[1:3] == ["auth", "status"]:
    print('{"loggedIn": true}')
    sys.exit(0)
prompt = sys.stdin.read()
tid = re.search(r'"task_id": "([^"]+)"', prompt).group(1)
with open(@DUMP@, "a") as fh:
    fh.write(json.dumps({"task_id": tid, "has_hmac": "SWARM_SIGNING_KEY" in os.environ,
                         "has_ed": "SWARM_ED25519_KEY" in os.environ,
                         "agent_session": os.environ.get("SWARM_AGENT_SESSION")}) + "\n")
res = {"task_id": tid, "state": "IN_REVIEW", "summary_md": "stub session: ran no script"}
if '"gate": "quality"' in prompt:
    res.update(gate="quality", verdicts={"T-be": {"verdict": "pass", "findings": []}})
print(json.dumps({"result": "done\n```json\n" + json.dumps(res) + "\n```"}))
'''


def test_runner_records_gate_after_session(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    dump = tmp_path / "env-dump.jsonl"
    stub = tmp_path / "claude-stub"
    stub.write_text(_STUB.replace("@PY@", sys.executable).replace("@DUMP@", repr(str(dump))))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"tasks": [
        {"id": "be", "capability": "code.backend", "agent": "A05", "gates": ["quality"]},
        {"id": "qa", "capability": "gate.quality", "agent": "A08", "depends_on": ["be"],
         "gates": {"gate": "quality", "for": ["be"]}},
    ]}))
    swarm = tmp_path / ".swarm"
    env = _clean_env(SWARM_DIR=str(swarm), SWARM_SIGNING_KEY="runner-secret")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--plan", str(plan), "--prefix", "T",
                        "--repo", str(work), "--json"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "swarm_run.py"), "--claude-bin", str(stub),
                        "--runtime", "claude", "--repo", str(work), "--json"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1500:]
    assert json.loads(r.stdout)["complete"] is True
    sessions = [json.loads(ln) for ln in dump.read_text().splitlines()]
    assert {s["task_id"] for s in sessions} == {"T-be", "T-qa"}
    assert all(not s["has_hmac"] and not s["has_ed"] and s["agent_session"] == "1" for s in sessions)
    rows = [x for x in _rows(swarm, "T-be") if x["gate"] == "quality"]
    assert rows, "the runner recorded no quality verdict on T-be"
    row = rows[-1]
    assert row["agent_id"] == "A08@local"
    envelope = json.loads(row["envelope_json"])
    assert envelope["payload"]["gate_task"] == "T-qa"
    for k in KEY_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SWARM_SIGNING_KEY", "runner-secret")
    from swarm.gates import validate_verdict
    assert validate_verdict(envelope)["verdict"] == "pass"


_REV_STUB = r'''#!@PY@
import json, re, sys
if sys.argv[1:3] == ["auth", "status"]:
    print('{"loggedIn": true}')
    sys.exit(0)
prompt = sys.stdin.read()
tid = re.search(r'"task_id": "([^"]+)"', prompt).group(1)
if '"gate": "review"' not in prompt:
    res = {"task_id": tid, "state": "IN_REVIEW", "summary_md": "built"}
elif @MODE@ == "crash":
    print(json.dumps({"is_error": True, "result": "API Error: overloaded"}))
    sys.exit(1)
else:
    res = {"task_id": tid, "state": "BLOCKED", "needs": "human-approval: diff touches prod IAM policy"}
print(json.dumps({"result": "done\n```json\n" + json.dumps(res) + "\n```"}))
'''


@pytest.mark.parametrize("mode", ["crash", "blocked"])
def test_failed_gate_session_records_no_verdict(tmp_path, mode):
    """CR-04: a review session that crashed or reported BLOCKED must not satisfy the target's review gate."""
    work = tmp_path / "work"
    work.mkdir()
    stub = tmp_path / "claude-stub"
    stub.write_text(_REV_STUB.replace("@PY@", sys.executable).replace("@MODE@", repr(mode)))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"tasks": [
        {"id": "be", "capability": "code.backend", "agent": "A05", "gates": ["review"]},
        {"id": "rev", "capability": "gate.review", "agent": "A09", "depends_on": ["be"],
         "gates": {"gate": "review", "for": ["be"]}},
    ]}))
    swarm = tmp_path / ".swarm"
    env = _clean_env(SWARM_DIR=str(swarm), SWARM_SIGNING_KEY="runner-secret")
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "orch_plan.py"), "--plan", str(plan), "--prefix", "S",
                        "--risk-class", "low", "--repo", str(work), "--json"], capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "swarm_run.py"), "--claude-bin", str(stub),
                        "--runtime", "claude", "--repo", str(work), "--json"],
                       capture_output=True, text=True, env=env, cwd=ROOT, timeout=600)
    assert r.returncode in (0, 1), r.stdout[-2000:] + r.stderr[-1500:]
    assert [x for x in _rows(swarm, "S-be") if x["gate"] == "review"] == []
    con = sqlite3.connect(swarm / "tasks.db")
    states = dict(con.execute("SELECT task_id, state FROM tasks"))
    con.close()
    assert states["S-be"] == "IN_REVIEW"
    assert states["S-rev"] == ("ESCALATED" if mode == "crash" else "BLOCKED")


def test_agent_session_gate_script_records_nothing(swarm_dir):
    from swarm.taskstore import TaskStore
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    _lease(TaskStore(), "X-qa", dry_run=True)  # leased and flagged exactly as a runner dry-run would
    r = run_script("qa_gate.py", "--dry-run", "--task-id", "X-qa", "--json", env={**env, "SWARM_AGENT_SESSION": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (swarm_dir / "verdicts" / "X-qa.quality.json").exists()
    assert _rows(swarm_dir) == []
    assert any("agent session" in e["payload"]["reason"] for e in _events(swarm_dir, "gate.verdict.unrecorded"))


# ---------------------------------------------------------------- WR-08: a gate task cannot finish without its script
_GATE_RESULT = {"gate": "quality", "state": "IN_REVIEW"}


def _gate_pair(ts, prefix, *, lease=True):
    """Target <prefix>-be plus quality gate task <prefix>-qa (gate_for [<prefix>-be]), the gate task leased by A01."""
    ts.create(task_id=f"{prefix}-be", correlation_id="c", capability="code.backend", notes={"gates": ["quality"]})
    ts.create(task_id=f"{prefix}-qa", correlation_id="c", capability="gate.quality",
              notes={"gate": "quality", "gate_for": [f"{prefix}-be"], "gates": []})
    if lease:
        for s in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"):
            ts.transition(f"{prefix}-qa", s)
    return {**_GATE_RESULT, "task_id": f"{prefix}-qa", "verdicts": {f"{prefix}-be": {"verdict": "pass", "findings": []}}}


def test_ingest_gate_result_without_script_rejected(tmp_path, swarm_dir):
    from swarm.taskstore import TaskStore
    env = {"SWARM_DIR": str(swarm_dir)}
    _plan(env)
    ts = TaskStore()
    _lease(ts, "X-qa")
    f = tmp_path / "r.json"
    f.write_text(json.dumps({**_GATE_RESULT, "task_id": "X-qa", "verdicts": {"X-be": {"verdict": "pass", "findings": []}}}))
    r = run_script("orch_status.py", "--ingest", str(f), "--json", env=env)
    assert r.returncode == 2 and "gate script not run" in r.stdout, r.stdout + r.stderr
    assert ts.get("X-qa")["state"] == "IN_PROGRESS"
    ts.set_notes("X-qa", dry_run=True)
    g = run_script("qa_gate.py", "--dry-run", "--task-id", "X-qa", "--json", env=env)
    assert g.returncode == 0, g.stdout + g.stderr
    r = run_script("orch_status.py", "--ingest", str(f), "--json", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ts.get("X-qa")["state"] in ("IN_REVIEW", "DONE")


def test_headless_gate_without_rows_fails_attempt(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.results import apply_result
    ts = TaskStore()
    res = _gate_pair(ts, "H")
    events = []
    out = apply_result(ts, ts.get("H-qa"), agent_id="A08", result=res, meta={}, mode="headless",
                       emit=lambda t, p: events.append((t, p)))
    assert out == "FAILED"
    assert ts.history("H-qa")[-1]["reason"].startswith("E-CONTRACT: gate script not run")
    assert [p["mode"] for t, p in events if t == "task.result.rejected"] == ["headless"]


def test_gate_task_without_claimed_row_rejected(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.results import apply_result
    from swarm.verdicts import record_gate_verdicts
    from swarm.errors import SwarmError, ErrorCode
    ts = TaskStore()
    res = _gate_pair(ts, "N", lease=False)
    ts.conn.execute("UPDATE tasks SET state='IN_PROGRESS' WHERE task_id='N-qa'")  # no CLAIMED row in its history
    ts.conn.commit()
    record_gate_verdicts(ts, gate_task_id="N-qa", gate="quality", agent_id="A08@local", findings=[], runs={},
                         correlation_id="c", expires_s=60, emit=lambda *a, **k: None)
    assert ts.latest_verdicts("N-be", verified_only=True)["quality"]["verdict"] == "pass"
    with pytest.raises(SwarmError) as e:
        apply_result(ts, ts.get("N-qa"), agent_id="A08", result=res, meta={}, mode="ingest", emit=lambda *a: None)
    assert e.value.code is ErrorCode.E_CONTRACT and "gate script not run" in str(e.value)
    assert ts.get("N-qa")["state"] == "IN_PROGRESS"


def test_reconcile_escalates_stalled_gate(swarm_dir):
    from swarm.taskstore import TaskStore
    from swarm.results import reconcile
    ts = TaskStore()
    steps = ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW")
    ts.create(task_id="S-be", correlation_id="c", capability="code.backend", notes={"gates": ["review"]})
    for s in steps:
        ts.transition("S-be", s)
    events = []

    def emit(t, p):
        events.append((t, p))

    reconcile(ts, "c", emit)
    reconcile(ts, "c", emit)
    esc = [p for t, p in events if t == "escalation.request"]
    assert len(esc) == 1
    assert esc[0]["task_id"] == "S-be" and esc[0]["evidence"] == ["review:absent"]
    assert ts.get("S-be")["state"] == "IN_REVIEW"  # A01 or a human decides
    # a live gate task that will issue the gate: waiting, not a stall
    ts.create(task_id="D-be", correlation_id="d", capability="code.backend", notes={"gates": ["review"]})
    for s in steps:
        ts.transition("D-be", s)
    ts.create(task_id="D-rev", correlation_id="d", capability="gate.review",
              notes={"gate": "review", "gate_for": ["D-be"], "gates": []})
    for s in ("VALIDATED", "PLANNED"):
        ts.transition("D-rev", s)
    events.clear()
    reconcile(ts, "d", emit)
    assert [p for t, p in events if t == "escalation.request"] == []
