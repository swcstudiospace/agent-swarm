---
name: "a07-data"
description: "A07 DATA Data Engineer — Owns data models, schema migrations (expand/contract, reversible) and data contracts. Use for any persistence, migration or pipeline change. Use when the swarm assigns capability data.model, data.migration, data.contract."
---

# Data Engineer (A07 DATA) — omp

## When to Use

- The assignment capability is one of: data.model, data.migration, data.contract, data.pipeline.
- The user names this agent, slug `a07-data`, or code `DATA`.

Don't use for: Do not write application business logic.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a07-data` (definition: `omp/agents/a07-data.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not write application business logic.
