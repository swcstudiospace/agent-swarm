---
name: a04-ux-designer--usability-redesign
description: Use when usability.findings of severity major or higher from QA or observability force a redesign task: compare options, resolve conflict, revise the spec.
owner: a04-ux-designer
tools: [shared_open_findings, shared_handover_read, shared_scratchpad_put, shared_escalate, shared_verdict, shared_handover_write]
---

# Usability redesign

## When to Use
- `usability.findings` (task success rate, drop-off, moderated-test failures) with severity `major` or higher arrive from A08 or A13; they force a redesign task in the next iteration (`decision_logic` 4).
- Task success, not conformance. WCAG violations are `skill://a04-ux-designer--a11y-triage`.

## Procedure
1. Call `shared_handover_read` with `{task_id}` and take the findings and their evidence (metric, test notes) from `handover`; call `shared_open_findings` with `{task_id}` for failing gate findings (`gate`, `agent`, `id`, `severity`, `summary`, `location`, `ac_ref`; observability metrics never appear there). Keep findings of severity `major` or above that name a screen or flow: each needs a redesign decision. Below `major`, record it and move on.
2. For each finding `read` the current spec of the screen and the evidence. State the failing task in one sentence and the target from the metrics (task success at least 90 percent on moderated tests, agents section 6).
3. Build an option matrix: two or three options (including "no change") scored 1 to 5 against the usability heuristics the finding implicates (visibility of status, error prevention, recognition over recall, consistency), each score with one line of rationale and its cost in components touched. Store it with `shared_scratchpad_put` as `{correlation_id, key: "a04.options.<screen-id lowercased>", value: {finding, options, recommended}}`; keys allow only `[a-z0-9._-]`, at most 64 characters.
4. One round only. First run: write the recommendation with `shared_handover_write` to A01 and put "object to option <X> by <condition>" in `open_questions`; stakeholders reach you only when A01 re-assigns the task. On that re-run, if the objections conflict and the matrix does not settle them, call `shared_escalate` with `{task_id, reason_code: "conflict", summary: "<option disagreement>", needs: ["decision between option A and B"]}` and return its `result_patch` as BLOCKED. Do not pick unilaterally.
5. A settled option becomes a spec revision: `edit` the spec following `skill://a04-ux-designer--screen-spec` (a new component adds `skill://a04-ux-designer--new-component`). Keep the original `acceptance_refs`; a changed requirement is reported for A01 to route to A02, never edited.
6. Call `shared_verdict` with the severities of the findings you addressed (`info|minor|major|critical|blocker`); a finding still unaddressed at `major` or above keeps the redesign task open.

## Outputs
- Option matrix per finding, the decision and its reason, the revised spec reference, and the findings left open.

## Failure Handling
- No findings in the handover, `task_known` false, or no evidence for a finding: BLOCKED needs=input; never invent metrics.
- `shared_scratchpad_put` over 8 KiB or at the 64-key limit: shorten rationale lines, keep only the recommended option in full.
- Unresolved conflict after one round: escalate, do not pick unilaterally.
- DEPENDENCY_UNAVAILABLE: retry twice, then BLOCKED needs=tool. TIMEOUT: retry once.

## Handover
Fill `decisions` (chosen option with rationale), `open_questions` (stakeholder objections requested), `blockers` (unresolved conflict), `remaining` (findings still open), `verification` (`status`, `confidence`, `checks`). `shared_handover_write` to A06 once the revised spec is ready, to A01 when escalated or awaiting objections.
