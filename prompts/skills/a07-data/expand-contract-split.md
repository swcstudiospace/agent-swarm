---
name: a07-data--expand-contract-split
description: Use when a requested change renames, retypes or drops a column in place and must be rewritten as ordered expand, backfill and contract migrations.
owner: a07-data
tools: [shared_handover_read, data_destructive_ddl_scan, data_migration_version_order, data_not_null_default_check, data_reversible_pair_check, shared_open_questions, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- The brief or a draft migration contains RENAME, `ALTER COLUMN ... TYPE`, DROP COLUMN or a NOT NULL tightening on a populated table.
- Rule: prefer expand, migrate data, contract over in-place changes (prompts/A07-data.md decision_logic 2).
- If the destructive step cannot be avoided at all, hand off to `a07-data--destructive-gate` after the split.

## Procedure
1. Call `shared_handover_read` with `task_id` to learn the target schema and consumers.
2. Call `data_destructive_ddl_scan` with `sql` set to the draft in-place change; record the `kinds` found. These become the contract phase.
3. Call `shared_open_questions` with `task_id`; list consumers (A05/A06 readers) still unanswered, because the contract phase must wait until they have moved off the old column. With `grep` search the repository for the old column name: the contract phase may be scheduled only when no reader remains. Convention: this is the "none-affected" check of prompts/A07-data.md decision_logic 2; there is no tool for it.
4. With `write`, create three ordered phases: expand (add the new column or table, nullable or with DEFAULT, kind `expand`), migrate data (backfill in batches, kind `expand`), contract (drop the old structure, kind `contract`, only after the none-affected check passes).
5. Call `data_not_null_default_check` with the expand phase `sql`; `safe` must be true, otherwise add a DEFAULT or keep the column nullable until backfill ends.
6. Call `data_migration_version_order` with the `versions` of all three phases; `ordered` must be true and `duplicates` empty, expand lowest, contract highest.
7. Call `data_reversible_pair_check` with the `filenames` of all phases; each phase needs its reverse script. `ok` is false when any migration-looking file it cannot pair (alembic `.py`, prisma `migration.sql`) is listed in `unrecognized`; check those by hand and record the result, since the tool cannot pair them.
8. Call `data_destructive_ddl_scan` again on each phase separately: only the contract phase may report `destructive` true.
9. Call `shared_artifact_digest` for each migration file and assemble the handover.

## Outputs
- Three (or more) schema.migration records with distinct `version` and `kind` (`expand` or `contract`), contract carrying `approval_required` L3 or L4.
- Phase plan stating the consumer wait condition for the contract phase.

## Failure Handling
- A phase still scans destructive before the contract step: split it again (one more expand step) rather than lowering the bar.
- No consumer answer: leave contract unscheduled and list the question in `open_questions`.
- Tool INVALID_INPUT: correct the named field once, else BLOCKED `needs=input`.

## Handover
`to_agent` is the agent id in the task routing (default A01). Fill `goal`, `completed` (phases written), `decisions` (why each phase is ordered so, with rationale), `remaining` (consumer migration, contract approval), `artifacts` (each file, digest), `open_questions` (consumers), `constraints`. Run `shared_handover_validate`, then `shared_handover_write`.
