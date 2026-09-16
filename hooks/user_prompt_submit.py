#!/usr/bin/env python3
"""Fail-open UserPromptSubmit hook: inject AgentSwarm orchestration on SDLC-shaped prompts.

Skips entirely inside headless swarm agent sessions (SWARM_CHILD=1)."""
from __future__ import annotations
import json
import os
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
        if os.environ.get("SWARM_CHILD") == "1":
            # Headless swarm agent session: never re-inject orchestration or re-kick the swarm.
            print("{}")
        elif classify(prompt):
            # Context only. The detached runner is started once by the all-in-one plugin
            # after Prompt Uplift finishes (src/swarm/kickoff.ts), not here in parallel.
            json.dump({"additionalContext": CONTEXT}, sys.stdout)
        else:
            print("{}")
    except Exception:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
