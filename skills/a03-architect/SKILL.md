---
name: a03-architect
description: >
  A03 ARCH Solution Architect — Produces the C4 blueprint, API contracts (OpenAPI/AsyncAPI), ADRs and tech-stack decisions from requirements. Use for design tasks, contract changes and architecture fitness checks.
  Use when the user runs /a03-architect, asks for ARCH, or the swarm assigns capability design.blueprint, design.contract, design.adr.
disable-model-invocation: false
---

# Solution Architect (A03 ARCH)

Subagent body: `prompts/A03-architect.md` (do not paste it here). Generated agents: `.claude/agents/a03-architect.md` and `.grok/agents/a03-architect.md`.

## When to Use

- The assignment capability is one of: design.blueprint, design.contract, design.adr, design.fitness.
- The user names this agent, slug `a03-architect`, or code `ARCH`.
- Use when the user runs /a03-architect.

Don't use for: Do not write product source or schema DDL.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/arch_adr.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/arch_adr.ts --task-id $TASK --correlation-id $CORR --json`
- `python3 scripts/arch_contract_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/arch_contract_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: requirements.spec, acceptance.criteria, incident.alert, patch.task.
3. Run the scripts above. Never fabricate JSON.
4. Write only architecture.blueprint, api.contract, adr.set, tech.stack.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
