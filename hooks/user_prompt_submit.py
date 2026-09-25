#!/usr/bin/env python3
"""Fail-open UserPromptSubmit hook: inject AgentSwarm orchestration on SDLC-shaped prompts.

classify() mirrors omp/src/hooks.ts classifyPrompt (D-10); tests/fixtures/classifier_prompts.json pins both.
Skips entirely inside headless swarm agent sessions (SWARM_CHILD=1)."""
from __future__ import annotations
import json
import os
import re
import sys

# Prompts containing these (case-insensitive) are owned by other plugins or explicitly opt out.
NEGATIVE = ("/uplift", "/think", "explain only", "/all-in-one:")

# Tags the /swarm dispatch prompt so the hook never re-steers the swarm's own dispatch.
DISPATCH_MARKER = "[agent-swarm:dispatch]"

# re.ASCII: JavaScript's \b and \w are ASCII-only, so the omp classifier sees "fixé" as "fix" + "é"; without the
# flag Python's Unicode \b would not, and the two runtimes would disagree on accented prompts (D-10 parity).
QUESTION = re.compile(r"^(what|why|how|when|where|who|which|is|are|can|could|does|do|should|explain|describe)\b", re.I | re.A)
TRIVIAL = re.compile(r"\btypos?\b|^rename\b", re.I | re.A)
SDLC = re.compile(
    r"\b(build|implement|add|create|fix|refactor|migrate|deploy|release|ship|write tests?|set up|setup|integrate|scaffold|upgrade)\b",
    re.I | re.A,
)

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
    """True only for SDLC-shaped prompts (D-09): silent on empty, slash, system-reminder, opt-out tokens,
    the dispatch marker, questions/explanations and trivial edits."""
    trimmed = prompt.strip()
    if not trimmed or trimmed.startswith("/") or trimmed.startswith("<system-reminder"):
        return False
    low = trimmed.lower()
    if DISPATCH_MARKER in low or any(n in low for n in NEGATIVE):
        return False
    if trimmed.endswith("?") or QUESTION.search(trimmed) or TRIVIAL.search(trimmed):
        return False
    return SDLC.search(trimmed) is not None


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
