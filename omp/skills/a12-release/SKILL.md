---
name: "a12-release"
description: "A12 REL Release Manager — Owns release plans, progressive delivery (canary) and rollback. Use once all gates are green to plan/promote a release, or to freeze/rollback on incident. Use when the swarm assigns capability release.plan, release.promote, release.rollback."
---

# Release Manager (A12 REL) — omp

## When to Use

- The assignment capability is one of: release.plan, release.promote, release.rollback, gate.release.
- The user names this agent, slug `a12-release`, or code `REL`.

Don't use for: Do not write application code.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a12-release` (definition: `omp/agents/a12-release.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not write application code.
