---
name: a25-resilience--probe-tuning
description: Use when pods restart in loops, receive traffic before they are ready, or probes look wrong; tune startup, liveness and readiness against measured start time.
owner: a25-resilience
tools: [resil_probe_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Probe tuning

## When to Use

- Containers restart during start-up, serve errors right after a rollout, or a scan reports `readiness-missing`, `liveness-too-aggressive`, `liveness-equals-readiness` or a startup budget finding.
- Not for termination behaviour: `skill://a25-resilience--graceful-shutdown`. Not for alert rules on restarts: A13.
- Manifest edits and staging rollouts are L2; production rollouts are L3 (a probe change restarts pods).

## Procedure

1. Run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <manifest-dir> --json`. Keep the probe findings (`readiness-missing`, `liveness-too-aggressive`, `liveness-equals-readiness`, `probe-invalid`) with `location` and `workload`. The script has no start-time measurement, so it never reports the startup-budget ids: those come from step 3.
2. Get `expected_startup_seconds`: the time from container start to the first passing readiness check, from `deploy.telemetry` (A13) or a cold start in staging (`bash` with `kubectl --context <ctx> -n <ns> get pod -o json`, read-only). No measurement: BLOCKED needs=input. Use the slow case (cold image pull excluded, cold cache included), not the average.
3. Call `resil_probe_check` with `{workload, serves_traffic, expected_startup_seconds, containers: [{name, startup, liveness, readiness}]}`; each probe is `{kind, endpoint, initial_delay_seconds, period_seconds, timeout_seconds, success_threshold, failure_threshold}`. Omitted fields take the Kubernetes defaults (delay 0, period 10, timeout 1, success 1, failure 3), so copy only what the manifest sets. Set `serves_traffic` true when a Service or Gateway selects the pods.
4. Decide from the `resil_probe_check` findings. `startup-budget-too-small` and `liveness-kills-slow-start` are blockers: make `failure_threshold * period_seconds` of the startup probe at least 1.25 times `expected_startup_seconds` (90 s start: period 10, failureThreshold 12 gives 120 s, above 112.5 s). `thin-startup-budget` is minor. `liveness-too-aggressive` means `failure_threshold * period_seconds` is under 10 s: use at least 3 failures at 10 s. `readiness-missing` on a serving pod is major.
5. Set the three probes apart with `edit`, by job: startup guards a slow boot and holds liveness and readiness back until it succeeds; liveness checks only the process itself (a local endpoint with no downstream calls), so a database outage never restarts pods; readiness may check what the pod needs to serve and only removes it from the endpoints. Fix `liveness-equals-readiness` by giving liveness its own cheap endpoint. `success_threshold` must stay 1 for liveness and startup; set `timeout_seconds` above the probe endpoint's p99 from `deploy.telemetry`.
6. Cross-check the script against the tool for the same container: ids reported by both must match in severity; `liveness-missing` is `info` in the tool and silent in the script (liveness is optional, add it only when the process can hang without exiting). A `grpc` probe needs a cluster with native gRPC probes (stable from Kubernetes 1.27): check the version with the task.
7. Apply the change with `edit` on a branch named `<seat-prefix>/<task_id>`, check with `kubectl --context <ctx> -n <ns> apply --dry-run=server -f <file>`, then re-run the script and `resil_probe_check`: `missing_readiness` must be 0 and no blocker may remain. Production rollout is L3: BLOCKED needs=human-approval.
8. Use `write` for `resilience-reports/<service>/probes.md` (before and after probe values, measured start time and its source) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `resilience.report` section: probe findings before and after, startup budget versus measurement, digest.

## Failure Handling

- INVALID_INPUT (value outside the API limits, `success_threshold` rule): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Start time varies by more than 2x across runs: report the range, size the budget for the worst, and say so; never tune from one run.
- Script and tool disagree: E-CONTRACT.

## Handover

`to_agent` A12. `goal`: release the probe change. `decisions` (budget arithmetic per container), `artifacts` (report and patch with digests, producer A25), `open_questions` (unmeasured containers), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
