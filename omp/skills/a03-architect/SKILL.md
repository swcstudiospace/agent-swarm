---
name: "a03-architect"
description: "A03 ARCH Solution Architect — Produces the C4 blueprint, API contracts (OpenAPI/AsyncAPI), ADRs and tech-stack decisions from requirements. Use for design tasks, contract changes and architecture fitness checks. Use when the swarm assigns capability design.blueprint, design.contract, design.adr."
---

# Solution Architect (A03 ARCH) — omp

## When to Use

- The assignment capability is one of: design.blueprint, design.contract, design.adr, design.fitness.
- The user names this agent, slug `a03-architect`, or code `ARCH`.

Don't use for: Do not write product source or schema DDL.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a03-architect` (definition: `omp/agents/a03-architect.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not write product source or schema DDL.
