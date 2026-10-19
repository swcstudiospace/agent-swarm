---
name: "a07-data--destructive-gate"
description: "Use when a DDL scan still reports DROP, TRUNCATE or a type change that cannot be split away, so it is prepared and not executed pending human approval."
disable-model-invocation: true
---

## When to Use
- `data_destructive_ddl_scan` returns `destructive` true after `a07-data--expand-contract-split` found no way to avoid it.
- Rules: destructive changes are L3 minimum, the `destructive_ddl` ceiling is L4, and A07 prepares but never executes (prompts/A07-data.md decision_logic 3; autonomy "Never").
- Not for GDPR/CCPA row deletion: that is `a07-data--privacy-request-plan`.

## Procedure
1. Call `shared_handover_read` with `task_id`; confirm the destructive change is the stated goal.
2. Call `data_destructive_ddl_scan` with `sql` set to the forward migration; copy `kinds` and `destructive_count` into the evidence.
3. Prepare the backup step (what is copied, where, restore command) and the rollback script; call `data_down_present` with the migration text as published. `reversible` is only a marker signal; the proof is the rollback script file plus the rehearsal in `a07-data--rollback-rehearsal`.
4. State the expected data loss as a row count: measure `before` on the shadow DB with `bash`, run the forward script there, measure `after`, and call `data_row_count_reconcile` with `before`, `after` and `delta` set to the stated loss (negative). `reconciled` must be true so the loss is explicit and bounded; with no shadow DB, record the loss as unmeasured and set verification `partial`.
5. Call `shared_artifact_digest` for the forward, rollback and backup-plan files.
6. With `write`, set `destructive: true`, `approval_required: "L4"` (L3 when only retention or narrowing applies) and `shadow_tested` per the rehearsal result.
7. Call `shared_status_report` with `kind` blocked and the step name, then `shared_escalate` with `task_id`, `reason_code` policy, `summary` naming the kinds, and `needs` containing `human-approval: destructive_ddl`.

## Outputs
- Prepared forward, rollback and backup plan files; never an executed statement.
- Evidence bundle: scan `kinds`, reconcile numbers, digests.
- BLOCKED result patch from `shared_escalate` with `needs=human-approval: destructive_ddl`.

## Failure Handling
- Backup or rollback missing: do not escalate for approval yet; complete them first, or BLOCKED `needs=input`.
- `data_row_count_reconcile` INVALID_INPUT on `delta`: the stated loss is larger than the table; correct the figure.
- `data_row_count_reconcile` INVALID_INPUT on `after` (before above 0, after 0, loss equal to `before`): the tool refuses to call a total loss reconciled because a dropped table and an emptied table both read zero. Take the table-drop proof from `data_destructive_ddl_scan` (`DROP_TABLE`) and the backup plan, record the loss as the whole table, and call the tool only for partial losses.
- `shared_escalate` fails: retry once, then report the same BLOCKED text in the final message.

## Handover
`to_agent` is A01 (it routes the human approval and the A11 execution). Fill `goal`, `completed`, `blockers` (the approval needed), `remaining` (human execution through A11 pipelines), `artifacts` with digests, `constraints` (never executed by A07), `verification` status `partial`. Run `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/destructive-gate.md; edit the source and run scripts/build_agents.py -->
