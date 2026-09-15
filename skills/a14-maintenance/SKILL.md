---
name: a14-maintenance
description: >
  A14 MAINT Maintenance Engineer — Owns patch tasks, dependency bumps, tech-debt register and EOL tracking. Use for hotfix root-cause analysis, CVE-driven patching and debt triage.
  Use when the user runs /a14-maintenance, asks for MAINT, or the swarm assigns capability maint.patch, maint.deps, maint.debt.
disable-model-invocation: false
---

# Maintenance Engineer (A14 MAINT)

Subagent body: `prompts/A14-maintenance.md` (do not paste it here). Generated agents: `.claude/agents/a14-maintenance.md` and `.grok/agents/a14-maintenance.md`.

## When to Use

- The assignment capability is one of: maint.patch, maint.deps, maint.debt, maint.rca.
- The user names this agent, slug `a14-maintenance`, or code `MAINT`.
- Use when the user runs /a14-maintenance.

Don't use for: Do not add unrelated features.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/maint_deps.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/maint_deps.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: incident.alert, vulnerability.report, release.record, test.results.
3. Run the scripts above. Never fabricate JSON.
4. Write only patch.task, debt.register, rca.report, dependency.bump.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
