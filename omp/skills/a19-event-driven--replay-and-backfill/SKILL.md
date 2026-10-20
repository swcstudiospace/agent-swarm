---
name: "a19-event-driven--replay-and-backfill"
description: "Use when retained events must be replayed or a consumer backfilled; check retention, catch-up time and idempotence, and get the L3 gate for production."
disable-model-invocation: true
---

# Replay and backfill

## When to Use

- A consumer must reprocess retained events (bug fix, new projection, new consumer) or a stream must be backfilled. Planning and any non-production rehearsal: L2 (`event_design`).
- Replay or backfill in production is `prod_replay_or_backfill`: L3. You prepare plan and runbook; a human approves and the owners run it. Deleting topics, offsets groups or data is L4: never.
- Not for moving poisoned messages out of a DLQ (`skill://a19-event-driven--dlq-redrive`).

## Procedure

1. Read the task and the topic config with `read` and `grep`: `environment` (`prod` or `non-prod`), the source and its retention (Kafka `retention.ms` or compaction, Kinesis and SQS retention periods differ per service and are set per resource: read the configured value, never the default), the oldest event to process, the number of events, the maximum consumer rate and the live arrival rate. Missing figure: BLOCKED `needs=input`.
2. Call `event_retention_replay` with `retention_s`, `replay_from_age_s`, `replay_events`, `replay_rate_per_s`, `live_rate_per_s`, `consumers_idempotent`, `compacted`, `environment`. Read `replay_duration_s` (events divided by replay rate minus live rate; -1 when never), `retention_headroom_s`, `restartable` and `requires_l3_approval`. Blockers: replay rate not above the live rate (it never catches up); `replay_from_age_s` beyond retention (events are gone); non-idempotent consumers. Major: `age + duration` beyond retention, so a failed run cannot restart from the same offset. A compacted topic yields the latest value per key, not history: it rebuilds state only.
3. Check duplicates with `event_idempotency_window`: `key_source`, `dedup_window_s`, `retry_window_s`, `redelivery_window_s`, `replay_window_s` = `replay_from_age_s`, `atomic_claim`, `naturally_idempotent`. A replay older than `dedup_window_s` re-runs every effect: `covered` must be true, otherwise the consumer needs a bigger window or a natural idempotent write first (`skill://a19-event-driven--idempotent-consumer`). Side effects that cannot be made idempotent (emails, payments) must be suppressed in replay mode by an explicit flag.
4. Protect live traffic: call `event_lag_assessment` with `lag_msgs` = `replay_events`, `arrival_msgs_per_s` = `live_rate_per_s`, `consumer_count`, `per_consumer_msgs_per_s` and `slo_s` of the live flow. A replay in the same consumer group that makes the live delay exceed `slo_s` (`slo_breach` true) must use a separate consumer group and an own rate limit instead. Cross-check: the effective replay rate must stay at or below the lower of the consumers' and the downstream capacity.
5. Rehearse in non-production first: Kafka offsets are reset only on a stopped group and previewed with `kafka-consumer-groups.sh --bootstrap-server <host> --group <group> --topic <topic> --reset-offsets --to-datetime <ISO instant> --dry-run` (use `--execute` only in a non-production rehearsal approved in the task); never in production by you. Record the observed counts against `replay_events`.
6. Write the runbook: scope (topic, partitions, time range), separate consumer group, rate cap, effect suppression flags, success check (count and checksum reconciliation with the target), stop and rollback (stop the group, restore the projection from its backup or rebuild it, keep the source untouched), restart rule when `restartable` is false (do not start), approver and window.
7. For `requires_l3_approval` true call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod replay or backfill awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as the BLOCKED `task.result`. For `non-prod` it is L2 once steps 2 to 4 pass.
8. Call `shared_artifact_digest` with `{path}` for the plan and runbook, then see Handover.

## Outputs

- `event.topology.spec` section "replay": plan, figures from the three tools, runbook, approval request.

## Failure Handling

- INVALID_INPUT: fix the named field or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name the gap.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no go decision without step 2.
- Retention and offset-expiry behaviour differ by broker and version: when unsure, defer to the tool output and the configured values.
- Never retry a replay run or an escalation; L3 and L4 are BLOCKED `needs=human-approval`.

## Handover

`to_agent`: `A05` (owners of the consumer; `A13` watches lag and also consumes the spec, name it in `remaining`). Fill `goal`, `completed`, `decisions`, `artifacts` (digests), `blockers` (`human-approval`), `constraints` (rate cap, window, separate group), `verification` (`partial`: planned, not run in production). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a19-event-driven/replay-and-backfill.md; edit the source and run scripts/build_agents.py -->
