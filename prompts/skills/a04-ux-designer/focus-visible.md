---
name: a04-ux-designer--focus-visible
description: Use when a flow, form or modal has interactive elements and its keyboard focus order and focus-indicator contrast must be specified and verified.
owner: a04-ux-designer
tools: [ux_spec_completeness_check, ux_focus_contrast_check, shared_verdict, shared_scratchpad_put, shared_handover_write]
---

# Focus visible

## When to Use
- A spec contains forms, dialogs, menus or any interactive flow, and `a11y.focus_order` must be correct and the focus ring visible on every surface it appears on.
- Focus only. Text pairs are `skill://a04-ux-designer--palette-contrast`; target sizes are `skill://a04-ux-designer--responsive-targets`.

## Procedure
1. `read` the spec and wireframe and list the interactive element ids in reading order (links, buttons, inputs, dialog controls). For a dialog also note where focus lands on open and where it returns on close.
2. Call `ux_spec_completeness_check` with `{spec: <the ux.spec object>, required_states: ["default"], interactive_ids: [ids from step 1]}` and use only `focus_order_unknown` (ids in `a11y.focus_order` that are not interactive ids) and `focus_order_missing` (interactive ids never reached). The other fields belong to `skill://a04-ux-designer--screen-spec`.
3. For each focus-indicator token, gather every surface it can sit on (page, card, input, selected row) with `grep` over the tokens file and resolve aliases to hex. Call `ux_focus_contrast_check` with `{focus_hex, surface_hexes (1 to 8), required_ratio: 3}`. 3 is the non-text contrast of SC 1.4.11; use a stricter value only when the spec sets one.
4. Assign severities from the `ux_spec_completeness_check` and `ux_focus_contrast_check` results: each `failing_surfaces` entry is `major` (a focus ring that disappears on a surface cannot ship), each unknown or missing focus id is `major`, a passing check is `info`. Note each failing surface with its ratio from `ratios`.
5. Revise the focus token or the focus order in the spec with `edit` and rerun steps 2 and 3 until clean. A design that violates WCAG 2.2 AA is auto-revised; a brand conflict is escalated through `skill://a04-ux-designer--palette-contrast` step 6 (`decision_logic` 3).
6. Call `shared_verdict` with `{severities}`, then `shared_scratchpad_put` with `{correlation_id, key: "a04.focus", value: {verdict, severities, focus_order, lowest_ratio}}` (object, at most 8 KiB).

## Outputs
- Final `focus_order`, ratio per surface, `lowest_ratio`, failing surfaces, verdict, the `a04.focus` key.

## Failure Handling
- INVALID_INPUT naming `interactive_ids`: remove the duplicate or blank id once and retry; naming `focus_hex` or `surface_hexes`: re-read the token (hex `#rgb` or `#rrggbb`) and fix once; then BLOCKED needs=input.
- A surface you cannot identify from the spec is an `open_questions` entry; do not guess it.
- DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; a required target then counts as FAILED. TIMEOUT: retry once.

## Handover
Fill `completed` (flows covered), `decisions` (focus order rationale, ring token chosen), `open_questions` (unidentified surfaces), `verification` (ratios as `checks`). `shared_handover_write` to A06 (it implements the order) or A08 (keyboard tests) as the task names.
