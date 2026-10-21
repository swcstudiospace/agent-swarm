---
name: a04-ux-designer--lofi-fallback
description: Use when Figma is unavailable or the brand guide is missing and the spec must be produced as lofi or PROVISIONAL instead of stalling or guessing.
owner: a04-ux-designer
tools: [shared_log_emit, ux_spec_completeness_check, ux_token_name_check, shared_status_report, shared_handover_write]
---

# Lofi fallback

## When to Use
- Figma cannot be read or written (A04 has no Figma tool of its own: the trigger is a failed Figma call you made through `bash`, or an input that says Figma is down) after its retries (E-DEP), or no `brand.guide` was supplied. Both fallbacks come from the prompt `error_handling` and agents section 5.
- Normal-fidelity specs are `skill://a04-ux-designer--screen-spec`. Use this skill only when a trigger above is true; otherwise degraded output hides a fixable problem.

## Procedure
1. Confirm the trigger: the failed call and its error, or the absent `brand.guide` in the task inputs (`read` the assignment). Call `shared_log_emit` with `{level: "warn", message: "ux fallback: figma-unavailable|brand-guide-missing", fields: {mode: "lofi"}, task_id}`.
2. Use `edit` to set the flags in the spec draft: `"fidelity": "lofi"` when Figma is down, `"provisional": true` when the brand guide is missing (both when both apply). The design system's default theme supplies colours and type; invent no brand values.
3. Use `write` to draw the layout as an ASCII wireframe, one block per state in the spec, inside the screen's spec bundle (it replaces the Figma frames; no file outside `ux.spec`, `design.system.tokens`, `a11y.requirements`). Label regions with the component `ref` they use.
4. Call `ux_spec_completeness_check` with `{spec, required_states: ["default", "loading", "error", "empty", "success"], interactive_ids, min_contrast_floor: 4.5}`. Degraded fidelity relaxes no rule: `complete` must still be true, accessibility floor included.
5. Call `ux_token_name_check` over the default theme's token paths (`{names, max_depth}` with the depth the token repo documents; segments are kebab-case and start with a letter) so a provisional spec cites only tokens that exist and follow the convention.
6. Call `shared_status_report` with `{task_id, kind: "progress", step: "lofi/PROVISIONAL spec ready", note: "<missing: figma frames, brand guide>"}`.

## Outputs
- A spec marked `fidelity: lofi` and/or `provisional: true`, the ASCII wireframe, a list of what must be redone once Figma or the brand guide is back.

## Failure Handling
- A spec that cannot reach `complete` in lofi stays unpublished: BLOCKED needs=input with the missing fields.
- A brand colour the default theme lacks: BLOCKED needs=input; do not invent it.
- INVALID_INPUT: fix the named field once, retry, then BLOCKED. DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`. TIMEOUT: retry once.
- `shared_log_emit` failure does not stop the work; repeat the notice in `open_questions`.

## Handover
Fill `constraints` (spec is lofi or PROVISIONAL and must be re-reviewed), `remaining` (high-fidelity frames, brand review), `decisions` (default theme used), `verification` (`status` partial, `confidence`, `checks`). `shared_handover_write` to A06 or A08 as the task names; they must read `constraints` before relying on the spec.
