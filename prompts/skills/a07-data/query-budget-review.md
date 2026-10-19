---
name: a07-data--query-budget-review
description: Use when data.access.patterns arrive or a query p95 budget is breached, to design the index set and prove it with an EXPLAIN comparison.
owner: a07-data
tools: [shared_handover_read, data_index_name_check, data_lock_risk_scan, data_explain_regression_check, shared_open_questions, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- A05 supplies `data.access.patterns`, or a bound query exceeds its A03 budget (prompts/A07-data.md decision_logic 5: queries are bound by A03 budgets).
- The output is an index and access-path design plus evidence, not a rehearsed migration.

## Procedure
1. Call `shared_handover_read` with `task_id`; extract each query, its filter and sort columns and its budget.
2. Call `shared_open_questions` with `task_id`; unanswered questions about row counts or frequency are listed, not guessed.
3. With `write`, map every query to a candidate index (equality columns first, then range, then sort), noting which queries one index serves and which writes it taxes.
4. Write the `CREATE INDEX CONCURRENTLY` statements and call `data_index_name_check` with their `sql` and the `prefix` the repository or blueprint declares (never guess it); resolve all `violations`.
5. Call `data_lock_risk_scan` with the same `sql`; `risks` must be empty, so builds stay non-blocking.
6. Measure each query on the shadow DB with `bash` (EXPLAIN before and after the index; the shadow DB and its connection come from the task inputs) and call `data_explain_regression_check` with `queries` (`id`, `baseline`, `shadow`, baseline above 0); a query in `regressions` (more than 20 % slower) goes back to step 3 and the index is rejected or reshaped. The tool does not know the A03 budget: compare each `shadow` value to its budget yourself and list any query still over it.
7. Call `shared_artifact_digest` for the index migration and the EXPLAIN table file.

## Outputs
- Index set with names, columns, and the queries each serves.
- EXPLAIN table: per query baseline, shadow and your computed change; the tool gives `pct` only for `regressions`, plus `max_pct` and `within_budget`.
- Open assumptions (volumes, frequencies) flagged for A05 or A03.

## Failure Handling
- No access patterns provided: BLOCKED `needs=input`; do not invent queries.
- A budget cannot be met by indexing alone: record the gap, propose a denormalisation (needs an ADR and L3), and hand back instead of publishing.
- Tool INVALID_INPUT: fix the named field once, else BLOCKED `needs=input`.

## Handover
`to_agent` is the agent id in the task routing (default A01; A05 or A03 for open assumptions). Fill `goal`, `completed` (queries covered), `decisions` (index choices with rationale and write cost), `artifacts` (index migration, EXPLAIN table, digests), `open_questions`, `verification` with `max_pct` in `checks`. Run `shared_handover_validate`, then `shared_handover_write`.
