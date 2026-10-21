---
name: "a07-data--rollback-rehearsal"
description: "Use when about to set shadow_tested true: run forward then rollback in the shadow DB, reconcile row counts and check the EXPLAIN regression budget."
disable-model-invocation: true
---

## When to Use
- A forward and a rollback script exist and the schema.migration still has `shadow_tested` false.
- `shadow_tested` is true only when forward and rollback both ran in the ephemeral shadow DB and the EXPLAIN regression is at most 20 % (prompts/A07-data.md operating procedure step 5).
- This is rehearsal; static linting belongs to `a07-data--additive-migration`, and a failure that already happened in staging to `a07-data--staging-failure-recovery`.

## Procedure
1. Call `shared_handover_read` with `task_id`, then `shared_attempt_history` with `task_id`; if `retries_left` is 0 or the fix loop already ran twice, go to Failure Handling.
2. Call `shared_status_report` with `kind` progress and `step` "shadow forward".
3. With `bash` against the shadow DB (provisioned by A11 environments; its connection details come from the task inputs), count rows of every touched table, run the forward script, count again. Call `data_row_count_reconcile` with `before`, `after` and the expected `delta`; `reconciled` must be true.
4. Only if the forward `sql` changed during the fix loop, re-run `data_lock_risk_scan` as in `a07-data--additive-migration` step 5; the scan is static, it measures no lock wait.
5. With `bash`, run EXPLAIN for each bound query before and after the forward script; call `data_explain_regression_check` with `queries` (`id`, `baseline`, `shadow`) and the default `threshold_pct` 20; `within_budget` must be true.
6. With `bash`, run the rollback script, recount the same tables and call `data_row_count_reconcile` with `before` equal to the post-forward count, `after` the post-rollback count and `delta` its expected reversal; the final count must equal the original count from step 3.
7. On any failure (re-run through `bash`) apply the fix loop at most twice (re-run steps 3 to 6), then split the migration (see `a07-data--expand-contract-split`).
8. Call `shared_artifact_digest` for the rehearsal log file and set `shadow_tested` accordingly.

## Outputs
- `shadow_tested` true or false with drift values per table and the EXPLAIN table (`regressions`, `max_pct`).
- Rehearsal log file with digest.

## Failure Handling
- Third failure: stop looping, split the migration, report state ESCALATED and put the plan options in the handover `decisions` and `remaining` for A01.
- `data_explain_regression_check` INVALID_INPUT (baseline 0 or duplicate id): fix the measurement, do not drop the query.
- Shadow DB unavailable: BLOCKED `needs=tool`, `shadow_tested` stays false.

## Handover
`to_agent` is the agent id in the task routing (default A01). Fill `completed` (steps run), `decisions` (fix-loop changes), `artifacts` (rehearsal log, digest), `verification` (`status` verified only when both directions reconciled, `checks` listing drift and `max_pct`). Run `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/rollback-rehearsal.md; edit the source and run scripts/build_agents.py -->
