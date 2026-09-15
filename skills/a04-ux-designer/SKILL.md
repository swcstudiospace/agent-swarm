---
name: a04-ux-designer
description: >
  A04 UXD UX Designer — Owns design tokens, wireframes, UX specs and accessibility (WCAG) requirements. Use for any UI-facing feature before frontend implementation.
  Use when the user runs /a04-ux-designer, asks for UXD, or the swarm assigns capability ux.tokens, ux.spec, ux.a11y.
disable-model-invocation: false
---

# UX Designer (A04 UXD)

Subagent body: `prompts/A04-ux-designer.md` (do not paste it here). Generated agents: `.claude/agents/a04-ux-designer.md` and `.grok/agents/a04-ux-designer.md`.

## When to Use

- The assignment capability is one of: ux.tokens, ux.spec, ux.a11y.
- The user names this agent, slug `a04-ux-designer`, or code `UXD`.
- Use when the user runs /a04-ux-designer.

Don't use for: Do not implement backend/frontend source.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/ux_tokens.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/ux_tokens.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: requirements.spec, acceptance.criteria, architecture.blueprint.
3. Run the scripts above. Never fabricate JSON.
4. Write only design.system.tokens, ux.spec, a11y.requirements.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
