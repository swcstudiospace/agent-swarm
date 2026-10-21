---
name: "a13-observability"
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

## Registered tools

Discover with `swarm_catalog`, call with `swarm_tool_call` (the caller is your own identity).

- `obs_error_burn` (READ): Compute the SLI and error-budget burn rate obs_slo uses.
- `obs_multiwindow_alert` (READ): Decide burn-rate paging (14.4x over 1h with 6x over 6h) and ticketing (1x over 3d) for an SLO.
- `obs_budget_remaining` (READ): Compute remaining error budget for an SLO window from good and total event counts.
- `obs_alert_fatigue` (READ): Measure the non-actionable share of pages against the allowed alert-fatigue noise ratio.
- `obs_incident_mttd_mttr` (READ): Compute mean time to detect and mean time to restore from incident start, detection, and resolution timestamp…
- `obs_ingestion_lag_p95` (READ): Compute the nearest-rank P95 telemetry ingestion lag and check it against the ingestion target.
- `obs_dashboard_staleness` (READ): Check how old a dashboard's last refresh is at a supplied check time against the freshness limit.
- `obs_runbook_link_coverage` (READ): Report which alerts link to a known runbook and which alerts lack a resolvable runbook.
- `obs_cardinality_estimate` (READ): Estimate worst-case metric series count from per-label distinct value counts and flag a series budget breach.
- `obs_probe_availability` (READ): Compute synthetic probe availability and the longest consecutive failure run against an availability target.
- `obs_mitigation_gate` (READ): Decide whether a proposed automatic mitigation may run: whitelisted playbook, canary or staging (or full prod…
- `obs_escalation_due` (READ): Check whether an unstabilized sev 1 or 2 incident has reached the escalation clock (10 minutes by default) at…

## Skills

Read the one that fits with its link; they are hidden from the skill listing.

- `skill://a13-observability--alert-hygiene`: Use when an alert rule set changes or the weekly review runs and rules without SLO or runbook links or with excess noise must be…
- `skill://a13-observability--burn-evaluation`: Use when an obs.slo task asks for an SLO check on a service that already has a manifest and the answer is a cross-checked slo.rep…
- `skill://a13-observability--cardinality-guard`: Use when a new metric or label set is proposed and its worst-case series count must be checked against the series budget before i…
- `skill://a13-observability--dashboard-coverage`: Use when tier-1 user journeys are reviewed before a release and each needs an SLO manifest and a fresh dashboard, ending in a das…
- `skill://a13-observability--deploy-telemetry-feed`: Use when A12 asks for canary telemetry on a release step and the answer is a deploy.telemetry record with a continue, hold or rol…
- `skill://a13-observability--escalation-timeline`: Use when a sev 1 or 2 incident may have gone ten minutes without stabilizing and humans need a timeline bundle to take command.
- `skill://a13-observability--incident-declare`: Use when SLO evaluation yields a sev 1 or 2 alert and the incident must be confirmed, recorded and handed to A12 with rollback an…
- `skill://a13-observability--mitigation-run`: Use when a whitelisted runbook mitigation is proposed during an open incident and must be gated, executed with before and after e…
- `skill://a13-observability--pipeline-degraded`: Use when telemetry is missing, empty or lagging and a monitoring.degraded broadcast with probe-based alerting must replace SLO ev…
- `skill://a13-observability--slo-manifest-intake`: Use when an obs.slo task names a service that has no slo.manifest yet and A03 or A02 has supplied the objective to define one.
