---
name: a02-requirements--conflict-resolution
description: Use when two requirements contradict; publish the conflict matrix, exclude blocked items with rationale and escalate when they block the critical path.
owner: a02-requirements
tools: [shared_handover_read, req_conflict_matrix, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Conflict Resolution

Handles contradictory content (spec section 5: publish a conflict matrix; blocked items leave the backlog with a
rationale; ESCALATED if they block the critical path). It does not rank a healthy backlog; that is
`a02-requirements--backlog-prioritization`.

## When to Use

- A reviewer, stakeholder or A03 reports that two statements cannot both hold (for example latency under 300 ms and over 500 ms).
- A new requirement touches a subject and attribute that already has a value.

## Procedure

1. Call `shared_handover_read` with `task_id` to get the reported pair and the requirement ids in `handover`.
2. Use `read` on `docs/requirements/nfr.md` and `docs/requirements/stories.md` (convention paths) and `grep` for the conflicting attribute to collect every statement about it.
3. Normalise each statement into a record: `id`, `subject`, `attribute`, `op` (one of eq, ne, lt, le, gt, ge), `value` as text and `priority` (stakeholder rank, 1 is highest; the tool lets the higher number yield) when the source stakeholder is known. Records are only compared when `subject` and `attribute` match, ignoring case.
4. Call `req_conflict_matrix` with `requirements` set to those records. Read `conflicts` (each with `kind` value, polarity or bounds), `blocked_ids`, `equal_rank` and `needs_escalation`.
5. Use `write` to publish the matrix to `docs/requirements/conflicts.md` (convention path): one row per conflict with both ids, the attribute and `kind`. Call `shared_artifact_digest` with that `path`.
6. Use `edit` on the backlog to remove every id in `blocked_ids`, leaving a line "excluded: <id>, conflicts with <id>, rationale <text>" for each.
7. If an id in `blocked_ids` sits on the critical path, or `equal_rank` is not empty, call `shared_escalate` with `task_id`, `reason_code` `conflict`, `summary` naming the pairs, and `needs` `["human-approval: scope_commitment"]`, and finish BLOCKED with the returned `result_patch`. A lower-rank loser is resolved by the matrix; equal rank is L3.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs

- Published conflict matrix and a trimmed backlog with rationale per exclusion.
- Escalation record when the conflict blocks the critical path or ranks are equal.

## Failure Handling

- `req_conflict_matrix` INVALID_INPUT: a bound operator (lt, le, gt, ge) needs a numeric `value`; restate the requirement or treat it as text with eq.
- No conflict returned but the reporter insists: ask for the exact sentences through `open_questions`; do not remove anything.
- Tool TIMEOUT: retry once, then E-TIMEOUT.

## Handover

Fill `decisions` (each exclusion and its rationale), `blockers` (items awaiting a human decision),
`remaining` (re-run the matrix after the decision), `artifacts` (kind conflicts, the saved path, its digest, `producer` `A02`) and `verification`
(`checks`: matrix counts). Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).
