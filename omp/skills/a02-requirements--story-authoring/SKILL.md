---
name: "a02-requirements--story-authoring"
description: "Use when a cleaned brief is ready and user stories must be drafted, scored for INVEST and de-duplicated before criteria are written."
disable-model-invocation: true
---

# Story Authoring

Turns the cleaned brief into a backlog of INVEST-passing stories with stable ids. It is story-level only:
acceptance criteria belong to `a02-requirements--acceptance-criteria`, ordering to `a02-requirements--backlog-prioritization`.

## When to Use

- The intake handover shows the brief is cleaned and no `user.story` list exists, or a story failed a gate.
- Stable ids matter: an existing `US-` id keeps its number across revisions (stability index at least 0.85, spec section 6).

## Procedure

1. Call `shared_handover_read` with `task_id`; take the cleaned brief path from `handover.artifacts` and the open questions from `handover.open_questions`.
2. Use `read` to load the cleaned brief and `grep` for `US-` in `docs/requirements` (convention path for A02's files) to find the highest existing id; continue numbering from there and never reuse an id.
3. Draft each story (saved with `write` in step 7) as "As a <role>, I want <capability>, so that <benefit>" with a `US-<n>` id, a dependency list and a point estimate. Describe what and how well, never how.
4. Call `req_invest_score` once per story with `story` (the text), `dependencies`, `estimate_points` and any `criteria` already written. Read `score` and `failed`. The letters are checked mechanically: I (no dependencies listed), N (no solution words such as api, endpoint, sql, queue, json, table, redis, using, via), V ("so that" or "in order to" present), E (an estimate given), S (estimate at most 8), T (at least one criterion supplied).
5. Fix every failed letter with `edit` on the story (split a story that is not Small; remove solution wording that fails Negotiable) and re-score; stop after two fix loops per story and move the remainder to `open_questions`. A `T` failure is expected until `a02-requirements--acceptance-criteria` has run and is not a defect here.
6. Call `req_story_duplicates` with `stories` as an array of `{id, text}` covering the new and the existing backlog, `threshold` left at its default (0.6). For each pair returned keep the older id and merge the newer text into it.
7. Use `write` to save the stories to `docs/requirements/stories.md` (convention path), one section per `US-` id, call `shared_artifact_digest` with that `path`, then `shared_handover_validate` with `{document, draft: true}` and `shared_handover_write`.

## Outputs

- Story list `user.story` with ids, text, dependencies, estimates, and the INVEST result per story.
- A duplicate report (pairs found and how each was merged).
- Stories that could not pass INVEST, listed with the unresolved check.

## Failure Handling

- `req_invest_score` INVALID_INPUT: name the story id, fix the field once, otherwise BLOCKED needs=input.
- `req_story_duplicates` INVALID_INPUT for non-unique ids: renumber the offender, never drop it.
- Any tool TIMEOUT: retry once, then E-TIMEOUT; a story without a score is not published.
- Brief missing or unredacted: stop and send the task back to `a02-requirements--brief-intake`.

## Handover

Fill `completed` (stories passing INVEST, with ids), `remaining` (criteria per story, NFRs, ordering),
`decisions` (each merge or split with its rationale), `artifacts` (kind stories, the saved path, its digest, `producer` `A02`), `open_questions`
(stories left failing) and `verification` (`status` partial until criteria exist; `checks` names both tools). Call
`shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).

<!-- generated from prompts/skills/a02-requirements/story-authoring.md; edit the source and run scripts/build_agents.py -->
