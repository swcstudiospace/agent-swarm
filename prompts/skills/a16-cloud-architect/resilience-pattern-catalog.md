---
name: a16-cloud-architect--resilience-pattern-catalog
description: Use when an incident.alert or finding names a failure mode and the design needs timeouts, retries, breakers, bulkheads or shedding with budgets that add up.
owner: a16-cloud-architect
tools: [cloud_latency_budget, cloud_composite_availability, cloud_blast_radius, cloud_twelve_factor_audit, shared_handover_read, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Resilience pattern catalog

## When to Use

- A13 raised an `incident.alert` (or a review found a failure mode such as dependency slowness, retry storm, noisy neighbour, cascade) and the design must say which in-service resilience patterns apply and with what numbers.
- Not for sizing cells (`skill://a16-cloud-architect--cell-architecture-split`), DR (`skill://a16-cloud-architect--dr-strategy-selection`) or running chaos experiments (A25 executes them).
- Autonomy: the catalog entry and design are L2. A change that alters production behaviour (new timeout, breaker threshold) is applied by A05 through its own gates and, in production, is L3.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the `incident.alert` or the review finding. Classify the failure mode with `read` of the evidence: slow dependency, error burst, overload, partial zone loss, deploy-induced, cascading restart. An unclassifiable alert is `needs=input`, not a guess.
2. Run `python3 scripts/cloud_readiness_scan.py --task-id $TASK --correlation-id $CORR --root . --path <service dir> --json`. Findings (ids start with `cloud.`) `k8s-probes-missing`, `graceful-shutdown-missing`, `docker-shell-form-entrypoint`, `k8s-single-replica`, `k8s-no-zone-spread` and `autoscaling-missing` are pre-conditions: patterns do not compensate for them, so they come first in the design.
3. Use `grep` on the repository configuration for existing timeouts, retries and breakers, then select from this catalog by failure mode: slow dependency -> deadline propagation plus per-hop timeout below the caller timeout; error burst -> retry with exponential backoff and jitter capped by a retry budget, plus a circuit breaker; overload -> load shedding and bounded queues with backpressure; noisy neighbour -> bulkhead (separate pool, quota or cell); duplicate work from retries -> idempotency keys; partial zone loss -> zone spread and a PodDisruptionBudget; cascading restart -> readiness gating, startup probe, graceful SIGTERM drain. Thresholds (timeout values, breaker open ratio) are set from measurement, never from this list.
4. Budget the retries: call `cloud_latency_budget` with `p99_target_ms` and `hops` in which each retried call sets `calls` to the maximum attempts (one attempt plus retries) and parallel hedged calls share a `group`. Read `critical_path_p99_ms`, `budget_remaining_ms`, `tail_hit_pct`, `stages`, findings `over-budget`, `dominant-stage:*`, `tail-amplification`. Layered retries multiply (3 attempts at each of 3 layers = 27 calls at the bottom): when `meets_target` is false, remove retries from all but one layer.
5. Check isolation: call `cloud_blast_radius` with `services` and `max_az_blast_pct` before and after the bulkhead or zone-spread change. `worst_blast_pct` must fall or stay equal; `spof_services` must not gain entries. A pattern that leaves the number unchanged is not credited as isolation.
6. Check the availability claim: call `cloud_composite_availability` with the tiers before and after redundancy patterns (`replicas`, `min_required`, `shared_failure_domain`). Never claim redundancy for replicas in one failure domain; the tool gives no credit and neither do you.
7. For a runtime service call `cloud_twelve_factor_audit` with `service`; `factor-09` findings (SIGTERM, idempotent jobs, `startup_seconds` above 30) must be zero before readiness-based patterns are listed as effective. Contradiction between the scan (shell-form entrypoint) and `handles_sigterm_gracefully: true` is E-CONTRACT; trust the scan until the descriptor is proven.
8. Write the pattern selection with `write` at the task path, else `docs/cloud/resilience-patterns.md`: failure mode, patterns, parameters to measure, step 4-6 evidence, and the chaos experiment A25 must run for each pattern. Call `shared_artifact_digest`, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `resilience.design`: failure mode to pattern map, retry and timeout budget, isolation evidence, verification experiments.

## Failure Handling

- `INVALID_INPUT`: fix the named hop or tier field or BLOCKED `needs=input`. `PARTIAL_SUCCESS`: use `completed_fraction`.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`.
- Library defaults (circuit breaker windows, retry caps) differ by framework and version: cite the repository's actual configuration found with `grep`, never a remembered default.
- No L3 or L4 action is taken here; a production parameter change is handed to A05.

## Handover

`to_agent` A25 (verifies each pattern by experiment); a second write to A13 (alert tuning). Fill `goal`, `completed`, `decisions`, `artifacts` (kind `resilience.design`, digest, producer A16), `open_questions` (parameters still to measure), `verification`. End with `shared_handover_validate` then `shared_handover_write`.
