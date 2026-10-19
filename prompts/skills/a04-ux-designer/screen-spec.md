---
name: a04-ux-designer--screen-spec
description: Use when authoring or revising a per-screen ux.spec that must cite acceptance criteria and pass the shape, state and AC-trace checks before handoff.
owner: a04-ux-designer
tools: [shared_handover_bundle, ux_spec_completeness_check, ux_state_coverage_check, ux_ac_trace_check, shared_dod_check, shared_artifact_digest, shared_handover_write]
---

# Screen spec

## When to Use
- A screen needs a new `ux.spec`, or an existing one changes (a redesign task, a requirement change, a triaged a11y fix).
- Full-fidelity specs only. Degraded mode is `skill://a04-ux-designer--lofi-fallback`; a new design-system component is `skill://a04-ux-designer--new-component`.

## Procedure
1. Call `shared_handover_bundle` with `{task_id}` and read `acceptance` (AC ids and text), `handover` and `upstream`. List the AC ids this screen set must satisfy.
2. `grep` or `glob` the design-system component registry the inputs name for existing components that fit each region; reuse first (`decision_logic` 1). Draft the spec in the output_format shape (`screen_id`, `flows`, `states`, `components` with `ref`, `a11y` with `target` `WCAG-2.2-AA`, `focus_order` and `min_contrast`, `tokens_version` MAJOR.MINOR.PATCH, `fidelity` `hifi`, `provisional`, `acceptance_refs`) and save it with `write`. `a11y.min_contrast` is the lowest ratio measured in this run by `skill://a04-ux-designer--palette-contrast`, never a guess.
3. Call `ux_spec_completeness_check` with `{spec, required_states: ["default", "loading", "error", "empty", "success"], interactive_ids: [ids in the wireframe], min_contrast_floor: 4.5}`; add any state family an AC demands. Repair the spec until `complete` is true; a spec that is not complete cannot be published (`decision_logic` 2).
4. Call `ux_state_coverage_check` with `{required_states: <spec.states>, declared_states: <states with a drawn frame in ui.wireframes>}`. Every `missing` entry is a state you specified but never drew: draw it or remove it with a reason.
5. Call `ux_ac_trace_check` with `{acceptance_ids: [all AC ids of the task], screens: [{screen_id, acceptance_refs}, ...]}` over every screen spec of the task. `uncovered_acs` need a screen or an explicit note; `unknown_refs` are typos or missing requirements; `screens_without_refs` are orphans and not publishable. You cannot edit requirements: report a needed change in the result so A01 routes it to A02.
6. Call `shared_dod_check` with `{result: <the spec JSON>, criteria: [AC text], task_id}`. A criterion that stays `unknown` has no evidence in the spec: add the evidence or list it in `open_questions`.
7. Call `shared_artifact_digest` with `{path}` of the saved spec, then `shared_handover_write` (see Handover).

## Outputs
- The spec file, the markdown summary (screens specified, token changes, a11y findings) and exactly one fenced `json` block in the final message (the output_format shape).
- Numbers to report: `complete`, `coverage_pct` from the trace, `missing` from step 4.

## Failure Handling
- Missing task_id, capability or inputs: BLOCKED needs=input.
- INVALID_INPUT naming `interactive_ids` or `screens`: remove the duplicate or blank id once, retry, then BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry twice, then report the check as `skipped:tool-missing`; never mark the spec complete on a skipped check.
- TIMEOUT: retry once, then E-TIMEOUT.

## Handover
Fill `goal`, `completed` (screens specified), `artifacts` (kind `ux.spec`, spec path, digest, `producer` A04), `decisions` (components reused and why), `open_questions` (unknown criteria), `verification` (`status`, `confidence`, `checks` = the three check results). `shared_handover_write` to A06 (implementation) or A08 (UI test baseline) as the task names.
