---
name: "a02-requirements--backlog-prioritization"
description: "Use when a drafted backlog needs an order; choose MoSCoW or WSJF by size and escalate equal-rank stakeholder ties with an option matrix."
disable-model-invocation: true
---

# Backlog Prioritization

Orders validated stories. MoSCoW is the default; WSJF applies above 20 stories (spec section 4, rule 3).
Contradictory content is not decided here (`a02-requirements--conflict-resolution`); only ranking is.

## When to Use

- The story list passed INVEST and has criteria, and no ranked backlog exists, or stakeholder scores changed.
- A scope-limited release needs a cut line.

## Procedure

1. Call `shared_handover_read` with `task_id`; take the story ids and any stakeholder scores from `handover.decisions`.
2. Use `read` on `docs/requirements/stories.md` (convention path) and count the `US-` ids. Record the count.
3. Use `read` on the stakeholder inputs named in the handover and collect for each story the four numbers: `business_value`, `time_criticality`, `risk_reduction` (relative scores, same scale for all) and `job_size` (above zero). A missing number goes to `open_questions`, not a guess.
4. Call `req_wsjf_rank` with `items` as `{id, business_value, time_criticality, risk_reduction, job_size}`. Read `method` in the result: the tool names `WSJF` for more than 20 items, otherwise `MOSCOW`, and `ranked` is always the WSJF order.
5. If `method` from `req_wsjf_rank` is `WSJF`, use `ranked` as the order. If it is `MOSCOW`, assign Must, Should, Could or Won't per story from the stakeholder statements, and use `ranked` only to order stories inside each class.
6. Where two stakeholders of equal rank rank the same stories differently, build an option matrix (options as rows, stakeholders and impact as columns) and call `shared_escalate` with `task_id`, `reason_code` `conflict`, `summary` naming the stories, and `needs` `["human-approval: scope_commitment"]`. The tie is L3: do not break it yourself, and finish BLOCKED with the returned `result_patch`.
7. Use `write` to save the ordered backlog to `docs/requirements/backlog.md` (convention path), call `shared_artifact_digest` with that `path`, then `shared_handover_validate` with `{document, draft: true}` and `shared_handover_write`.

## Outputs

- Ranked backlog with `method` stated, MoSCoW class or WSJF value per story.
- Option matrix for each unresolved equal-rank tie.

## Failure Handling

- `req_wsjf_rank` INVALID_INPUT (non-unique ids, `job_size` not above 0): fix the named item; never drop it to make the call pass.
- Stakeholder scores unavailable: publish the order as provisional and say which input was assumed.
- Tool TIMEOUT: retry once, then E-TIMEOUT; an order computed by hand is not reported as tool output.

## Handover

Fill `completed` (ranked list, method), `decisions` (class assignments and why), `open_questions` (missing scores,
unresolved ties), `artifacts` (kind backlog, the saved path, its digest, `producer` A02) and `verification` (`checks`: the `req_wsjf_rank`
result). Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).

<!-- generated from prompts/skills/a02-requirements/backlog-prioritization.md; edit the source and run scripts/build_agents.py -->
