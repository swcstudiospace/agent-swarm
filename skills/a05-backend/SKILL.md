---
name: a05-backend
description: >
  A05 BE Backend Engineer — Implements backend services against A03 contracts and A07 data contracts, with unit tests. Use for server-side code, APIs, business logic and hotfix patches.
  Use when the user runs /a05-backend, asks for BE, or the swarm assigns capability code.backend, code.api, code.patch.
disable-model-invocation: false
---

# Backend Engineer (A05 BE)

Subagent body: `prompts/A05-backend.md` (do not paste it here). Generated agents: `.claude/agents/a05-backend.md` and `.grok/agents/a05-backend.md`.

## When to Use

- The assignment capability is one of: code.backend, code.api, code.patch.
- The user names this agent, slug `a05-backend`, or code `BE`.
- Use when the user runs /a05-backend.

Don't use for: Do not edit frontend, schema, or deploy.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/code_checks.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/code_checks.ts --task-id $TASK --correlation-id $CORR --json`
- `python3 scripts/be_contract_conformance.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/be_contract_conformance.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: api.contract, data.contract, schema.migration, acceptance.criteria, review.verdict, gate.verdict.
3. Run the scripts above. Never fabricate JSON.
4. Write only code.patch, unit.tests, task.result.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
