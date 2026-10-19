---
name: a07-data--additive-migration
description: Use when a data.migration only adds structure (new table, nullable or defaulted column, concurrent index) and can ship at L2 without a destructive step.
owner: a07-data
tools: [shared_handover_read, data_not_null_default_check, data_index_name_check, data_lock_risk_scan, data_down_present, data_reversible_pair_check, shared_dod_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- The task asks for new tables, columns or indexes and nothing is dropped, renamed or narrowed (prompts/A07-data.md autonomy: L2 = additive migrations, new indexes).
- Not for in-place renames or type changes: use `a07-data--expand-contract-split`. Not for shadow-DB rehearsal: that is `a07-data--rollback-rehearsal`.

## Procedure
1. Call `shared_handover_read` with `task_id`; if `status` is not `ok`, stop and follow Failure Handling.
2. Run `python3 scripts/data_migration_check.py --root <repo> --task-id $TASK --correlation-id $CORR --file <new migration files> --json`, then the Bun twin `bun scripts/ts/data_migration_check.ts` with the same flags. In `findings[]`, a `kind` of `destructive:drop-table`, `destructive:drop-column`, `destructive:truncate`, `destructive:type-change-no-using` or `destructive:rename` means the change is not additive: stop and switch skill. `irreversible` and `version_issues` must be empty.
3. Call `data_not_null_default_check` with `sql` set to the forward migration (it reads only ALTER TABLE ... ADD COLUMN); every entry in `unsafe_columns` needs a DEFAULT (or a nullable column) before continuing.
4. Call `data_index_name_check` with `sql` and the `prefix` that the repository or blueprint declares (never guess it; none declared is BLOCKED `needs=input`); fix each `violations` entry (shape `prefix_table_suffix`, 63-character limit).
5. Call `data_lock_risk_scan` with `sql` and `lock_timeout_s` set to the intended `requires_lock_timeout_s`. Rewrite index builds as CONCURRENTLY and FK/CHECK additions as NOT VALID until `risks` is empty, or keep the risk and confirm `lock_timeout_declared` is true; it is true only when `lock_timeout_s` is above 0 or every risky statement runs after a non-zero `SET lock_timeout` that no `RESET`, `SET ... TO 0`, `COMMIT` (for `SET LOCAL`) or `ROLLBACK` has undone, so put the `SET` before the DDL.
6. Call `data_down_present` with the migration text as published; `reversible` is one marker signal, not proof. Then call `data_reversible_pair_check` with the migration `filenames`: `ok` must be true and `unrecognized` empty (it only understands `.up.sql`/`.down.sql` and Flyway V/U names; for other layouts rely on step 2 `irreversible` and the rollback script you wrote).
7. Call `shared_dod_check` with `task_id` and `result` an object whose `completed` lines and `checks` (`{name, status}`) cite the evidence per acceptance criterion; any `unknown` criterion is remaining work, not a claim.
8. Call `shared_artifact_digest` for the forward and rollback files, then build the handover (see Handover).

## Outputs
- schema.migration JSON per 03-agents/A07-data.md section 3 with `kind: "additive"`, `destructive: false`, `approval_required: "none"`, `requires_lock_timeout_s` set.
- Lint evidence: script findings, `unsafe_count`, `violation_count`, `risk_count`, `ok`.
- `shadow_tested` stays false until rehearsal runs (this skill does not run it).

## Failure Handling
- Tool INVALID_INPUT: fix the named field once; a second failure is BLOCKED `needs=input`.
- DEPENDENCY_UNAVAILABLE or TIMEOUT: retry once, then record the check as skipped and set verification status `partial`.
- Script binary missing: record `skipped:tool-missing`; never invent findings.

## Handover
`to_agent` is the agent id in the task routing (default A01; ids like A09, not slugs). Fill `goal`, `completed` (checks that passed), `remaining` (shadow rehearsal), `artifacts` (forward and rollback URIs with digests, producer A07), `constraints` (lock timeout), `verification` (`status` partial until rehearsal, with `checks`). Call `shared_handover_validate`, then `shared_handover_write`.
