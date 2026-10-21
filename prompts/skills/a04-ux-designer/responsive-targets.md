---
name: a04-ux-designer--responsive-targets
description: Use when a layout changes across breakpoints or serves touch input and every interactive target must meet the WCAG 2.2 minimum size at each breakpoint.
owner: a04-ux-designer
tools: [ux_breakpoint_order_check, ux_target_size_check, shared_verdict, shared_scratchpad_put, shared_handover_write]
---

# Responsive targets

## When to Use
- A spec adds or changes breakpoints, reflows controls, or targets touch devices, so sizes and spacing differ per breakpoint.
- Size and spacing only (SC 2.5.8). Focus is `skill://a04-ux-designer--focus-visible`; animation is `skill://a04-ux-designer--motion-review`.

## Procedure
1. `read` the responsive section of the spec and list the breakpoints in declared order. Call `ux_breakpoint_order_check` with `{breakpoints: [{name, min_width_px}, ...]}` (1 to 12 entries). Not `valid` (`out_of_order`, `duplicate_names`), or `starts_at_zero` false, is a `major` finding: fix names or widths before measuring anything.
2. Use `read` and `grep` to collect, for each breakpoint, every interactive target the layout shows (buttons, icon buttons, links, form controls) with its width, height and the distance from its centre to the nearest edge of the closest neighbouring target, in CSS px. Take sizes from the spec or tokens; measure from the wireframe grid, never from visual impression.
3. Call `ux_target_size_check` once per breakpoint with `{targets: [{id: "<bp>:<element>", width_px, height_px, center_to_nearest_edge_px}]}`. Prefix each id with the breakpoint so ids stay unique (the tool rejects duplicates).
4. Read the result per breakpoint: `failing` ids need a larger size or more clearance; `spacing_exception` ids pass only because the centre-to-edge clearance is at least 12 px, so record that dependency in the spec (changing the layout can break it). The tool does not compute whether the 24 px circles of two undersized targets overlap; check that from the grid yourself.
5. Revise the spec with `edit` (size tokens first, then spacing) and repeat step 3 for the affected breakpoints only.
6. Assign severity: each `failing` id is `major`, each exception-only id `info`, an invalid breakpoint list `major`. Call `shared_verdict` with `{severities}`, then `shared_scratchpad_put` with `{correlation_id, key: "a04.targets", value: {verdict, severities, failing: {"<bp>": [ids]}}}`.

## Outputs
- Breakpoint list status, per-breakpoint table (total, meets, exception, failing), verdict, the `a04.targets` key.

## Failure Handling
- INVALID_INPUT from `ux_target_size_check` naming `targets`: a duplicate id; rename it with its breakpoint prefix once, retry, then BLOCKED needs=input. From `ux_breakpoint_order_check`: more than 12 entries or a bad width.
- Native platform touch guidelines are out of scope for these tools; do not claim them as verified.
- DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; targets stay unchecked. TIMEOUT: retry once.

## Handover
Fill `completed` (breakpoints checked), `decisions` (size and spacing changes), `remaining` (breakpoints skipped), `verification` (`status`, `confidence`, `checks` = counts per breakpoint). `shared_handover_write` to A06 when sizes change its layout, to A08 for responsive test baselines.
