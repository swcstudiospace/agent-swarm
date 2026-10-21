---
name: a02-requirements--acceptance-criteria
description: Use when a story has no acceptance criteria or its criteria are weak, unstructured or manual-only; write Given/When/Then and prove them machine-checkable.
owner: a02-requirements
tools: [shared_handover_read, req_gwt_check, req_criteria_testability, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Acceptance Criteria

The criterion-level gate. Every story needs at least one automated-checkable criterion (spec section 4, rule 1);
manual-only criteria above 30 % trigger a clarification round. Story quality is a different skill.

## When to Use

- A `US-` story lacks `AC-` criteria, or A08 or the lint reported structure, vague wording or manual-only criteria.
- Not for non-functional targets (`a02-requirements--nfr-elicitation`).

## Procedure

1. Call `shared_handover_read` with `task_id` to get the story ids and any criteria feedback in `handover.open_questions`.
2. Use `read` on `docs/requirements/stories.md` (convention path for A02's story file) and `grep` for existing `AC-` ids in `docs/acceptance.md` so new ids stay unique and stable.
3. Write each criterion as `AC-<n> story: US-<n> Given <state> When <action> Then <observable result> automated: true|false`, one behaviour each. `req_lint.py` splits the file at every `AC-` id occurrence, so each id appears once, at its own criterion, and never in prose or cross-references. The Then clause carries a number, status code or error code (house style). Add `check` (for example `api-contract`) as text in the same block; the `story:` field is what the traceability matrix links on.
4. Call `req_gwt_check` with `text` set to the full criteria block. `missing_gwt` must be 0 and `criteria` must equal the number you wrote (0 criteria is not a pass); fix every criterion it cannot see.
5. Use `write` to store the criteria in `docs/acceptance.md`, the file A08 and `req_lint.py` read (or a JSON file `{criteria: [{id, given, when, then, automated}]}`).
6. Run `python3 scripts/req_lint.py --root <repo> --file docs/acceptance.md --task-id <task_id> --json`. Fix findings of kind `structure`, `duplicate-id`, `measurable` and `manual-ratio` (major) in the file; `vague` findings are minor and still fixed. Re-run until `verdict` is `pass`.
7. Call `req_criteria_testability` with `criteria` as `{id, text, automated}`. `manual_ratio` at or below 0.3 and an empty `unmeasurable_ids` list are required; convert or rewrite the offenders, otherwise record them as questions.
8. Call `shared_artifact_digest` with `{path: "docs/acceptance.md"}`, then `shared_handover_validate` with `{document, draft: true}` and `shared_handover_write`.

## Outputs

- `acceptance.criteria` per story with `automated` flags, saved at `docs/acceptance.md`.
- `req_gwt_check`, lint verdict and testability ratios cited as evidence.

## Failure Handling

- Lint verdict fail after two edit rounds: do not publish; hand the findings back as `blockers`.
- `req_gwt_check` PARTIAL_SUCCESS: record `completed_fraction`, treat the count as unverified.
- Lint reports `file not found`: re-create the file, never fabricate lint output.
- Criterion that can only be judged by a person: keep it, mark `automated: false` (the lint counts that text as manual-only) and state the measurable outcome.

## Handover

Fill `completed` (criteria per story), `remaining` (NFRs, publish gate), `open_questions` (unmeasurable
criteria), `artifacts` (kind criteria, the saved path, the digest from step 8, `producer` A02) and `verification` with `checks` holding the three
results from steps 4 to 7. Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).
