---
name: "a04-ux-designer--token-lint"
description: "Use when a design-tokens file is about to be published or changed and needs a structural lint: token shape, names, spacing grid, breakpoint order."
disable-model-invocation: true
---

# Token lint

## When to Use
- Before any `design.system.tokens` file is published, and again after any edit to it.
- You need structure only (shape, names, grid, breakpoints). Colour pairs belong to `skill://a04-ux-designer--palette-contrast`; version impact belongs to `skill://a04-ux-designer--token-release`.

## Procedure
1. With `bash` run `python3 scripts/ux_tokens.py --file <tokens.json> --task-id <task_id> --correlation-id <correlation_id> --json` (twin: `bun scripts/ts/ux_tokens.ts`, same flags; ids from the assignment). Keep its `findings` and `verdict`; it flags a missing `$type` (minor), an invalid hex value and an alias that does not resolve (major).
2. `read` the token file and flatten every leaf (a node with `$value`) to a dotted path. Call `ux_token_name_check` with `{names: [paths], max_depth: <depth the token repo documents>, roots: [first segments the repo declares]}`. Each segment must be lowercase kebab-case starting with a letter (`scale-4` passes, `4` fails). The prompt's worked example uses depth 4 with roots `color` and `space`; use it only when the repo documents nothing else and record that in `decisions`.
3. Collect the dimension tokens under the spacing root as plain numbers in px (convert rem with the documented root size) and call `ux_spacing_scale_check` with `{base_px: <repo base unit>, values_px: [...]}` (the prompt's worked example uses 4).
4. When the file defines breakpoints, call `ux_breakpoint_order_check` with `{breakpoints: [{name, min_width_px}]}` in declared order. No breakpoints in the file: skip and record `no breakpoints`.
5. Assign a severity to every finding from evidence: script findings keep the severity the script gave; each `invalid` or `duplicates` entry is `major` (consumers resolve tokens by name); each `off_scale` value is `minor`; `valid: false` breakpoints are `major`. Call `shared_verdict` with `{severities}`.
6. Call `shared_scratchpad_put` with `{correlation_id, key: "a04.lint", value: {verdict, severities, findings: [<=10 short strings]}}` so `skill://a04-ux-designer--a11y-sheet` can read it.

## Outputs
- Lint verdict (`pass` or `fail` from `shared_verdict`), the finding list with severities, and the `a04.lint` scratchpad key.
- A `fail` is state FAILED: the token set cannot ship (prompt `OPERATING_PROCEDURE` step 5).

## Failure Handling
- Script and twin both missing: BLOCKED needs=tool. Never invent script output.
- INVALID_INPUT from a tool: correct the named field once from the source token, retry, then BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry twice, then record the check as `skipped:tool-missing`; a skipped check is never reported as passed.
- TIMEOUT: retry once, then E-TIMEOUT. `shared_scratchpad_put` failure does not change the verdict; note it in `open_questions`.

## Handover
Fill `goal` (lint of the named token file), `completed` (checks run), `remaining` (fixes owed), `decisions` (depth, roots, base unit chosen and why), `verification` (`verified` only if every check ran; confidence from the share of checks that ran). Call `shared_handover_write` with `to_agent` set to the downstream agent the task names (A06 when the tokens are next consumed), else A01.

<!-- generated from prompts/skills/a04-ux-designer/token-lint.md; edit the source and run scripts/build_agents.py -->
