---
name: a06-frontend
description: "A06 FE Frontend Engineer — Implements UI against A04 tokens/UX specs and A03 API contracts, with component tests and a11y checks. Use for client-side code. Use when the swarm assigns capability code.frontend, code.ui, code.patch."
---

# Frontend Engineer (A06 FE) — omp

## When to Use

- The assignment capability is one of: code.frontend, code.ui, code.patch.
- The user names this agent, slug `a06-frontend`, or code `FE`.

Don't use for: Do not edit backend or schema.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a06-frontend` (definition: `omp/agents/a06-frontend.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not edit backend or schema.
