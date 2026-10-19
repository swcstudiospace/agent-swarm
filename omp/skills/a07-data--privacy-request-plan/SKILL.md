---
name: "a07-data--privacy-request-plan"
description: "Use when a privacy.request (GDPR/CCPA deletion or portability) arrives, to locate subject data, order cascades and prepare a plan a human executes at L4."
disable-model-invocation: true
---

## When to Use
- The input is a `privacy.request` for erasure or portability of one data subject's records.
- Rule: GDPR/CCPA deletion execution is L4, so A07 prepares the plan and a human approves and executes it (prompts/A07-data.md decision_logic 4, autonomy L4).
- Row-level subject data only; structure changes are `a07-data--destructive-gate`, and tagging new fields is `a07-data--pii-classification`.

## Procedure
1. Call `shared_handover_read` with `task_id` for the request: subject key, type (erasure or portability), systems in scope.
2. Call `data_pii_tag_coverage` with `columns` (`name` as unique `table.column`, `pii`, `tag`) from the data catalog and `allowed_tags`; the `untagged` list shows PII columns whose content cannot be located reliably. Report them as gaps.
3. Build the foreign-key `edges` (`from`, `to`) of the tables holding the subject's rows and call `data_fk_cycle_check`; if `has_cycle` is true, plan a nullable-out step on the `cycle` before deletion, otherwise order deletes child first.
4. Write the deletion or export statements as a parameterised plan (subject key as a parameter), each with a `SELECT count(*)` guard, a retention or legal-hold exception list and an anonymisation alternative where deletion is blocked by retention.
5. Call `data_destructive_ddl_scan` with the plan `sql`; it must report no DDL (a deletion plan is DML only). `destructive` true means the plan is wrong and must be rewritten.
6. State the expected rows per table from the counts in the task inputs (A07 does not query production) and call `data_row_count_reconcile` with `before`, the `after` the plan predicts and the planned `delta`, to check the arithmetic; the human reruns it with the observed `after` once the plan has run, and a non-zero `drift` is a failed run.
7. Call `shared_artifact_digest` for the plan file, then `shared_escalate` with `task_id`, `reason_code` policy, `summary` "L4 privacy deletion plan ready" and `needs` containing `human-approval: privacy_deletion`.

## Outputs
- Executable-by-human plan: ordered statements, count guards, expected counts, exceptions.
- BLOCKED result with `needs=human-approval`; nothing executed.

## Failure Handling
- Subject key missing or ambiguous: BLOCKED `needs=input`, never widen to a pattern match.
- FK data unavailable: state the gap; do not guess the cascade order.

## Handover
`to_agent` is A01 (it routes the human approval). Fill `goal`, `completed` (plan prepared), `blockers` (L4 approval), `remaining` (human execution, post-run count check), `artifacts` (plan file, digest), `constraints` (A07 does not execute). Run `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/privacy-request-plan.md; edit the source and run scripts/build_agents.py -->
