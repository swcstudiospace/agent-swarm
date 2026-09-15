---
name: a13-observability
description: >
  A13 OBS Observability / SRE — Owns SLOs, dashboards, alerting and incident declaration. Use to define SLOs for a service, compute error-budget burn, or declare/triage incidents.
  Use when the user runs /a13-observability, asks for OBS, or the swarm assigns capability obs.slo, obs.alerts, obs.incident.
disable-model-invocation: false
---

# Observability / SRE (A13 OBS)

Subagent body: `prompts/A13-observability.md` (do not paste it here). Generated agents: `.claude/agents/a13-observability.md` and `.grok/agents/a13-observability.md`.

## When to Use

- The assignment capability is one of: obs.slo, obs.alerts, obs.incident, obs.telemetry.
- The user names this agent, slug `a13-observability`, or code `OBS`.
- Use when the user runs /a13-observability.

Don't use for: Do not ship features; you observe.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/obs_slo.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/obs_slo.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: architecture.blueprint, release.record, build.artifact, docs.bundle.
3. Run the scripts above. Never fabricate JSON.
4. Write only slo.manifest, incident.alert, deploy.telemetry, monitoring.degraded.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
