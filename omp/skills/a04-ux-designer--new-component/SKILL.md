---
name: "a04-ux-designer--new-component"
description: "Use when no existing design-system component fits a screen need and a new component spec needs states, focus and target checks plus L3 approval."
disable-model-invocation: true
---

# New component

## When to Use
- A screen region has no matching component in the design-system component registry. A new component is L3 (prompt `autonomy`): you may specify and check it, never add it unapproved.
- If an existing component fits (even with a variant), stop and use `skill://a04-ux-designer--screen-spec`; reuse comes first.

## Procedure
1. `grep` the component registry the task inputs name (`platform.constraints` or the design-system path; none named: add that to `open_questions`, invent no path) for the role, its name and two synonyms, and `glob` its library directory for a matching file. Record the search terms and what each returned; a fit means this skill ends here.
2. Use `write` for the component spec inside your `ux.spec` bundle: purpose, props, token references (never raw hex), and states. Call `ux_state_coverage_check` with `{required_states: ["default", "hover", "focus", "error", "loading", "empty"], declared_states: [<spec states>]}`; these are the component-linter families from 03-agents section 2. Drop a family from `required_states` only when the spec marks it not applicable with a one-line reason.
3. Call `ux_focus_contrast_check` with `{focus_hex: <focus token value>, surface_hexes: [every surface the component sits on], required_ratio: 3}` (SC 1.4.11).
4. Call `ux_target_size_check` with `{targets: [{id, width_px, height_px, center_to_nearest_edge_px}]}`, one per interactive part (SC 2.5.8; unique ids). Every `failing` id needs a larger size or more clearance in the spec.
5. Fix the spec until `complete` (step 2), `meets` (step 3) and `compliant` (step 4) are all true, call `shared_artifact_digest` with `{path}` of the spec, then call `shared_status_report` with `{task_id, kind: "blocked", step: "awaiting L3 approval: new component <name>"}`.
6. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "new component <name>: no registry fit (<search terms>)", needs: ["human-approval: new component <name>"]}` and return its `result_patch` as the BLOCKED result. Do not add the component to the registry or reference it from a published screen spec before approval.

## Outputs
- Component request: search evidence, spec path, state table, focus ratios, target-size result, and BLOCKED needs=human-approval.

## Failure Handling
- INVALID_INPUT: the handler names the field (`required_states`, `focus_hex`, `surface_hexes`, `targets`); correct it once from the spec, retry, then BLOCKED needs=input.
- A failing check is not an approval blocker: revise the component first, escalate only a compliant spec.
- DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; a skipped check keeps the spec unapproved. TIMEOUT: retry once, then E-TIMEOUT.

## Handover
Fill `goal`, `completed` (checks run), `blockers` (the L3 approval), `decisions` (why no existing component fit), `artifacts` (spec path, digest, `producer` A04), `verification` (`status` partial until approved, `confidence`, `checks`). Call `shared_handover_write` with `to_agent` A01, who owns the approval request; `shared_escalate`'s `result_patch` is the BLOCKED task result.

<!-- generated from prompts/skills/a04-ux-designer/new-component.md; edit the source and run scripts/build_agents.py -->
