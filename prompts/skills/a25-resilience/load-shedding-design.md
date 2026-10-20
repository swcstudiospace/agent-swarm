---
name: a25-resilience--load-shedding-design
description: Use when a service must degrade by priority under overload; size concurrency limits, reservations and bounded queues from Little's law and client timeouts.
owner: a25-resilience
tools: [resil_load_shed_check, resil_chaos_spec_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Load-shedding design

## When to Use

- A service saturates, queues grow without bound, or retries amplify an overload, and the task wants a priority admission policy.
- Not for adding capacity or autoscaler tuning: A21 owns `cluster.capacity.report` and node sizing. Not for alerting on saturation: A13.
- Design and staging overload tests are L2; a production overload test is L3 behind the error-budget gate.

## Procedure

1. Use `read` on `cluster.capacity.report` (A21), `resilience.design` (A16) and `deploy.telemetry` (A13) for the request classes, their peak `expected_rps`, `p99_service_ms` and the callers' timeouts. `capacity_concurrency` is the number of requests the service really runs at once (replicas times per-pod worker or pool limit, confirmed by a load test), not a CPU figure. Missing measurement: BLOCKED needs=input.
2. Define the classes for `resil_load_shed_check`: `name`, `priority` (1 most important), `expected_rps`, `p99_service_ms`, `max_concurrency`, `reserved_concurrency`, `queue_depth`, `queue_bounded`, `sheddable`. Health checks, payment or login style critical paths get priority 1 and `sheddable` false; batch and prefetch traffic get the lowest priority and `sheddable` true. `client_timeout_ms` is the strictest caller timeout.
3. Call `resil_load_shed_check` with `{capacity_concurrency, client_timeout_ms, classes}` at nominal load. Blockers: `reservations-exceed-capacity`, `unbounded-queue`, `non-sheddable-class-starved`. `queue-wait-exceeds-timeout` (major) means a request that joins a full queue is finished after the caller left: shrink `queue_depth` until `queue_depth * p99_service_ms / max_concurrency + p99_service_ms` (wait plus one service time) is at most `client_timeout_ms`. `shed-at-nominal-load` (minor) means the service is undersized before any surge.
4. Cross-check `resil_load_shed_check` against Little's law by hand: per class `needed = ceil(expected_rps * p99_service_ms / 1000)`; their sum must equal `total_needed_concurrency`, and `overloaded` must be true exactly when `capacity_concurrency` is below it. The p99 overstates mean concurrency, which errs on the safe side.
5. Run the surge: repeat step 3 with every `expected_rps` multiplied by the peak factor from `deploy.telemetry` (or 2 when the task names none, recorded as assumption). In `shed_plan`, the priority 1 class must have `shed_percent` 0 and shedding must start with the lowest priority. A shed top class means more capacity or a bigger reservation: send the number to A21, do not hide it.
6. Specify the mechanism for the platform in use: an ingress concurrency limit (for Envoy, circuit-breaker thresholds `max_connections`, `max_pending_requests`, `max_requests`), reject early with HTTP 429 or 503 and `Retry-After`, a bounded queue that drops the oldest or newest work, and client retries with a budget and jitter so retries do not add load. Check names against the actual gateway documentation in the task; say `unverified` otherwise.
7. Propose the overload test: call `resil_chaos_spec_check` with `environment` `staging`, steady state on the priority 1 success ratio, an abort condition on it, and `automatic_abort` true. `required_autonomy` `L2` with `blockers` 0 may run; production needs `skill://a25-resilience--chaos-experiment-design` first (L3).
8. Use `write` for `resilience-reports/<service>/load-shedding.md` (class table, nominal and surge `shed_plan`, mechanism, test spec) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `resilience.report` section: shed plan at nominal and surge load, queue bounds with the timeout arithmetic, test spec, digest.

## Failure Handling

- INVALID_INPUT (`reserved_concurrency` above `max_concurrency`, duplicate class `name`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool is a steady-state model: it knows nothing about bursts, retries or warm-up. Say so and let the staging overload test decide.
- L3 production test: BLOCKED needs=human-approval.

## Handover

`to_agent` A12. `goal`: release the shedding policy with its test result. `decisions` (priorities, reservations, surge factor and its source), `artifacts` (design with digest, producer A25), `open_questions` (capacity gaps sent to A21), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
