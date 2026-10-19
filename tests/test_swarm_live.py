"""scripts/swarm_live.py: the AgentScript twin that prints the live run table / swarm.live.v1 document."""
import json
from pathlib import Path

import pytest

from conftest import run_script

ROOT = Path(__file__).resolve().parent.parent


def _seed(tmp_path):
    """A real Task Store with one running and one queued task, in tmp_path/.swarm (the transitions refresh live.json)."""
    from swarm.taskstore import TaskStore
    store = TaskStore(root=tmp_path)
    store.create(task_id="T-run", correlation_id="c1", capability="code.backend", agent_id="a05-backend")
    store.create(task_id="T-q", correlation_id="c1", capability="qa", agent_id="a08-qa")
    for st in ("VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS"):
        store.transition("T-run", st)
    for st in ("VALIDATED", "PLANNED"):
        store.transition("T-q", st)


def _run(tmp_path, *args):
    env = {"SWARM_DIR": str(tmp_path / ".swarm")}
    return run_script("swarm_live.py", "--root", str(tmp_path), *args, env=env)


def test_dry_run_json_exits_zero_with_a_valid_canned_document(tmp_path):
    r = _run(tmp_path, "--json", "--dry-run")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["status"] == "ok" and out["source"] == "dry-run" and out["script"] == "swarm_live"
    from swarm.schema import load_schema, validate
    assert validate(out["live"], load_schema("swarm.live.v1")) == []
    assert not (tmp_path / ".swarm" / "tasks.db").exists() and not (tmp_path / ".swarm" / "live.json").exists()


def test_json_reports_runs_derived_from_the_task_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    _seed(tmp_path)
    (tmp_path / ".swarm" / "live.json").unlink()  # force the derive path
    r = _run(tmp_path, "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["source"] == "task-store"
    assert {x["task_id"]: x["state"] for x in out["live"]["runs"]} == {"T-run": "running", "T-q": "queued"}


def test_text_mode_prints_a_compact_table(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    _seed(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "a05-backend" in r.stdout and "running" in r.stdout and "queued" in r.stdout and "1 run" in r.stdout


def test_fresh_live_json_is_preferred_and_a_stale_one_is_not(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    _seed(tmp_path)  # the observer wrote live.json just now
    assert json.loads(_run(tmp_path, "--json").stdout)["source"] == "live.json"
    live = tmp_path / ".swarm" / "live.json"
    doc = json.loads(live.read_text())
    doc["updated_wall"] -= 3600
    live.write_text(json.dumps(doc))
    assert json.loads(_run(tmp_path, "--json").stdout)["source"] == "task-store"


def test_watch_json_prints_one_compact_document_per_tick(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    _seed(tmp_path)
    r = _run(tmp_path, "--json", "--watch", "0.05", "--ticks", "2")
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith('{"schema"')]
    assert len(lines) == 2 and all(json.loads(ln)["schema"] == "swarm.live.v1" for ln in lines)


@pytest.mark.parametrize("args", [("--watch", "0"), ("--watch", "-3"), ("--watch", "1", "--ticks", "0")])
def test_invalid_watch_arguments_exit_2_naming_the_flag(tmp_path, args):
    r = _run(tmp_path, "--json", *args)
    assert r.returncode == 2
    assert "--watch" in r.stdout or "--ticks" in r.stdout


def test_flags_are_exact_no_abbreviation(tmp_path):
    assert _run(tmp_path, "--json", "--wat", "1").returncode == 2


def test_ts_twin_is_a_thin_passthrough():
    src = (ROOT / "scripts" / "ts" / "swarm_live.ts").read_text()
    assert 'passthrough("swarm_live")' in src and "passthrough.ts" in src
