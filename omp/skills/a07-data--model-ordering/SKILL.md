---
name: "a07-data--model-ordering"
description: "Use when a new or changed data.model (ERD) needs table dependency order, FK indexes and migration sequencing, with an ADR for any denormalisation."
disable-model-invocation: true
---

## When to Use
- A new entity model or ERD change is requested from requirements and the A03 blueprint.
- Rules: 3NF by default for OLTP; denormalise only with a measured access pattern and an ADR reference (L2 with rationale; prompts/A07-data.md decision_logic 1).
- Design only; migration safety lint is `a07-data--additive-migration`.

## Procedure
1. Call `shared_handover_read` with `task_id` to get the requirements, blueprint and capacity model.
2. Call `shared_open_questions` with `task_id`; open questions about cardinality or ownership are carried forward, not assumed.
3. With `write`, draft the logical model in 3NF; list every foreign key as an edge (`from` child, `to` parent).
4. Call `data_fk_cycle_check` with `edges`; `has_cycle` true means break the loop (nullable FK, link table, or deferred constraint) and re-run until it is false. Use `allow_self_reference` only for a deliberate tree such as a category parent.
5. With `write`, derive the create order from the parents-first edges and assign each creating migration a `version` in that order.
6. Call `data_migration_version_order` with the `versions`; `ordered` true and `duplicates` empty.
7. Write one index per foreign key as `CREATE INDEX` text and call `data_index_name_check` with `sql` and the `prefix` the repository or blueprint declares (never guess it; none declared is BLOCKED `needs=input`); fix the `violations`.
8. With `write`, for any proposed denormalisation attach the measured access pattern and the ADR reference (A03 owns ADRs; ask for it through `open_questions`); without both, revert to 3NF. The spec calls this L2 with rationale but the prompt autonomy list L3: put the rationale in `decisions` and the level question in `open_questions`.
9. Call `shared_artifact_digest` for the ERD file and the migration order file.

## Outputs
- data.model: ERD file plus entity list with keys and constraints.
- Migration order table (version, tables created) and the FK index list.
- ADR references for each denormalisation.

## Failure Handling
- Unbreakable cycle: record the options and escalate to A03 through `open_questions` rather than inventing a constraint.
- Name violations after one fix pass: BLOCKED `needs=input` with the offending names.

## Handover
`to_agent` is the agent id in the task routing (default A01; A03 when an ADR is needed). Fill `goal`, `completed`, `decisions` (3NF choices, cycle break, denormalisations with ADR), `artifacts` (ERD, order file, digests), `open_questions`. Run `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/model-ordering.md; edit the source and run scripts/build_agents.py -->
