---
name: a14-maintenance--debt-review
description: Use when the monthly tech-debt scoring is due; score every register item by impact, recurrence and effort and propose iterations that compete through A01.
owner: a14-maintenance
tools: [maint_debt_priority, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Monthly debt review

## When to Use

- The monthly scoring pass of A14 spec section 4, rule 4 (impact x recurrence x effort), or `tech.debt.signals` from A09 or A03 add new items.
- Not for platform end-of-life dates (`a14-maintenance--eol-review`) and not for a single pinning problem found in a sweep.

## Procedure

1. Run `python3 scripts/maint_deps.py --task-id <task> --correlation-id <corr> --root . --max-age-days 30 --json` and read `debt.items` (`item_id`, `kind` always `code`, `source` `marker-scan`, `location`, `impact` text, `est_effort_h`, `priority_score`), `debt.git` and `unpinned`. The script rewrites `.swarm/debt_register.json` (the raw marker scan) on every run; never hand-edit it. Its `est_effort_h` is the marker count, not hours, and its `priority_score` is marker-based. Add each `unpinned` entry as a `kind: deps` item with `source: maint_deps unpinned` (convention: the script does not put them in the register).
2. Use `read` on the A09 or A03 signal reports in the task bundle and add each as an item with a `source` naming the report and a `kind` of architecture, code, deps or docs.
3. For each item call `maint_debt_priority` with `{impact, recurrence, effort_h}`: `impact` and `recurrence` are integers 1 to 5, `effort_h` is a positive number of hours up to 400. Take them from evidence (for example `bash` `git log --oneline -- <location>` for how often the location changed) and state the rule you used; take the hours from the task or the owner's estimate, never from the script marker count. When evidence is absent record an open question and leave the item unscored. Keep `score` and `proposed_iteration` (the tool gives I+1 at 70 or more, I+2 at 40 or more, else I+3).
4. Replace the script's `priority_score` with the `maint_debt_priority` `score` in your register fragment and keep the script value as `marker_score` for traceability (convention).
5. Check the fragment yourself with `read`: every item has `kind`, `source`, `est_effort_h` (hours), `priority_score` and `proposed_iteration`. No registry tool checks field presence.
6. Use `write` to save the scored fragment at the path the task names, then call `shared_artifact_digest` with that `path` and list it as an artifact.
7. Keep debt out of feature PRs beyond lint-level cleanups: debt items go to A01 as backlog candidates, never as bundled changes. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- A `tech.debt.register` fragment: items with `item_id`, `kind` (architecture, code, deps, docs), `source`, `impact`, `est_effort_h`, `priority_score`, `proposed_iteration`.
- A burn-down line: items closed against items added since the previous review's register in the task bundle (target at least 1.2x accrual, A14 spec section 6); `no baseline` when the bundle has none.

## Failure Handling

- `maint_deps.py` finds no markers: report an empty register with the scanned root, not a healthy codebase.
- `INVALID_INPUT` on `impact`, `recurrence` or `effort_h`: correct the value from evidence once; otherwise leave the item unscored in `open_questions`.
- `git` missing makes `age_days` null (`debt.git: skipped:tool-missing`); say so, do not invent ages.

## Handover

- Fill `goal` (plan these debt items into iterations), `completed` (items scored), `remaining` (unscored items), `decisions` (scoring inputs and sources), `artifacts` (fragment with digest), `open_questions`, `verification`.
- `shared_handover_write` to `A01`. See `skill://swarm-handover-emit`.
