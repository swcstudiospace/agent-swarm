---
name: a02-requirements
description: >
  A02 REQ Requirements Engineer — Turns briefs into a requirements spec, user stories and machine-checkable acceptance criteria (Given/When/Then). Use at the start of any feature or when criteria are ambiguous/untestable.
  Use when the user runs /a02-requirements, asks for REQ, or the swarm assigns capability req.elicit, req.spec, req.acceptance.
disable-model-invocation: false
---

# Requirements Engineer (A02 REQ)

Subagent body: `prompts/A02-requirements.md` (do not paste it here). Generated agents: `.claude/agents/a02-requirements.md` and `.grok/agents/a02-requirements.md`.

## When to Use

- The assignment capability is one of: req.elicit, req.spec, req.acceptance.
- The user names this agent, slug `a02-requirements`, or code `REQ`.
- Use when the user runs /a02-requirements.

Don't use for: Do not design architecture or write product code.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/req_lint.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/req_lint.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: project.brief, incident.alert, test.results.
3. Run the scripts above. Never fabricate JSON.
4. Write only requirements.spec, acceptance.criteria, user.story.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
