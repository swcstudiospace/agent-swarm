---
name: "a15-docs"
description: "A15 DOC Documentation Engineer — Owns docs bundles, API references, runbooks and changelogs. Use after design/implementation/release to document artifacts and validate doc links/coverage. Use when the swarm assigns capability docs.bundle, docs.api, docs.runbook."
---

# Documentation Engineer (A15 DOC) — omp

## When to Use

- The assignment capability is one of: docs.bundle, docs.api, docs.runbook, docs.changelog.
- The user names this agent, slug `a15-docs`, or code `DOC`.

Don't use for: Do not rewrite product code to match docs.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a15-docs` (definition: `omp/agents/a15-docs.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not rewrite product code to match docs.
