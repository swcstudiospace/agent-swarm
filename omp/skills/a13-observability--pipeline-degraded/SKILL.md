---
name: "a13-observability--pipeline-degraded"
description: "Use when telemetry is missing, empty or lagging and a monitoring.degraded broadcast with probe-based alerting must replace SLO evaluation."
disable-model-invocation: true
---

## When to Use

- An evaluation returned `total: 0` for a window, the collector is silent, or ingestion lag is above its target (spec section 5: telemetry pipeline outage).
- Fail-closed rule: never report "healthy" without data. Alerting falls back to external synthetic probes and A12 halts canaries.
- When data is present, use `skill://a13-observability--burn-evaluation` instead.

## Procedure

1. Use `read` on the obs_slo JSON or collector report and list the windows with `total: 0` (their `burn_rate` is null) and the last event time.
2. Compute lag per event as `ingest_ts - event_ts` in seconds from the task-supplied samples (the dual stamps of spec section 5). Treat a negative value within 60 s as 0 (the spec tolerates 60 s skew); a value below -60 is not skew and goes to `open_questions`.
3. Call `obs_ingestion_lag_p95` with `lags_seconds` and `target_s: 30` (spec metric: P95 under 30 s). Use `p95_seconds` and `within_target` in the report.
4. Call `obs_probe_availability` with the latest external synthetic `results` (booleans, time order) and `min_availability` from the task (the manifest `objective` when the task names none: convention of this skill). This is the fallback signal; if `meets_target` is false or `longest_failure_run` is long, say it is a user-visible suspicion, not a confirmed SLO breach.
5. From the `obs_ingestion_lag_p95` and `obs_probe_availability` results, emit the `monitoring.degraded` record in your result: affected service, evidence (empty windows or `p95_seconds`), probe result, and `fallback: "synthetic-probes"`. State the SLO as unknown, never healthy. A13 has no broadcast tool, so the record in the result and the handover to A12 are how canaries get halted.
6. Call `shared_log_emit` with `level: "warn"`, `message: "monitoring degraded"` and `fields` for service and `p95_seconds`.
7. Call `shared_handover_validate`, then `shared_handover_write` to `A12` with the constraint that canary steps stay halted until telemetry returns.

## Outputs

- `monitoring.degraded` block and a summary naming which signal is missing, the lag P95 against 30 s and the probe availability.

## Failure Handling

- `obs_ingestion_lag_p95` INVALID_INPUT (empty or negative `lags_seconds`): there are no valid samples; that is itself the degraded finding, record it.
- `obs_probe_availability` DEPENDENCY_UNAVAILABLE: retry twice, then report that both telemetry and probes are unavailable and set `verification.status` to unverified.
- Telemetry returns: rerun `skill://a13-observability--burn-evaluation` and clear the degraded state in a new handover.

## Handover

- `to_agent`: `A12`. `goal`: keep canaries halted while telemetry is degraded.
- `completed`: lag and probe checks. `constraints`: no canary promotion, SLO status unknown. `remaining`: pipeline recovery. `verification`: partial or unverified per the probe result.
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a13-observability/pipeline-degraded.md; edit the source and run scripts/build_agents.py -->
