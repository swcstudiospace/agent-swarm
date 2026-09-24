"""Shared fixtures: isolated SWARM_DIR, script runner, stub claude CLI."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def swarm_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    # reload modules that cache SWARM_DIR at import time
    for m in [m for m in list(sys.modules) if m.startswith("swarm")]:
        del sys.modules[m]
    return tmp_path / ".swarm"


def run_script(name, *args, env=None):
    cmd = [sys.executable, str(ROOT / "scripts" / name), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})}, cwd=ROOT)


def stub_claude(tmp_path, result: dict) -> Path:
    """Executable standing in for `claude`: `auth status` → logged in; `-p` → result in a fenced json block."""
    out = json.dumps({"result": "done\n```json\n" + json.dumps(result) + "\n```"})
    p = tmp_path / "claude-stub"
    p.write_text(f"""#!{sys.executable}
import sys
if sys.argv[1:3] == ["auth", "status"]:
    print('{{"loggedIn": true}}')
elif "-p" in sys.argv:
    sys.stdin.read()
    print({out!r})
""")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p
