---
name: "a19-event-driven--backpressure-handling"
description: "Use when a consumer group shows growing lag or an SLO breach; compute drain time and consumers needed, find the binding limit and fix it."
disable-model-invocation: true
---

# Backpressure handling

## When to Use

- A consumer lags: `lag_msgs` keeps rising, a delay SLO is breached, or a function is throttled while the queue grows. Analysis and a fix proposal are L2 (`event_design`).
- Changing live consumer counts, partitions or throttles in production is outside this skill: hand the numbers over, the pipeline owners apply them (L3).
- Not for a DLQ build-up (`skill://a19-event-driven--dlq-redrive`) nor for a planned scaler (`skill://a19-event-driven--scale-to-zero-rollout`).

## Procedure

1. Read the lag figures from the task or a metrics export with `read`: `lag_msgs`, `arrival_msgs_per_s`, `consumer_count`, `per_consumer_msgs_per_s` (measured under load, not the nameplate), `slo_s`, `partition_count`. A missing figure is BLOCKED `needs=input`.
2. Call `event_lag_assessment` with those fields. Read `trend`: `growing` is a blocker (consumers process less than arrives, lag unbounded), `stalled` a major, `draining` has `drain_time_s`, `steady` means lag 0. Read `estimated_delay_s` against `slo_s` (`slo_breach`), `recovery_time_s` (time until the delay is back inside the SLO; -1 when it never is), `consumers_needed` and `needs_more_partitions`.
3. Find the binding limit, in this order. (a) `needs_more_partitions` true: parallelism is capped by partitions (a consumer beyond `partition_count` is idle, minor finding); plan more partitions in step 4. (b) Per-consumer rate too low: profile the handler, raise batch size, remove synchronous per-message calls. (c) Downstream limit: call `event_function_sizing` with `platform`, `memory_mb`, `timeout_s`, `peak_events_per_s` (= `arrival_msgs_per_s`), `batch_size`, `p95_duration_ms`, `p99_duration_ms`, `headroom_pct`, `cold_start_ms`, `cold_start_budget_ms`, `reserved_concurrency`, `downstream_max_connections`, `connections_per_instance`; `max_safe_concurrency` below `concurrency_needed` means more consumers would overload the downstream: add a pooler or buffer instead of scale.
4. When partitions bind, call `event_partition_plan` with `peak_msgs_per_s` (arrival plus the drain rate), `avg_msg_bytes`, `partition_max_mib_per_s`, `consumer_max_msgs_per_s` (= `per_consumer_msgs_per_s`), `headroom_pct`, `hot_key_share_pct`, `current_partitions` = `partition_count`. `required_partitions` above the current count is the target; if `partitions_by_skew` is -1 the lag is a single hot key: re-key, partitions will not help. State that raising partitions remaps keys and breaks per-key order (see `skill://a19-event-driven--topic-design`).
5. Cross-check the outputs of `event_lag_assessment`, `event_partition_plan` and `event_function_sizing`: `consumers_needed` from step 2 must be at or below `max_consumer_parallelism` of step 4 and `max_safe_concurrency` of step 3; if not, the SLO cannot be met by scaling alone: say so and name the alternatives (relax `slo_s`, shed or defer low priority events, drop ordering for a class of events).
6. Choose the protection that stops the next incident and write it into the change note with `write`: bounded consumer prefetch and in-flight limit, producer-side rate limit or batching, load shedding with an explicit policy, an alert on `estimated_delay_s` (for A13). Do not add unbounded in-memory queues.
7. Rerun `event_lag_assessment` with the proposed `consumer_count` and `per_consumer_msgs_per_s`: `slo_breach` false and `trend` `draining` or `steady`. Write the change proposal and, where code is changed, a patch under the task path; call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `event.topology.spec` update (partitions, consumer limits, shedding policy, alert threshold) with the numbers of both runs.
- `code.patch` when handler or producer glue changed.

## Failure Handling

- INVALID_INPUT: fix the named field or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the missing figure.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tools model steady rates: bursts and rebalances are not modelled; say so beside any recovery time.
- Applying a change to a production consumer or topic: BLOCKED `needs=human-approval`; delete or purge is L4: never.

## Handover

`to_agent`: `A13` (alerting on delay; `A05` also consumes the topology spec, name it in `remaining`). Fill `goal`, `completed`, `decisions` (binding limit, target consumers or partitions), `artifacts` (digests), `open_questions`, `verification` (`partial`: from rates, not a load test). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a19-event-driven/backpressure-handling.md; edit the source and run scripts/build_agents.py -->
