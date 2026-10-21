---
name: a04-ux-designer--palette-contrast
description: Use when a new palette, brand colour change or theme needs every text and UI colour pair measured against WCAG 2.2 AA with severities.
owner: a04-ux-designer
tools: [ux_contrast, ux_scale_monotonic_check, shared_verdict, shared_escalate, shared_scratchpad_put, shared_handover_write]
---

# Palette contrast

## When to Use
- A palette, theme or brand colour is added or changed and you must prove each pair you specify.
- Colour only. Token shape is `skill://a04-ux-designer--token-lint`; focus rings are `skill://a04-ux-designer--focus-visible`.

## Procedure
1. With `bash` run `python3 scripts/ux_tokens.py --file <tokens.json> --background <background token path> --task-id <task_id> --correlation-id <correlation_id> --json` (twin: `bun scripts/ts/ux_tokens.ts`, same flags; ids from the assignment). Its `contrast` pairs below 4.5 are `major` findings; keep them. It only measures each colour token against the one background token.
2. `read` the spec and tokens and list every foreground/background pair you specify or change (text on surface, icon on surface). Resolve aliases to final hex values.
3. Call `ux_contrast` once per pair with `{fg, bg}` (hex `#rgb` or `#rrggbb`). Never reuse a ratio from an earlier run; every ratio you report must come from a call in this run.
4. For each tone ramp (for example 50 to 900) call `ux_scale_monotonic_check` with `{steps: [{step: "50", hex: "#..."}, ...], order: "light_to_dark"|"dark_to_light"}` (2 to 24 steps); state the intended order, do not infer it. Each `violations` entry (`a->b`) is a finding against the ramp.
5. Severity per pair: normal text below 4.5 is `major`; at or above 4.5 is `info`. Large text and UI components use the threshold the spec states (SC 1.4.3 allows 3 for large text, SC 1.4.11 asks 3 for UI components); record that threshold next to the ratio. Call `shared_verdict` with `{severities}`.
6. Brand conflict: if the only compliant fix changes a brand colour, stop. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "<pair, ratio, brand token>", needs: ["human-approval: brand conflict"]}` and return its `result_patch` as BLOCKED (prompt ESCALATION; `decision_logic` 3). A non-brand failure is auto-revised: change the token within the design system and repeat from step 3.
7. Call `shared_scratchpad_put` with `{correlation_id, key: "a04.contrast", value: {verdict, severities, findings: [<=10 strings such as "color.text.muted on color.background: 3.9"]}}`.

## Outputs
- Ratio table (pair, ratio, threshold, severity), ramp results, verdict, the `a04.contrast` key; BLOCKED needs=human-approval on a brand conflict.

## Failure Handling
- INVALID_INPUT from `ux_contrast` always names `fg`, even when `bg` is the bad value; re-read both from the source token, fix once, retry, then BLOCKED needs=input.
- A pair you cannot measure is `skipped:tool-missing` and counts as unchecked, never compliant. If it is required for an a11y target, state FAILED.
- DEPENDENCY_UNAVAILABLE: retry twice. TIMEOUT: retry once, then E-TIMEOUT.

## Handover
Fill `completed` (pairs measured), `decisions` (each recolour with its ratio before and after), `blockers` (the brand conflict), `open_questions` (unmeasured pairs), `verification` (`status`, `confidence`, `checks`). `shared_handover_write` to A01 when blocked, else to the downstream agent the task names (A06).
