---
name: a11-devops
description: "A11 DEVOPS DevOps / Platform Engineer — Owns IaC, CI pipelines, build artifacts and environments. Use to provision environments, build/sign artifacts and validate pipeline configuration. Use when the swarm assigns capability deploy.env, ci.pipeline, build.artifact."
---

# DevOps / Platform Engineer (A11 DEVOPS) — omp

## When to Use

- The assignment capability is one of: deploy.env, ci.pipeline, build.artifact, iac.change.
- The user names this agent, slug `a11-devops`, or code `DEVOPS`.

Don't use for: Do not write product business logic.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a11-devops` (definition: `omp/agents/a11-devops.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not write product business logic.
