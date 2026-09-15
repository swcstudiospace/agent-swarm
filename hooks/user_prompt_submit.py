#!/usr/bin/env python3
"""Fail-open UserPromptSubmit hook: inject AgentSwarm orchestration on SDLC-shaped prompts."""
from __future__ import annotations
import json
import re
import sys

POSITIVE = (
    "implement", "feature", "bug", "fix", "refactor", "release", "deploy", "hotfix",
    "requirements", "architecture", "code review", "security audit", "run the swarm",
    "agent-swarm", "a01-orchestrator",
)
NEGATIVE = ("/uplift", "/think", "what is", "explain only")

CONTEXT = """## AgentSwarm orchestration (mandatory)

Load skill `agent-swarm-orchestrate` (path: agent-swarm/skills/orchestrate/SKILL.md or .claude/skills/agent-swarm/orchestrate/SKILL.md).
Do not implement domain work in the parent session.
Spawn subagent `a01-orchestrator` (Claude Agent tool / Grok spawn_subagent subagent_type=a01-orchestrator) with the user request as the brief.
A01 plans with orch_plan.py and spawns a02–a15 by slug.
"""


def extract_prompt(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("prompt", "user_prompt"):
        if isinstance(payload.get(key), str):
            return payload[key]
    inputs = payload.get("inputs")
    if isinstance(inputs, dict) and isinstance(inputs.get("prompt"), str):
        return inputs["prompt"]
    return ""


def classify(prompt: str) -> bool:
    low = prompt.lower()
    if any(n in low for n in NEGATIVE):
        return False
    return any(p in low for p in POSITIVE)


def main() -> int:
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            print("{}")
            return 0
        prompt = extract_prompt(payload)
        cwd = ""
        if isinstance(payload, dict):
            cwd = str(payload.get("cwd") or payload.get("cwd_path") or "")
        if classify(prompt):
            json.dump({"additionalContext": CONTEXT}, sys.stdout)
            try:
                import os
                import subprocess
                from pathlib import Path
                if os.environ.get("AIO_SWARM") != "0":
                    runner = Path(__file__).resolve().parent / "autonomous_run.py"
                    subprocess.Popen(
                        [sys.executable, str(runner), "--cwd", cwd or ".", "--brief", prompt, "--runtime", "auto"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
            except Exception:
                pass
        else:
            print("{}")
    except Exception:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
