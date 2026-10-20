---
name: a19-event-driven--idempotent-consumer
description: Use when a handler must tolerate duplicate delivery; pick the idempotency key, size the dedup window, make the claim atomic and ship a code.patch.
owner: a19-event-driven
tools: [event_idempotency_window, shared_toolchain, shared_check_plan, shared_git_head, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Idempotent consumer

## When to Use

- A handler has a side effect (write, payment call, outbound message) and its source is at-least-once (Kafka, Kinesis, SQS standard, Pub/Sub, EventBridge, async Lambda).
- This skill writes handler glue only. Persistence schema for the dedup store belongs to A07: ask for it through the handover, do not alter migrations.
- Not for review of an existing delivery claim without code changes (`skill://a19-event-driven--delivery-semantics-review`).

## Procedure

1. Read the handler and its tests with `read` and `grep`. Identify the effect and whether it is naturally idempotent (upsert by key, PUT, set-to-value) or not (increment, append, send, charge). Name `retry_window_s` (producer plus consumer retries), `redelivery_window_s` (how long the broker can still redeliver: SQS retention or visibility cycles, Kafka consumer-group offset retention, Pub/Sub ack deadline and retention) and `replay_window_s` (oldest event a replay may re-emit). A window you cannot read from config or the task is BLOCKED `needs=input`.
2. Pick `key_source` for `event_idempotency_window`: `producer-event-id` (the CloudEvents `id` the producer sets once per logical event and keeps across retries) or `business-key` (order id plus transition). `broker-message-id` is not stable across producer retries and replay, so it is a major finding and `covered` stays false unless the effect is naturally idempotent; `payload-hash` merges two identical commands: use it only with a written reason. `none` is acceptable only when `naturally_idempotent` is true.
3. Design the claim as one atomic operation with the effect: a unique constraint or `INSERT ... ON CONFLICT DO NOTHING` in the same transaction as the effect, or a conditional write (for example DynamoDB `attribute_not_exists`) with a TTL attribute. A check-then-act sequence is not atomic. Set `atomic_claim` accordingly.
4. Call `event_idempotency_window` with `key_source`, `dedup_window_s` (the store retention you will configure), `retry_window_s`, `redelivery_window_s`, `replay_window_s`, `atomic_claim`, `naturally_idempotent`. Read `required_window_s` (the maximum of the three windows), `headroom_s` and `covered`. Findings: `none` without natural idempotency is a blocker; a window shorter than `required_window_s` is a blocker (minor if naturally idempotent); a non-atomic claim is a major.
5. Fix the design with `edit` until `covered` is true from `event_idempotency_window`, then keep `headroom_s` at or above 0; if the replay window dominates, say that a replay older than `dedup_window_s` will re-run effects and point to `skill://a19-event-driven--replay-and-backfill`.
6. Implement the handler change with `edit`: claim first, effect second, claim released or marked failed when the effect throws so a retry can proceed; return the same result for a duplicate; emit structured logs with the key. Call `shared_toolchain` with `{"scan": true}` and `shared_check_plan` with `{"scan": true}`, then run exactly the commands it names through `bash`, plus a test that delivers the same event twice and one that delivers two concurrent copies.
7. Call `shared_git_head` with `{"scan": true}` and record `sha` and `branch` (the task branch is `<seat-prefix>/<task_id>`) as the base of the patch. Call `shared_artifact_digest` with `{path}` for the patch file and the tests, then see Handover.

## Outputs

- `code.patch` (handler, dedup claim, tests) with the `event_idempotency_window` fields `required_window_s`, `headroom_s`, `covered`.

## Failure Handling

- INVALID_INPUT: fix the named field (windows are seconds, at least 0) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name what was not assessed; do not claim coverage.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never hand-wave a window.
- `shared_check_plan` names no commands or a command fails: report the output; do not mark tests as passed.
- The dedup store is a new table or index: BLOCKED to A07 `needs=input`; never edit migrations. L3 applies if the change also alters a production schema.
- Exactly-once is never claimed: Kafka transactions cover Kafka-to-Kafka only; external effects need this claim.

## Handover

`to_agent`: `A09` (review of the `code.patch`; `A08`, `A10` also consume it, name them in `remaining`). Fill `goal`, `completed` (key, window, claim design), `decisions`, `artifacts` (patch and tests with digests, producer `A19`), `open_questions` (store ownership for A07), `constraints`, `verification` (tests run, from the check plan). Call `shared_handover_validate`, then `shared_handover_write`.
