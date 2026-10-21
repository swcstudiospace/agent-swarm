---
name: a07-data--schema-drift-reconcile
description: Use when A13 raises schema.drift.alert because a production schema differs from the migrations, to freeze contracts and plan a reconcile.
owner: a07-data
tools: [shared_handover_read, data_migration_version_order, data_contract_field_compat, data_reversible_pair_check, shared_scratchpad_put, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- Input is a `schema.drift.alert` from A13: production differs from the recorded migrations or contracts.
- Rule: open a reconcile task and freeze related contract versions until reconciled (prompts/A07-data.md error_handling).
- This is observed production drift; a failure during a migration in staging is `a07-data--staging-failure-recovery`.

## Procedure
1. Call `shared_handover_read` with `task_id` to read the alert (affected tables, observed columns, time of detection).
2. Run `python3 scripts/data_migration_check.py --root <repo> --task-id $TASK --correlation-id $CORR --json` (Bun twin `bun scripts/ts/data_migration_check.ts`, same flags) to list the migrations the repository declares, with their findings; a missing binary is recorded as `skipped:tool-missing`.
3. Call `data_migration_version_order` once with the `versions` of the repository migrations and once with the `versions` the alert or task inputs record as applied in production (A07 never queries production); `duplicates` or `out_of_order` in either call marks a re-applied or misordered migration, and a version present in only one list (your own set difference, the tool does not compare lists) marks a missing or skipped one.
4. Build the `before` set (expected fields from the declared contract or schema) and the `after` set (observed fields in the alert) and call `data_contract_field_compat`: `breaking` lists fields removed, newly required, retyped or made required in the observed schema, `additive` the new optional ones; both are drift.
5. Call `data_reversible_pair_check` with the `filenames` of the suspect migrations; a `missing_reverse` entry means the reconcile cannot rely on rolling back. `ok` is also false while migration-looking files sit in `unrecognized` (alembic, prisma); those need a hand check.
6. Call `shared_scratchpad_put` with the task `correlation_id`, `key` "frozen-contracts" (convention), `shared` true and `value` listing the contract ids and versions to freeze; other agents read that key from the correlation scratchpad. Nothing enforces the freeze, so repeat it under handover `constraints`.
7. With `write`, create the reconcile plan: either a forward corrective migration (version above the latest) or an update of the declared schema, with the evidence from steps 3 to 5; never edit production directly (execution goes through A11 pipelines). A07 has no task-creation tool: A01 opens the reconcile task from your handover.
8. Call `shared_artifact_digest` for the reconcile plan.

## Outputs
- Reconcile plan with evidence tables (version gaps, field differences).
- Frozen contract list and the unfreeze condition.

## Failure Handling
- Observed schema not supplied: BLOCKED `needs=input`; do not reconstruct it from memory.
- Differences that need DDL against live data: route through `a07-data--destructive-gate`.

## Handover
`to_agent` is A01 (opens the reconcile task and routes A11 execution). Fill `goal`, `completed`, `decisions` (forward fix vs declared update), `remaining` (execution via A11, unfreeze), `artifacts` (plan, digest), `constraints` (contracts frozen). Run `shared_handover_validate`, then `shared_handover_write`.
