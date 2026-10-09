"""Headless swarm agents must never re-run Prompt Uplift or re-kick the swarm."""
import importlib.util
import io
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
    # ChildGroup patches this module's Popen. The dead-pid spawn has to use the real one, or it re-enters the double.
    real_popen = subprocess.Popen

    class FakePopen:
        """The runner reads the session's pipes while it is alive, so a double has to be a pipe, not `communicate`."""

        def __init__(self, cmd, **kw):
            seen["env"] = kw["env"]
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(b'{"result":"ok"}')
            self.stderr = io.BytesIO()
            self.returncode = 0
            # pid 0 is this process's own group: a shutdown would SIGTERM the test run. A pid that has already
            # exited is ESRCH, which the runner treats as "the group is gone".
            dead = real_popen([sys.executable, "-c", "pass"])
            dead.wait()
            self.pid = dead.pid

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(mod.subprocess, "Popen", FakePopen)
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


_SPAWN_TRAP = r"""
import os, runpy, subprocess, sys

def trap(name):
    def _boom(*a, **kw):
        sys.stderr.write(f"SPAWN:{name}\n")
        raise RuntimeError(name)
    return _boom

subprocess.Popen = trap("Popen")
for fn in ("fork", "posix_spawn", "posix_spawnp", "system", "execv", "execve", "execvp", "execvpe", "spawnv", "spawnve"):
    if hasattr(os, fn):
        setattr(os, fn, trap(fn))
runpy.run_path(sys.argv[1], run_name="__main__")
"""


def test_hook_does_not_spawn_runner():
    """The hook only writes context: with every process-creation primitive trapped, a positive prompt still
    gets its additionalContext and no primitive is reached (a spawn attempt would trip fail-open to `{}`)."""
    env = {**os.environ, "SWARM_CHILD": "0"}
    p = subprocess.run([sys.executable, "-c", _SPAWN_TRAP, str(HOOK)], input=json.dumps({"prompt": "implement a billing feature"}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0
    assert "SPAWN:" not in p.stderr
    assert "agent-swarm-orchestrate" in json.loads(p.stdout).get("additionalContext", "")
