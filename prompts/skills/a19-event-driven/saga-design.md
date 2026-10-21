---
name: a19-event-driven--saga-design
description: Use when a business flow spans services over events and needs a saga: ordered steps, compensations, one pivot, a retriable tail and a transactional outbox.
owner: a19-event-driven
tools: [event_saga_coverage, event_idempotency_window, shared_handover_read, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Saga design

## When to Use

- A flow commits in more than one service and a distributed transaction is not available (`architecture.blueprint` from A03, `data.contract` from A07).
- Output is an `event.topology.spec` section and, when asked, a `code.patch` of orchestrator or participant glue: autonomy L2 (`event_design`).
- Not for single-service idempotency (`skill://a19-event-driven--idempotent-consumer`). Data ownership and the outbox table schema belong to A07.

## Procedure

1. Call `shared_handover_read` with `{task_id: <assigner task id>}` and read the blueprint and `data.contract`: the steps, the service that owns each, and which step after which the business cannot undo (the commit point). No clear commit point is BLOCKED `needs=input` for A03.
2. Choose coordination: orchestration (one orchestrator owns state and commands; prefer for more than 3 steps or a branching flow) or choreography (participants react to events; only for 3 steps or fewer, otherwise the flow cannot be read). Record the reason.
3. Classify every step in execution order for `event_saga_coverage` as `compensable` (undoable, before the pivot), `pivot` (exactly one go or no-go step, the commit point) or `retriable` (after the pivot, must eventually succeed). For each give `compensation` (text, empty when none), `idempotent` and `compensation_idempotent`.
4. Call `event_saga_coverage` with `steps` (execution order, unique `name`, at most 100) and `outbox` (true only when the state change and the event publish are one local transaction with a relay). Read `uncompensated_steps`, `pivot`, `compensation_order` and `findings`. Blockers: a compensable step without a compensation (blank text counts as none). Majors: more than one pivot, a compensable step after the pivot, a non-idempotent compensation, a non-idempotent pivot, a non-idempotent retriable step, a retriable step before a later compensable or pivot step, no outbox. Minor: a non-idempotent compensable step.
5. Fix the design with `edit` until the `event_saga_coverage` `verdict` is not `fail`. Reorder so risky steps come first and the pivot is as late as possible; make compensations semantic (cancel, refund, release) and keyed by saga id plus step. Check `compensation_order` is the reverse of the executed compensable steps.
6. Compensation commands are redelivered like any message. For each one call `event_idempotency_window` with `key_source: "producer-event-id"`, `retry_window_s` and `redelivery_window_s` from the broker config, `replay_window_s` 0 unless a replay is planned, `atomic_claim` and `naturally_idempotent`; `covered` must be true.
7. Define timeouts and the stuck-saga rule (state, deadline, who is alerted); a timeout moves to compensation before the pivot and to retry after it. Write the saga spec (steps table, state machine, commands, events, outbox relay, timeouts) as the topology section.
8. If asked for code, implement with `edit` and run the tests the repo defines through `bash`; the outbox table or relay migration is A07's: request it in the handover. Call `shared_artifact_digest` with `{path}` per file, then see Handover.

## Outputs

- `event.topology.spec` saga section: steps, pivot, `compensation_order`, outbox decision, timeouts, tool verdicts.
- Optional `code.patch` for orchestrator and participants.

## Failure Handling

- INVALID_INPUT (duplicate step names, `steps` over 100): fix `steps` or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name the steps not assessed.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never certify coverage by hand.
- `data.contract` disagrees with the blueprint on who owns a step: E-CONTRACT, BLOCKED to A03 and A07 `needs=input`.
- A saga step that moves money or touches production data is not exercised here: only a plan; running it in production is L3 for the owners.

## Handover

`to_agent`: `A05` (implements participants; `A13` also consumes the spec, name it in `remaining`). Fill `goal`, `completed`, `decisions` (orchestration or choreography, pivot, outbox), `artifacts` (spec with digest, producer `A19`), `open_questions` (outbox migration for A07), `verification` (the `verdict` of `event_saga_coverage`). Call `shared_handover_validate`, then `shared_handover_write`.
