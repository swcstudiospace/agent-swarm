import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "user_prompt_submit.py"
# D-10: the one prompt list both the TS and the Python classifier suites read.
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "classifier_prompts.json").read_text())


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


@pytest.mark.parametrize("prompt", FIXTURE["positive"])
def test_fixture_positive_injects(prompt):
    ctx = run_hook(prompt).get("additionalContext", "")
    assert "agent-swarm-orchestrate" in ctx
    assert "a01-orchestrator" in ctx


@pytest.mark.parametrize("prompt", FIXTURE["negative"])
def test_fixture_negative_silent(prompt):
    """D-09 narrowed classifier: questions, trivial edits, slash/marker prompts and blanks get nothing."""
    assert not run_hook(prompt).get("additionalContext")


def test_silent_on_slash():
    out = run_hook("/uplift last")
    assert not out.get("additionalContext")


def test_never_blocks_on_bad_stdin():
    p = subprocess.run([sys.executable, str(HOOK)], input="not-json", capture_output=True, text=True)
    assert p.returncode == 0
