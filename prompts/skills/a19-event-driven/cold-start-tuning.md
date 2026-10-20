---
name: a19-event-driven--cold-start-tuning
description: Use when a serverless function misses its cold-start or timeout budget, or throttles; size memory, timeout, concurrency and warm capacity as serverless.config.
owner: a19-event-driven
tools: [event_function_sizing, shared_toolchain, shared_check_plan, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Cold-start and function sizing

## When to Use

- A function (AWS Lambda or a generic platform) has a measured cold start above budget, p99 near its timeout, throttling, or a downstream connection limit at risk.
- Output is a `serverless.config` recommendation: L2 (`event_design`). Warm capacity (provisioned concurrency) costs money: you recommend it with the figure, you never purchase or apply it; applying to production is the pipeline's (A11, A22) and L3.
- Not for autoscaling of containers (`skill://a19-event-driven--scale-to-zero-rollout`).

## Procedure

1. Collect measurements with `read` and `grep` of the task, dashboards exports or load-test output: `memory_mb`, `timeout_s`, `peak_events_per_s`, `batch_size`, `p95_duration_ms`, `p99_duration_ms`, `cold_start_ms`, the budget (`cold_start_budget_ms`), `reserved_concurrency`, `downstream_max_connections` with `connections_per_instance`, and `source_visibility_timeout_s` for a queue trigger. Missing measurements are BLOCKED `needs=input`; do not use typical values.
2. Call `event_function_sizing` for the current configuration with `platform` (`aws-lambda` or `generic`) and the fields above, plus `headroom_pct` from the task. Read `concurrency_needed` (peak invocations per second times p95 in seconds, with headroom), `max_safe_concurrency` (-1 means no downstream limit given), `provisioned_concurrency_suggested`, `vcpu_equivalent` and `findings`.
3. Apply the `event_function_sizing` findings in this order. p99 at or above the timeout is a blocker (at least 1% of invocations are cut off); p99 above 80% of the timeout is minor: raise the timeout or cut the work. `reserved_concurrency` below `concurrency_needed` throttles; above `max_safe_concurrency` it breaks the database: put a pooler or proxy in front or cap concurrency.
4. For cold start, read `provisioned_concurrency_suggested` from `event_function_sizing`: `cold_start_ms` above the budget makes it equal to `concurrency_needed`. Try the free levers first with `edit` and re-measure: smaller package and fewer imports, initialization outside the handler, memory increase (CPU scales with memory on Lambda; one vCPU at 1769 MB, shown by `vcpu_equivalent`), a runtime snapshot feature where the runtime supports it. Use warm capacity only if the levers do not reach the budget; state the number as a recommendation with its cost for A20 or the requester.
5. Queue trigger, from `event_function_sizing`: on Lambda the source visibility timeout must be at least 6 times `timeout_s`; elsewhere at least the timeout (blocker). Fix the queue setting in the config or the timeout, whichever is cheaper. `p95_duration_ms` and `p99_duration_ms` are per invocation (a whole batch), so measure them at the batch size you propose: a larger `batch_size` lengthens them and eats the timeout margin.
6. Rerun `event_function_sizing` with the proposed values: `verdict` must not be `fail`, and `concurrency_needed` must not exceed `max_safe_concurrency` when it is positive. Compare the two runs and list each changed field and its reason.
7. Write the `serverless.config` (memory, timeout, batch size, reserved and provisioned concurrency, queue visibility, partial batch response for batch triggers) under the path the task names. Where code changes (lazy init, connection reuse), edit it with `edit`; call `shared_toolchain` with `{"scan": true}` and `shared_check_plan` with `{"scan": true}` and run the named checks through `bash`.
8. Call `shared_artifact_digest` with `{path}` for the config and any patch, then see Handover.

## Outputs

- `serverless.config` with before and after tool fields; optional `code.patch`.

## Failure Handling

- INVALID_INPUT (for example `memory_mb` outside 128..10240 on `aws-lambda`, `timeout_s` above 900, `p99_duration_ms` below `p95_duration_ms`, connection fields not given together): fix that field or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the gap.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Platform limits can change: defer to the tool output and name any figure you could not confirm.
- Applying configuration to production: BLOCKED `needs=human-approval`.

## Handover

`to_agent`: `A11` (applies `serverless.config` through the pipeline; `A22` also consumes it, name it in `remaining`). Fill `goal`, `completed`, `decisions` (each changed field with the finding), `artifacts` (digests), `open_questions` (cost approval), `verification` (partial unless a load test ran). Call `shared_handover_validate`, then `shared_handover_write`.
