---
name: a02-requirements--change-control
description: Use when requirements change after the spec is PLANNED; estimate impact, raise a requirements.change and require approval above 20% scope change.
owner: a02-requirements
tools: [shared_handover_read, req_change_impact, req_traceability_coverage, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Change Control

Forward-looking change after PLANNED (spec section 4, rule 4): a `requirements.change` with an impact estimate
goes to A01 for re-planning, and a change above 20 % of committed stories needs human approval (L3). Defects
that reveal a wrong requirement are a different trigger (`a02-requirements--escape-retro`).

## When to Use

- A `change.request` or stakeholder feedback alters, adds or removes a committed story or NFR.
- Not before PLANNED: before that, edit the spec directly.

## Procedure

1. Call `shared_handover_read` with `task_id`; take the change text from `handover.goal` and the committed ids from `handover.constraints` or the spec files (`docs/requirements/stories.md`, `nfr.md`; convention paths).
2. Find task links for each affected id: `grep` `.swarm/plans/*.json` for the id (plan tasks carry it in `title` or `acceptance`), giving `{requirement_id, task_id}`, and `read` the traceability matrix `docs/requirements/traceability.md` (convention path, written by `a02-requirements--publish-gate`) for the criterion and artifact links.
3. Call `req_traceability_coverage` with `requirement_ids` set to the affected ids and `links` as `{requirement_id, artifact_id}`. Any id in `uncovered` has no known task or artifact: say so in the impact note instead of assuming zero impact.
4. Call `req_change_impact` with `affected_ids`, `committed_ids` (the full committed scope, at least one id) and `task_links`. Read `tasks_at_risk`, `affected_tasks`, `scope_change_ratio` and `needs_human_approval`.
5. Build the change record from the `req_change_impact` result: `change_id` `RC-<n>` (next unused number among `docs/requirements/changes/`), `affected` ids, `impact_estimate` holding `tasks_at_risk` and the ratio, `approved_by` null and `change_state` `proposed` (field names as in the A02 prompt output_format).
6. If `needs_human_approval` is true (ratio above 0.2), call `shared_escalate` with `task_id`, `reason_code` `policy`, `summary` giving the ratio, and `needs` `["human-approval: scope_change"]`; `change_state` stays `proposed` and the task finishes BLOCKED with the returned `result_patch`. If false, set `change_state` `approved` with `approved_by` null and say in the note that the ratio is within the 20 % L2 limit; `approved_by` carries `human:<who>` only after a human answered.
7. Use `write` to save the record to `docs/requirements/changes/RC-<n>.md`, call `shared_artifact_digest` with that `path`, then `shared_handover_validate` with `{document, draft: true}` and `shared_handover_write`.

## Outputs

- `requirements.change` record with impact estimate and approval state.
- Affected task list for A01 re-planning, ratio and approval flag cited from `req_change_impact`.

## Failure Handling

- `req_change_impact` INVALID_INPUT (empty `committed_ids`): the scope is unknown; BLOCKED needs=input.
- Affected id with no task link: list it under `open_questions`; do not drop it.
- DEPENDENCY_UNAVAILABLE: retry at most twice, then E-DEP; never estimate impact by hand.
- UNAUTHORIZED from `shared_escalate`: stop and report the refusal.

## Handover

Fill `goal` (the change), `decisions` (approval route and why), `blockers` (human approval pending when above
20 %), `remaining` (re-plan by A01 once approved), `artifacts` (kind change, the record path, its digest, `producer` A02) and `verification`
(`checks`: ratio and `tasks_at_risk`). Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).
