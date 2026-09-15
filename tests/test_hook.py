import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "user_prompt_submit.py"


def run_hook(prompt: str) -> dict:
    env = {**__import__("os").environ, "AIO_SWARM": "0"}
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True,
        text=True,
        env=env,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout or "{}")


def test_injects_on_feature():
    out = run_hook("implement a new billing feature in the app")
    ctx = out.get("additionalContext", "")
    assert "agent-swarm-orchestrate" in ctx
    assert "a01-orchestrator" in ctx


def test_silent_on_explain():
    out = run_hook("what is a monad")
    assert not out.get("additionalContext")


def test_never_blocks_on_bad_stdin():
    p = subprocess.run([sys.executable, str(HOOK)], input="not-json", capture_output=True, text=True)
    assert p.returncode == 0
