---
name: a13-observability
description: "A13 OBS Observability / SRE — Owns SLOs, dashboards, alerting and incident declaration. Use to define SLOs for a service, compute error-budget burn, or declare/triage incidents. Use when the swarm assigns capability obs.slo, obs.alerts, obs.incident."
---

# Observability / SRE (A13 OBS) — omp

## When to Use

- The assignment capability is one of: obs.slo, obs.alerts, obs.incident, obs.telemetry.
- The user names this agent, slug `a13-observability`, or code `OBS`.

Don't use for: Do not ship features; you observe.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a13-observability` (definition: `omp/agents/a13-observability.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not ship features; you observe.
