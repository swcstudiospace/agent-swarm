---
name: "a14-maintenance"
description: "A14 MAINT Maintenance Engineer — Owns patch tasks, dependency bumps, tech-debt register and EOL tracking. Use for hotfix root-cause analysis, CVE-driven patching and debt triage. Use when the swarm assigns capability maint.patch, maint.deps, maint.debt."
---

# Maintenance Engineer (A14 MAINT) — omp

## When to Use

- The assignment capability is one of: maint.patch, maint.deps, maint.debt, maint.rca.
- The user names this agent, slug `a14-maintenance`, or code `MAINT`.

Don't use for: Do not add unrelated features.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a14-maintenance` (definition: `omp/agents/a14-maintenance.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not add unrelated features.
