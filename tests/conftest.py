"""Shared fixtures: isolated SWARM_DIR, script runner, stub claude and omp CLIs, omp workspace-install dirs."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from install_helpers import _ENV_KEYS, _yaml  # noqa: E402  (after ROOT is on sys.path, as the installer expects)


@pytest.fixture()
def swarm_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    # reload modules that cache SWARM_DIR at import time
    for m in [m for m in list(sys.modules) if m.startswith("swarm")]:
        del sys.modules[m]
    return tmp_path / ".swarm"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return h


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w.resolve()


@pytest.fixture(params=["pyyaml", "stdlib"])
def yaml_mode(request, monkeypatch):
    """Run with PyYAML validating, and on the canonical stdlib-only path (PyYAML import blocked)."""
    if request.param == "stdlib":
        monkeypatch.setitem(sys.modules, "yaml", None)
    elif _yaml is None:
        pytest.skip("PyYAML not installed")
    return request.param


_TREE = ("scripts", "swarm", "prompts", "agents.json", ".claude", ".grok", "omp", "skills", "hooks")


@pytest.fixture()
def tree(tmp_path):
    """A copy of the checkout the installer runs from; build_agents.py resolves ROOT from its own path."""
    t = tmp_path / "checkout"
    ignore = shutil.ignore_patterns("__pycache__", "node_modules")
    for name in _TREE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, t / name, ignore=ignore, symlinks=True)
        else:
            t.mkdir(exist_ok=True)
            shutil.copy2(src, t / name)
    return t.resolve()


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
import json, os, re, subprocess, sys, time
prompt = sys.stdin.read()
with open(@CALLS@, "a") as fh:
    fh.write(json.dumps({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd(), "stdin": prompt}) + "\n")
if @LOAD_ERROR_ON@ is not None and @LOAD_ERROR_ON@ in prompt:
    # omp 18.3.1 when the -e package fails to load: this stderr line, then the session runs on without it (rc 0)
    ext = sys.argv[sys.argv.index("-e") + 1]
    print(f"Failed to load extension {ext}/src/index.ts: Failed to load extension: Failed to parse extension source "
          f"for dependency rewriting: {ext}/src/index.ts: Unexpected token (3:0)", file=sys.stderr, flush=True)
if @HANG@:
    # a tool process of the session, then a session that outlives any --task-timeout
    gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(@GRANDCHILD@, "w") as fh:
        fh.write(str(gc.pid))
    print(json.dumps({"type": "session", "version": 3, "id": "stub-session", "cwd": os.getcwd()}), flush=True)
    time.sleep(120)
res = dict(@RESULT@)
m = re.search(r'"task_id": "([^"]+)"', prompt)
if "task_id" not in res and m:
    res["task_id"] = m.group(1)
if @YIELD_ERROR@ is not None and @YIELD_ERROR_ON@ in prompt:
    # the final text still carries a result block, but the yield itself failed
    call = {"type": "toolCall", "id": "toolu_1", "name": "yield", "arguments": {"error": @YIELD_ERROR@}}
    text = "done\n```json\n" + json.dumps(res) + "\n```"
    tres = {"role": "toolResult", "toolCallId": "toolu_1", "toolName": "yield", "isError": False,
            "details": {"status": "aborted", "error": @YIELD_ERROR@}}
else:
    call = {"type": "toolCall", "id": "toolu_1", "name": "yield", "arguments": {"data": res}}
    text = "done"
    tres = {"role": "toolResult", "toolCallId": "toolu_1", "toolName": "yield", "isError": False,
            "details": {"data": res, "status": "success"}}
asst = {"role": "assistant", "content": [{"type": "text", "text": text}, call], "stopReason": "toolUse",
        "usage": {"cost": {"total": 0.01}}}
print("omp: stub banner (not json)")
for ev in ({"type": "session", "version": 3, "id": "stub-session", "cwd": os.getcwd()}, {"type": "agent_start"},
           {"type": "turn_end", "message": asst, "toolResults": [tres]},
           {"type": "agent_end", "messages": [{"role": "user", "content": prompt}, asst, tres],
            "isTerminal": True, "yielded": True}):
    print(json.dumps(ev))
'''


def stub_omp(tmp_path, result: dict, name: str = "omp", *, yield_error: str | None = None, yield_error_on: str = "",
             hang: bool = False, load_error_on: str | None = None) -> Path:
    """Executable `<tmp>/omp-bin/<name>` standing in for `omp -p --mode json`: appends {argv, env, cwd, stdin} to
    omp_calls(stub), then prints a JSONL stream whose terminal agent_end yields `result` (task_id taken from the
    prompt when `result` has none). Its directory holds no claude, so it can be the whole PATH.
    yield_error: sessions whose prompt contains `yield_error_on` put `result` in a fenced json block of the final
    text and yield {error} instead (status aborted). hang: the session starts a `sleep` grandchild (pid in
    omp_grandchild(stub)), prints a session event and sleeps 120 s. load_error_on: sessions whose prompt contains it
    print omp's `Failed to load extension …` stderr line for the -e package, then run normally."""
    d = tmp_path / "omp-bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(_OMP_STUB.replace("@PY@", sys.executable).replace("@CALLS@", repr(str(d / "calls.jsonl")))
                 .replace("@GRANDCHILD@", repr(str(d / "grandchild.pid"))).replace("@HANG@", repr(hang))
                 .replace("@YIELD_ERROR_ON@", repr(yield_error_on)).replace("@YIELD_ERROR@", repr(yield_error))
                 .replace("@LOAD_ERROR_ON@", repr(load_error_on))
                 .replace("@RESULT@", repr(result)))
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def omp_grandchild(stub: Path) -> int | None:
    f = stub.parent / "grandchild.pid"
    return int(f.read_text()) if f.exists() else None


def omp_calls(stub: Path) -> list[dict]:
    f = stub.parent / "calls.jsonl"
    return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []
