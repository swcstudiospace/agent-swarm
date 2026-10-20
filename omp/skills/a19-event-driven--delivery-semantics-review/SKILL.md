---
name: "a19-event-driven--delivery-semantics-review"
description: "Use when a design or patch claims ordering, at-least-once or exactly-once delivery; test it against broker, key, retry and dedup settings and give a verdict."
disable-model-invocation: true
---

# Delivery semantics review

## When to Use

- A design, `event.topology.spec` or `code.patch` says "ordered", "exactly once" or "no duplicates", and someone must confirm it before it ships. This is a review with findings: L2 (`event_design`); it changes no code.
- Not for building the dedup claim (`skill://a19-event-driven--idempotent-consumer`) nor choosing partitions (`skill://a19-event-driven--topic-design`).

## Procedure

1. Read the claim and the settings with `read` and `grep`: the broker, producer config, consumer config, retry or DLQ config, the dedup store. List each claim as one line: "per-entity order for X", "effect Y happens once". Call `shared_open_questions` with `{task_id}`: a claim with no setting to test, or an unanswered question, is open, not a pass.
2. For each ordered flow call `event_ordering_check` with `broker`, `order_required`, `entity_key_fields`, `partition_key_fields`, `handler_concurrency_per_unit`, `retry_mode` (`in-place`, `retry-queue` or `none`), `producer_in_flight`, `idempotent_producer`, `partitions_will_change`. Read `ordering_unit` and `ordered`. Findings that break order: a standard SQS or SNS or classic EventBridge target with `order_required` (no guarantee; the newer EventBridge Custom Event Bus orders per event group for FIFO subscribers and is not modelled), a routing key containing fields outside the entity key, more than one handler per partition or group, a retry queue behind later messages, Kafka with more than 1 in flight and no idempotence (or more than 5 with it), a Kafka partition count change or a Kinesis reshard (read parent shards to the end first). With `order_required` false every such finding is info and `ordered` still says whether the setup keeps order.
3. For each at-least-once effect call `event_idempotency_window` with `key_source`, `dedup_window_s`, `retry_window_s`, `redelivery_window_s`, `replay_window_s`, `atomic_claim`, `naturally_idempotent`. `covered` false or a blocker means the effect can repeat. Note SQS FIFO and SNS FIFO deduplicate within a 5 minute (300 s) interval only: if `redelivery_window_s` or `retry_window_s` exceed it, that broker dedup does not cover the case; defer to the dedup figure in the task or the provider docs if different.
4. Call `event_retry_schedule` with the actual retry config: `base_delay_s`, `multiplier`, `max_delay_s`, `max_attempts`, `jitter`, `retry_mode` (`in-process` or `queue-redelivery`), `processing_timeout_s`, `visibility_timeout_s`, `budget_s`. Use `worst_case_total_s` as `retry_window_s` in step 3 and rerun it; the retry window and the dedup window must be derived from one source. A `fits_visibility` false is a blocker: the message returns while still being processed, which is duplicate delivery by design.
5. Cross-check `event_ordering_check`, `event_idempotency_window` and `event_retry_schedule`: a retry mode that is `retry-queue` in step 2 must be `queue-redelivery` or an explicit retry topic in step 4; a contradiction is E-CONTRACT. An `ordered: true` flow whose consumer has `covered: false` is ordered but duplicated: report both.
6. Classify each claim with `write` into the review. "Exactly once" is accepted only as at-least-once delivery plus a covered idempotent effect (or Kafka transactions for a Kafka-to-Kafka read-process-write; external side effects are never covered by them). "At most once" is accepted only if message loss is acceptable by the task. Anything else is a finding.
7. Take the review verdict from the tool outputs only (`event_ordering_check`, `event_idempotency_window`, `event_retry_schedule`): `fail` when any `verdict` is `fail` (a blocker or major finding), `warn` on minor only, else `pass`. List each finding id with its severity; never downgrade one.
8. Write the review (claims table, setting evidence, tool verdicts, required fixes with owner) and call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `event.topology.spec` review section (claims, verdicts, required changes). No `code.patch`.

## Failure Handling

- INVALID_INPUT: fix the named field or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name the claims not tested.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no verdict for that claim.
- Broker dedup intervals and Kafka limits come from provider docs: if unsure of a figure for the installed version, defer to the tool output and flag it.
- Two sources disagree (config versus design text): E-CONTRACT, report both.

## Handover

`to_agent`: the agent that assigned the review (`A09` for a patch review, else `A05`; `A13` consumes the topology spec). Fill `goal`, `completed`, `decisions` (each claim: accepted or rejected), `artifacts` (review with digest), `open_questions`, `verification` (verified only if all three tool verdicts were obtained and consistent). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a19-event-driven/delivery-semantics-review.md; edit the source and run scripts/build_agents.py -->
