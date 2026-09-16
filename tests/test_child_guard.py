"""Headless swarm agents must never re-run Prompt Uplift or re-kick the swarm."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "user_prompt_submit.py"


def _load_swarm_run(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    spec = importlib.util.spec_from_file_location("swarm_run_under_test", ROOT / "scripts" / "swarm_run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(mod)
    return mod


def test_headless_agent_env_marks_child(tmp_path, monkeypatch):
    mod = _load_swarm_run(tmp_path, monkeypatch)
    seen = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw["env"]
        return SimpleNamespace(stdout='{"result":"ok"}', stderr="", returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    args = SimpleNamespace(runtime="claude", claude_bin="claude", permission_mode="bypassPermissions",
                           max_turns=5, model="", allowed_tools="", task_timeout=10)
    mod.run_agent_headless({"slug": "a05-backend", "id": "A05"}, "implement the thing", tmp_path, args)
    env = seen["env"]
    assert env["AIO_UPLIFT"] == "0"
    assert env["AIO_SWARM"] == "0"
    assert env["SWARM_CHILD"] == "1"


def test_hook_silent_inside_swarm_child():
    env = {**os.environ, "AIO_SWARM": "0", "SWARM_CHILD": "1"}
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"prompt": "implement a billing feature"}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0
    assert not json.loads(p.stdout or "{}").get("additionalContext")


def test_hook_does_not_spawn_runner():
    src = HOOK.read_text()
    assert "Popen" not in src and "autonomous_run" not in src
