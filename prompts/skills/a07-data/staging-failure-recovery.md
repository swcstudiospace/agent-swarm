---
name: a07-data--staging-failure-recovery
description: Use when a migration has failed in staging, to roll it back, prove the data matches the original, and write the incident note and post-mortem.
owner: a07-data
tools: [shared_handover_read, data_down_present, data_row_count_reconcile, shared_attempt_history, shared_status_report, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- A migration already failed or half-applied in staging.
- Rule: run the automatic rollback, send an incident note to A13, and write a post-mortem to memory (prompts/A07-data.md error_handling). A07 has no memory or messaging tool: the note and post-mortem are files listed as artifacts, and A01 routes them (A13 accepts none of A07's message types).
- This is recovery after a failure; the planned pre-publish check is `a07-data--rollback-rehearsal`.

## Procedure
1. Call `shared_handover_read` with `task_id`, then `shared_status_report` with `kind` progress and `step` "staging rollback started".
2. Call `data_down_present` with the failed migration text (forward and down together when one file); `reversible` false means no down marker was found, so confirm by hand that a rollback script exists; none exists means a manual restore from backup, go to Failure Handling.
3. With `bash`, run the rollback script against staging (connection details from the task inputs; never production) and record exit code and errors.
4. Count the touched tables with `bash` and call `data_row_count_reconcile` per table with `before` the count recorded before the migration, `after` the count now and `delta` 0; any non-zero `drift` is lost or duplicated data to report.
5. Call `shared_attempt_history` with `task_id` to see the earlier attempts and `last_error`; do not retry the same migration unchanged. The retry is a fix (at most two, then split).
6. With `write`, create the incident note meant for A13: migration id, failing statement, error, rollback result, `drift` per table, current staging state.
7. Write the post-mortem (cause, detection, fix, prevention check to add) and call `shared_artifact_digest` for both files.
8. If staging stays inconsistent after rollback, call `shared_escalate` with `task_id`, `reason_code` blocked, `summary` naming the tables with drift and `needs` "restore staging from backup".

## Outputs
- Rollback result with per-table `drift` values.
- Incident note (for A13) and post-mortem file with digests.
- Decision: fix and rehearse again, or split the migration.

## Failure Handling
- Rollback script fails or is not reversible: stop running scripts, restore from backup through A11, escalate.
- Counts before the failure unknown: say so; do not set `delta` from guesses.

## Handover
`to_agent` is A01 (default; routes the incident note to A13 and a re-plan to A07). Fill `goal`, `completed` (rollback, notes), `blockers` (inconsistent staging), `remaining` (fix and re-rehearse; A01 sends the incident note to A13), `artifacts` (note, post-mortem, digests), `verification` with `drift` in `checks`. Run `shared_handover_validate`, then `shared_handover_write`.
