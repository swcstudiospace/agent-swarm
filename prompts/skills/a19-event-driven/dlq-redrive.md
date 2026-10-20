---
name: a19-event-driven--dlq-redrive
description: Use when consumers need dead-letter coverage, a retry schedule that fits the visibility timeout, and a redrive runbook; production redrive itself is L3.
owner: a19-event-driven
tools: [event_dlq_coverage, event_retry_schedule, event_retention_replay, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# DLQ and redrive

## When to Use

- A queue consumer or function has no dead-letter path, a doubtful retry policy, or a DLQ with messages to move back.
- Designing a policy and a runbook is L2 (`event_design`). Executing a redrive of a production DLQ is a production replay: L3. Purging or deleting a DLQ or its messages is L4: never do it.
- Not for re-reading old retained events (`skill://a19-event-driven--replay-and-backfill`).

## Procedure

1. Collect every consumer of the topology with `grep` and `read` over the infra and handler code. Per consumer note `name`, `queue`, `dlq`, `max_receive_count`, `fifo`, `dlq_fifo`, `source_retention_days`, `dlq_retention_days`, and whether a `redrive_runbook` and a `dlq_alarm` exist. For Kafka treat the dead-letter topic as `dlq` and the topic retention as the retention days.
2. Call `event_dlq_coverage` with `consumers` = that list (names unique). Read `covered` against `consumers`, `uncovered_consumers` and `findings`. Blockers: no DLQ, DLQ equal to the source, `max_receive_count` below 1, FIFO mismatch (a FIFO queue needs a FIFO DLQ). Majors: `max_receive_count` above 1000 (the SQS limit), no redrive runbook, DLQ retention not longer than source retention (retention counts from the original enqueue time). Minor: `max_receive_count` 1 or above 20, no DLQ alarm.
3. For each consumer call `event_retry_schedule` with `base_delay_s`, `multiplier`, `max_delay_s`, `max_attempts` (deliveries including the first), `jitter` (`full` or `equal`; `none` is a minor herd risk), `retry_mode` (`queue-redelivery` or `in-process`), `processing_timeout_s`, `visibility_timeout_s` and `budget_s`. Blockers: `processing_timeout_s` above visibility, or in-process worst case above visibility. Check `fits_visibility` and `fits_budget` are both true.
4. Cross-check `event_dlq_coverage` with `event_retry_schedule`: the `max_receive_count` from step 2 equals the `max_attempts` of step 3 for queue redelivery (the receive count is the delivery count). A difference is E-CONTRACT.
5. Write the redrive runbook: who approves, the DLQ depth to expect, the rate cap (SQS DLQ redrive to source supports a messages-per-second limit where the platform offers it; take the supported flag from the provider docs, never assume), a stop condition (new DLQ arrivals within 5 minutes means halt), and the precondition that the consumer is idempotent and the root cause is fixed.
6. For the production redrive call `event_retention_replay` with `retention_s`, `replay_from_age_s` (age of the oldest DLQ message), `replay_events` (DLQ depth), `replay_rate_per_s`, `live_rate_per_s`, `consumers_idempotent`, `compacted` false and `environment: prod`. `requires_l3_approval` true is expected; `restartable` false or a blocker (rate not above live rate, non-idempotent consumers) stops the plan.
7. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod DLQ redrive awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as the BLOCKED `task.result`. Staging redrives are L2 once steps 2 to 6 pass.
8. Write the policy and runbook files, call `shared_artifact_digest` with `{path}` for each, then see Handover.

## Outputs

- `event.topology.spec` section "reliability": per-consumer DLQ, receive count, retry schedule, alarm, redrive runbook.
- `code.patch` only when you changed consumer retry or DLQ configuration in code.

## Failure Handling

- INVALID_INPUT (`consumers` names not unique, `max_delay_s` below `base_delay_s`): fix that field or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the consumers not assessed.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Provider limits (max visibility 43200 s, receive count 1..1000) come from the tool; if a platform differs, defer to the tool finding and flag it.
- Never retry a redrive, never purge a DLQ; L3 and L4: BLOCKED `needs=human-approval`.

## Handover

`to_agent`: `A13` (alerts on DLQ depth; consumes the `event.topology.spec`) when no code changed, `A09` (review) when a `code.patch` exists; name the other and `A05` in `remaining`. Fill `goal`, `completed`, `decisions` (receive count, schedule, approver), `artifacts` (digests), `blockers` (`human-approval`), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
