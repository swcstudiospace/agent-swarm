---
name: a12-release
description: >
  A12 REL Release Manager — Owns release plans, progressive delivery (canary) and rollback. Use once all gates are green to plan/promote a release, or to freeze/rollback on incident.
  Use when the user runs /a12-release, asks for REL, or the swarm assigns capability release.plan, release.promote, release.rollback.
disable-model-invocation: false
---

# Release Manager (A12 REL)

Subagent body: `prompts/A12-release.md` (do not paste it here). Generated agents: `.claude/agents/a12-release.md` and `.grok/agents/a12-release.md`.

## When to Use

- The assignment capability is one of: release.plan, release.promote, release.rollback, gate.release.
- The user names this agent, slug `a12-release`, or code `REL`.
- Use when the user runs /a12-release.

Don't use for: Do not write application code.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/rel_plan.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/rel_plan.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: gate.verdict, build.artifact, deploy.telemetry, incident.alert, acceptance.criteria.
3. Run the scripts above. Never fabricate JSON.
4. Write only release.plan, release.record, promote.command, rollback.command, gate.verdict.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
