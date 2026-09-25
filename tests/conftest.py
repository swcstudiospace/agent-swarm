"""Shared fixtures: isolated SWARM_DIR, script runner, stub claude and omp CLIs."""
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


_OMP_STUB = r'''#!@PY@
import json, os, re, sys
prompt = sys.stdin.read()
with open(@CALLS@, "a") as fh:
    fh.write(json.dumps({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd()}) + "\n")
res = dict(@RESULT@)
m = re.search(r'"task_id": "([^"]+)"', prompt)
if "task_id" not in res and m:
    res["task_id"] = m.group(1)
call = {"type": "toolCall", "id": "toolu_1", "name": "yield", "arguments": {"data": res}}
asst = {"role": "assistant", "content": [{"type": "text", "text": "done"}, call], "stopReason": "toolUse",
        "usage": {"cost": {"total": 0.01}}}
tres = {"role": "toolResult", "toolCallId": "toolu_1", "toolName": "yield", "isError": False,
        "details": {"data": res, "status": "success"}}
print("omp: stub banner (not json)")
for ev in ({"type": "session", "version": 3, "id": "stub-session", "cwd": os.getcwd()}, {"type": "agent_start"},
           {"type": "turn_end", "message": asst, "toolResults": [tres]},
           {"type": "agent_end", "messages": [{"role": "user", "content": prompt}, asst, tres],
            "isTerminal": True, "yielded": True}):
    print(json.dumps(ev))
'''


def stub_omp(tmp_path, result: dict, name: str = "omp") -> Path:
    """Executable `<tmp>/omp-bin/<name>` standing in for `omp -p --mode json`: appends {argv, env, cwd} to
    omp_calls(stub), then prints a JSONL stream whose terminal agent_end yields `result` (task_id taken from the
    prompt when `result` has none). Its directory holds no claude, so it can be the whole PATH."""
    d = tmp_path / "omp-bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(_OMP_STUB.replace("@PY@", sys.executable).replace("@CALLS@", repr(str(d / "calls.jsonl")))
                 .replace("@RESULT@", repr(result)))
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def omp_calls(stub: Path) -> list[dict]:
    f = stub.parent / "calls.jsonl"
    return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []
