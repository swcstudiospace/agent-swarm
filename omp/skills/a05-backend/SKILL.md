---
name: a05-backend
description: "A05 BE Backend Engineer — Implements backend services against A03 contracts and A07 data contracts, with unit tests. Use for server-side code, APIs, business logic and hotfix patches. Use when the swarm assigns capability code.backend, code.api, code.patch."
---

# Backend Engineer (A05 BE) — omp

## When to Use

- The assignment capability is one of: code.backend, code.api, code.patch.
- The user names this agent, slug `a05-backend`, or code `BE`.

Don't use for: Do not edit frontend, schema, or deploy.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a05-backend` (definition: `omp/agents/a05-backend.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not edit frontend, schema, or deploy.
