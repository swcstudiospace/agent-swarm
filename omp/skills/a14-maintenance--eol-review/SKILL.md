---
name: "a14-maintenance--eol-review"
description: "Use when an upstream.eol.notice arrives or the monthly platform review runs; open upgrade epics within 90 days and flag EOL-passed production."
disable-model-invocation: true
---

# Platform end-of-life review

## When to Use

- `upstream.eol.notice` arrives, or the monthly review lists runtimes, frameworks and base images with published EOL dates (A14 spec section 4, rule 5).
- Not for library debt scoring (`a14-maintenance--debt-review`) and not for licence drift (`a14-maintenance--license-recheck`).

## Procedure

1. Collect one row per platform component: name, version in use, published EOL date (`YYYY-MM-DD`), and whether it runs in production. Take the date from the notice or the vendor calendar named in the task; a component without a sourced date goes to `open_questions`, never a guessed one.
2. Get today's date with `bash` (`date -u +%F`) and use that value as `today` in every call so the whole run is consistent.
3. Call `maint_eol_distance` with `{today, eol_date, in_production}` for each row. Keep `days_to_eol`, `status` (`supported`, `within_90_days`, `passed`), `upgrade_epic` and `compliance_finding`.
4. For every row where `maint_eol_distance` returns `upgrade_epic: true`, draft an upgrade epic: component, target version, EOL date, days left and the first milestone before the date.
5. For every row where `maint_eol_distance` returns `compliance_finding: true` (EOL passed and in production) draft a compliance finding for A10 with the component, the days since EOL and the exposure. It is a finding, never silently downgraded.
6. Use `write` to save the `eol.report` at the path the task names, then call `shared_artifact_digest` with that `path` and with the EOL source file when the dates came from a local file, so the report can prove its inputs.
7. Call `shared_handover_validate`, then `shared_handover_write`; when a compliance finding exists write a second handover to A10 (see Handover).

## Outputs

- An `eol.report`: every component with `days_to_eol` and `status`, the upgrade epics, the compliance findings.
- Zero EOL-passed production components is the target (A14 spec section 6); the report states the current count.

## Failure Handling

- `INVALID_INPUT` on `eol_date` or `today`: the format is not YYYY-MM-DD; fix once, else list the component as unknown.
- Conflicting EOL dates from two sources: report both and use the earlier for the status, noting the conflict in `open_questions`.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry once; a component never ends up marked `supported` because a check failed.

## Handover

- Fill `goal` (plan the epics, handle the findings), `completed`, `remaining`, `decisions` (date source per component), `artifacts` (report and EOL source, each with digest), `open_questions`, `verification`.
- `shared_handover_write` to `A01` for epics; a second `shared_handover_write` to `A10` carrying only the compliance findings. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/eol-review.md; edit the source and run scripts/build_agents.py -->
