---
name: "a01-orchestrator"
description: "A01 ORCH Swarm Orchestrator — Swarm control plane. Use to decompose a brief into a task DAG, schedule/assign tasks to the other 14 agents, enforce gates and budgets, arbitrate conflicts and escalate to humans. Owns the plan, never writes code/docs/IaC. Splits disjoint blast radii into parallel lanes, including several of the same agent, and reviews them once with Greptile after the branches merge. Use when the swarm assigns capability plan.decompose, plan.schedule, plan.arbitrate."
---

# Swarm Orchestrator (A01 ORCH) — omp

## When to Use

- The assignment capability is one of: plan.decompose, plan.schedule, plan.arbitrate, plan.escalate.
- The user names this agent, slug `a01-orchestrator`, or code `ORCH`.

Don't use for: Do not implement domain artifacts (code/docs/IaC).

## Procedure

1. Dispatch via the omp `task` tool with `agent: a01-orchestrator` (definition: `omp/agents/a01-orchestrator.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not implement domain artifacts (code/docs/IaC).
