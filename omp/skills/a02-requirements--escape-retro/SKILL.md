---
name: "a02-requirements--escape-retro"
description: "Use when a test escape or incident.alert points at a missing or wrong requirement; find the root cause in the criteria and draft a requirements.change."
disable-model-invocation: true
---

# Escape Retro

Backward-looking: a defect or incident reached a later stage, and the question is whether the requirement was
unclear, untested or absent. It is triggered by `test.results`, `test.escape.feedback` or `incident.alert`, never
by a new request. The output is a root cause and a change proposal, not a decision to ship.

## When to Use

- A08 or A13 reports an escape, or a failing gate verdict names an `AC-` or `NFR-` id.
- Not for a stakeholder-initiated change (`a02-requirements--change-control`).

## Procedure

1. Call `shared_handover_read` with `task_id` to read the A08 or A13 payload from `handover`: failing check, evidence, the ids it names and the id of the failing task (`failing_task`, below).
2. Call `shared_open_findings` with `{task_id: <failing_task>}` to list the unexpired findings of failing verdicts on that task, most severe first; keep gate, issuing agent and severity. The retro task itself has no verdicts.
3. Call `shared_attempt_history` with `{task_id: <failing_task>}` to see how many rework loops already ran and the last error, so the retro does not repeat a fix that failed.
4. Use `read` on `docs/acceptance.md` and `grep` for each named `AC-` id to get the criterion text and its story.
5. Call `req_traceability_coverage` with `requirement_ids` set to every `AC-` id of the spec and `links` as `{requirement_id, artifact_id}` from the traceability matrix `docs/requirements/traceability.md` (convention path). A failing id in `uncovered` has no test linked: cause class `untested`. A failing id absent from `docs/acceptance.md` altogether is cause class `missing`; an id in `unknown_requirement_ids` is a stale link to a criterion that no longer exists.
6. Call `req_ambiguity_scan` with `text` set to the offending criterion or story. A flagged sentence with modal or vague words marks cause class `ambiguous`.
7. Call `req_criteria_testability` with `criteria` as `{id, text, automated}` for the story's criteria. `unmeasurable_ids` containing the failing id marks cause class `unmeasurable`.
8. Use `write` to save the retro (cause class per id, evidence per tool call, proposed corrected wording) to `docs/requirements/retro-<task_id>.md` (convention path) and call `shared_artifact_digest` with that `path`. Draft a `requirements.change` from it (`a02-requirements--change-control` carries it forward), then call `shared_handover_validate` with `{document, draft: true}` and `shared_handover_write`.

## Outputs

- Root cause per failing id: `missing`, `untested`, `ambiguous` or `unmeasurable`, each cited to a tool result.
- Draft `requirements.change` with corrected criteria text for approval through change control.

## Failure Handling

- No criterion id in the payload: BLOCKED needs=input; ask A08 or A13 for the failing check id.
- Findings list empty: record that no verdict backed the claim and keep the cause `unconfirmed`.
- Never assign blame to code from this skill; only requirement defects are in scope.
- Tool TIMEOUT: retry once, then E-TIMEOUT.

## Handover

Fill `completed` (causes found), `decisions` (cause class and why), `remaining` (approval and re-plan through
`a02-requirements--change-control`), `artifacts` (kind retro, path, digest, `producer` `A02`) and `verification` (`checks` per tool).
Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).

<!-- generated from prompts/skills/a02-requirements/escape-retro.md; edit the source and run scripts/build_agents.py -->
