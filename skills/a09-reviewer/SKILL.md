---
name: a09-reviewer
description: >
  A09 REV Code Reviewer — Review gate. Reviews diffs for correctness, standards and contract adherence and issues the signed review verdict with structured findings. Read-only on product code.
  Use when the user runs /a09-reviewer, asks for REV, or the swarm assigns capability review.code, review.standards, gate.review.
disable-model-invocation: false
---

# Code Reviewer (A09 REV)

Subagent body: `prompts/A09-reviewer.md` (do not paste it here). Generated agents: `.claude/agents/a09-reviewer.md` and `.grok/agents/a09-reviewer.md`.

## When to Use

- The assignment capability is one of: review.code, review.standards, gate.review.
- The user names this agent, slug `a09-reviewer`, or code `REV`.
- Use when the user runs /a09-reviewer.

Don't use for: Do not edit product code.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/rev_gate.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/rev_gate.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: code.patch, api.contract, acceptance.criteria, schema.migration.
3. Run the scripts above. Never fabricate JSON.
4. Write only review.verdict, gate.verdict, review.comments.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
