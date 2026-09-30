---
name: "a02-requirements"
description: "A02 REQ Requirements Engineer — Turns briefs into a requirements spec, user stories and machine-checkable acceptance criteria (Given/When/Then). Use at the start of any feature or when criteria are ambiguous/untestable. Use when the swarm assigns capability req.elicit, req.spec, req.acceptance."
---

# Requirements Engineer (A02 REQ) — omp

## When to Use

- The assignment capability is one of: req.elicit, req.spec, req.acceptance.
- The user names this agent, slug `a02-requirements`, or code `REQ`.

Don't use for: Do not design architecture or write product code.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a02-requirements` (definition: `omp/agents/a02-requirements.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not design architecture or write product code.
