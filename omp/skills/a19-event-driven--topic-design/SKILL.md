---
name: "a19-event-driven--topic-design"
description: "Use when an event.topology task needs a new topic, stream or queue sized and keyed from a blueprint: partitions, routing key, ordering unit and retention."
disable-model-invocation: true
---

# Topic design

## When to Use

- A new topic, stream or queue is needed and `architecture.blueprint` (A03) or `cloud.architecture` (A16) names the flows. Output is an `event.topology.spec`, autonomy L2 (`event_design`).
- Not for an existing backlog (`skill://a19-event-driven--backpressure-handling`), not for the system-level AsyncAPI contract, which A03 owns, and not for retry or DLQ policy (`skill://a19-event-driven--dlq-redrive`).

## Procedure

1. Call `shared_handover_read` with `{task_id: <assigner task id>}` and `shared_open_questions` with the same `task_id`. Extract per flow: peak message rate, average message size, the entity whose order matters (or "none"), consumer rate and retention need. A missing figure is BLOCKED `needs=input`; never estimate silently.
2. Keep the broker the `cloud.architecture` names (read it with `read`). Otherwise pick by ordering need: per-entity order needs Kafka (ordering unit: partition), Kinesis (shard), SQS or SNS FIFO (message group) or Pub/Sub (ordering key); SQS standard, SNS standard and the classic EventBridge bus give no order. Recommend only; provisioning is A11/A22.
3. Call `event_partition_plan` with `peak_msgs_per_s`, `avg_msg_bytes`, `partition_max_mib_per_s` (the broker or tier figure the task or `cloud.architecture` gives), `consumer_max_msgs_per_s` (measured, not guessed), `headroom_pct` (from the task; if none, use 30 and record it as an assumption) and `hot_key_share_pct` (from data or 0 with a note). `required_partitions` is the maximum of `partitions_by_throughput`, `partitions_by_consumers` and `partitions_by_skew`.
4. In the `event_partition_plan` result, `partitions_by_skew` of -1 means the hottest key alone reaches one partition's capacity: no partition count helps, so split or salt the key (only where its order does not matter) or re-key. A `skew_ratio` above 2 is a minor finding; record it with the key choice.
5. Fix the final partition count now and rerun `event_partition_plan` with `current_partitions` set to it; keep `evaluated_partitions`, `hot_partition_msgs_per_s` and the `verdict`. Partitions normally cannot be reduced, and raising them remaps hash keys and breaks per-key order, so size for the horizon in the task instead of planning a later resize.
6. Call `event_ordering_check` with `broker`, `order_required`, `entity_key_fields`, `partition_key_fields` (must be a subset of the entity key), `handler_concurrency_per_unit`, `retry_mode`, `producer_in_flight`, `idempotent_producer` and `partitions_will_change`. On a keyed broker (Kafka, Kinesis, FIFO, Pub/Sub), empty `entity_key_fields` is INVALID_INPUT when `order_required` is true, and otherwise gives `ordered` false with an info note. `order_required` true with `ordered` false means the design fails; fix the finding (key, concurrency 1 per unit, in-place retry, Kafka idempotent producer with at most 5 in flight).
7. Cross-check `event_partition_plan` with `event_ordering_check`: `max_consumer_parallelism` from step 5 equals the number of parallel consumers the design allows; `ordering_unit` from step 6 must be the unit you counted. Disagreement is E-CONTRACT.
8. Write the topology spec (topic, partitions, key fields, retention, DLQ or dead-letter topic, owner). If you drafted an AsyncAPI document, call `event_asyncapi_lint` with `document` as JSON text; `verdict` `fail` blocks. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `event.topology.spec` with the figures and `verdict` of both tools and the assumptions list.

## Failure Handling

- INVALID_INPUT: fix the named field (for example `hot_key_share_pct` between 0 and 100) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the missing figure.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; do not hand-compute a partition count.
- Per-partition write budgets and retention limits are provider figures that change: use the task or `cloud.architecture` values and say so when none is given.
- A broker choice that conflicts with the blueprint is E-CONTRACT: BLOCKED to A03 `needs=input`.

## Handover

`to_agent`: `A05` (implements producers and consumers against the topology); `A13` also consumes `event.topology.spec` for lag SLOs: name it in `remaining`. Fill `goal`, `completed`, `decisions` (broker, partitions, key, assumptions), `artifacts` (spec with digest, producer `A19`), `open_questions`, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a19-event-driven/topic-design.md; edit the source and run scripts/build_agents.py -->
