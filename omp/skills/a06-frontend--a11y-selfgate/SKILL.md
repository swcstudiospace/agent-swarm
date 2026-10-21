---
name: "a06-frontend--a11y-selfgate"
description: "Use when UI code is ready for IN_REVIEW; run the per-rule a11y tools and fe_a11y_check so serious and critical findings are zero."
disable-model-invocation: true
---

## When to Use
- Before the state goes to IN_REVIEW (A06 spec section 4 rule 3: "axe clean (serious/critical = 0) before IN_REVIEW; keyboard paths tested per spec focus order").
- Accessibility only. The pre-review check bundle (lint, typecheck, tests) is run from the prompt procedure, not here. axe-core in component tests stays the authority; the checks below are the early warning.

## Procedure
1. Take the focus order and breakpoints of each screen from the bound-inputs note of `a06-frontend--bind-inputs`. After a resume or rework call `shared_handover_read` with `{task_id}` and read them from `handover.constraints`; when `status` is not `ok`, `read` the `ux.spec` input again.
2. For each component's rendered markup, call `fe_a11y_scan` with `{html}`. A count above 0 is a lead only: locate it with step 6.
3. Call `fe_heading_sequence_check` with `{levels: [<h-level 1 to 6 of every heading in document order>]}` and require `sequence_ok`. Call `fe_alt_text_check` with `{images: [{id, has_alt, alt, decorative}]}` (`alt` is `""` when absent; ids unique) and require `alt_ok`.
4. Call `fe_form_label_check` with `{controls: [{id, type, has_label, aria_label, aria_labelledby}]}` (`type` is from the schema enum: text, email, password, number, tel, url, search, date, checkbox, radio, select, textarea, file, hidden, submit, button, reset; `aria_label` is `""` when absent; `aria_labelledby` is a boolean; at most 300 controls) and require `labels_ok`. Call `fe_tab_order_check` with `{elements: [{id, tabindex}]}` in DOM order and require `order_ok` (a positive tabindex is a defect), then compare the order with the spec focus order by hand.
5. Call `fe_target_size_check` with `{targets: [{id, width_px, height_px}]}` (default `min_px` 24) and require `size_ok`.
6. Run `python3 scripts/fe_a11y_check.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json`; add `--file <path>` (repeatable) to narrow a rerun. Read `serious`, `moderate` and each finding's `location` (`file:line`). This Python script is the authority: the `bun scripts/ts/fe_a11y_check.ts` twin only checks `<html lang>` and `<img alt>`, so a clean twin proves nothing. Fix every serious finding with `edit`, then rerun steps 2 to 6 on the changed parts.
7. Build `a11y_selfcheck` for the result: `serious` and `moderate` from the `scripts/fe_a11y_check.py` JSON (the `fe_a11y_scan` counts are only leads); `critical` from the axe-core component-test run (the static script never emits it). With no axe run in the repo, list `critical` as UNVERIFIED, never 0. Any serious or critical above 0 means the state is not IN_REVIEW.

## Outputs
- `a11y_selfcheck: {serious, critical, moderate}` with the tool and script runs it came from, plus the fixed and open findings (`a11y.selfcheck.report`).

## Failure Handling
- A tool returns `DEPENDENCY_UNAVAILABLE`: retry the same input at most twice, then record `skipped:tool-missing` for it and rely on the script. If the script cannot run either, FAILED `E-DEP`; never claim the gate passed.
- `INVALID_INPUT`: the named field has a duplicate id, a bad enum or a bad type; fix the list and rerun once.

## Handover
Fields filled: `verification` (status `verified` only when serious and critical are 0 and measured, otherwise `partial`, with the counts in `checks`), `blockers` (open serious findings). Written with `shared_handover_write` to A09 (`skill://swarm-handover-emit`); A08 gets the same counts through `a06-frontend--component-catalog`.

<!-- generated from prompts/skills/a06-frontend/a11y-selfgate.md; edit the source and run scripts/build_agents.py -->
