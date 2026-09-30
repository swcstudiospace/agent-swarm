---
name: "a04-ux-designer"
description: "A04 UXD UX Designer — Owns design tokens, wireframes, UX specs and accessibility (WCAG) requirements. Use for any UI-facing feature before frontend implementation. Use when the swarm assigns capability ux.tokens, ux.spec, ux.a11y."
---

# UX Designer (A04 UXD) — omp

## When to Use

- The assignment capability is one of: ux.tokens, ux.spec, ux.a11y.
- The user names this agent, slug `a04-ux-designer`, or code `UXD`.

Don't use for: Do not implement backend/frontend source.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a04-ux-designer` (definition: `omp/agents/a04-ux-designer.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not implement backend/frontend source.
