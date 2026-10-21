---
name: a04-ux-designer--a11y-triage
description: Use when a11y.violations arrive from A06 or QA and each must be classified as a spec defect or a code defect, re-measured, and routed.
owner: a04-ux-designer
tools: [shared_open_findings, shared_handover_read, ux_contrast, ux_target_size_check, ux_focus_contrast_check, shared_route_check, shared_verdict, shared_handover_write]
---

# A11y triage

## When to Use
- `a11y.violations` reach A04 from A06 or a QA scan. The question is accessibility conformance of the spec, not task success; usability evidence is `skill://a04-ux-designer--usability-redesign`.

## Procedure
1. Collect the violations from two places. Call `shared_handover_read` with `{task_id}` and take them from `handover` (`remaining`, `blockers`, `open_questions`). Call `shared_open_findings` with `{task_id}` for failing gate findings (`gate`, `agent`, `id`, `severity`, `kind`, `summary`, `location`, `ac_ref`; only unexpired failing verdicts are listed). Keep accessibility ones. `count` 0 and nothing in the handover: BLOCKED needs=input (no violation data), do not triage from memory.
2. For each violation, `read` the value the spec specifies for that element (colour tokens, size, focus ring). Re-measure from the spec, never from the report: `ux_contrast` with `{fg, bg}` for a contrast finding, `ux_target_size_check` with `{targets: [{id, width_px, height_px, center_to_nearest_edge_px}]}` for a size finding, `ux_focus_contrast_check` with `{focus_hex, surface_hexes, required_ratio: 3}` for a focus finding.
3. Classify from the step 2 results of `ux_contrast`, `ux_target_size_check` or `ux_focus_contrast_check`: the spec's own value fails the measurement, so it is a spec defect (yours); the spec value passes while the build reports a failure, so the build deviates and it is a code defect (A06's).
4. Spec defect: revise the spec with `edit` through `skill://a04-ux-designer--screen-spec` (a token change also through `skill://a04-ux-designer--token-release`), then repeat the same measuring tool from step 2 until the measure passes. A fix that needs a brand change is escalated (`skill://a04-ux-designer--palette-contrast` step 6), never shipped (`decision_logic` 3).
5. Code defect: you cannot fix or route frontend code (`decision_logic` 5). Call `shared_route_check` with `{sender: "A04", recipient: "A06", message_type: "ux.spec"}` to confirm the revised spec can go to A06. Put the needed frontend change, with the measured spec value against the reported value, into `remaining` of a `shared_handover_write` to A01 so it creates the task.
6. Call `shared_verdict` with the severities of the confirmed violations. Any `major` or above is state FAILED until the fix lands.

## Outputs
- Per violation: id, element, measured spec value, class (`spec` or `code`), owner, fix. The verdict. The code-defect requests handed to A01.

## Failure Handling
- `shared_open_findings` reports `task_known` false: BLOCKED needs=input with the task id you need.
- A violation without enough data to re-measure (no element id, no colours) goes to `open_questions`, not into a class.
- INVALID_INPUT from a measuring tool: re-read the named field from the spec, fix once, retry, then BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`. TIMEOUT: retry once.

## Handover
Fill `completed` (violations classified), `remaining` (code-defect requests), `decisions` (class and evidence per violation), `open_questions`, `verification` (`status`, `confidence`, `checks`). `shared_handover_write` to A01 for code defects; to A06 or A08 for the revised spec.
