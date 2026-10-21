---
name: "a15-docs--coverage-report"
description: "Use when the per-release doc.coverage.report is due: API, ADR, runbook, readability, staleness and package coverage measured against the targets."
disable-model-invocation: true
---

## When to Use
- Once per release, before the docs-release gate (03-agents/A15-docs.md section 4.5: coverage report published per release).
- Not for finding defects: use `a15-docs--bundle-audit`. Not for fixing gaps: run the matching authoring skill, then re-measure here.

## Procedure
1. Call `shared_handover_bundle` with `task_id` for the release ref, `acceptance` and the contract, ADR and runbook artifacts in `artifacts`. `task_known` false or a missing input artifact is BLOCKED `needs=input`.
2. Run `python3 scripts/docs_bundle.py --task-id $TASK --correlation-id $CORR --json`; take `coverage.packages_documented_pct` (target 100) and `docs_with_title_pct`.
3. Call `docs_api_endpoint_coverage` with `total_endpoints` (contract endpoints as `METHOD /path`), `documented_endpoints` (from the reference headings) and `target_pct` 100. Report `covered_pct`, `undocumented` and `unmatched_documented`. For `apis.stale` count the `stale` entries of the latest `a15-docs--staleness-sweep` result whose bound artifact is an api.contract; with no sweep result, run that skill first.
4. Call `docs_adr_render_coverage` with `adrs` (`{id, status}` from adr.set), `rendered` (ids with a rendered doc) and `target_pct` 100.
5. Call `docs_runbook_verified_ratio` with `as_of` (today, `YYYY-MM-DD`), `max_age_days` (the verification window the task or runbook policy states; record it in the report), `runbooks` (`{runbook_id, tier, last_verified}`) and `target_pct` 95. Report `tier1_verified_pct` as `runbooks_with_verified_date_pct`.
6. For each user-facing doc call `docs_flesch_grade` with `text` (prose only, code and front matter removed) and `max_grade` 9; the report value is the median `grade` over docs, written `grade-<median>` as `readability_avg` (metric: median grade at most 9).
7. Call `docs_stale_doc_age` with `as_of`, `max_age_days` 7 and `docs` (`{path, last_verified}`); `stale_pct` must stay under 5 (staleness index metric). Docs it lists in `invalid` have no usable date: count them as unverified, not fresh.
8. Call `shared_dod_check` with `result` (the assembled report, one `checks` entry per metric) and `criteria` (the six targets as sentences: APIs 100, ADR 100, packages 100, tier-1 runbooks 95, grade 9, stale under 5 percent); an `unknown` criterion is listed as remaining, never claimed met.
9. Use `write` to save the report at the path the task `outputs[]` names (with none named, `docs/coverage-report.json`, a convention of this skill), then call `shared_artifact_digest` with that `path`.

## Outputs
- `doc.coverage.report` JSON per 03-agents/A15-docs.md section 3: `apis.covered_pct`, `apis.stale`, `adr_rendered_pct`, `runbooks_with_verified_date_pct`, `readability_avg`, plus `packages_documented_pct` and one `meets_target` flag per metric.

## Failure Handling
- A tool PARTIAL_SUCCESS: do not report that metric as complete; mark it `partial` with the gap.
- INVALID_INPUT: fix the named field once; then BLOCKED `needs=input` for that metric only.
- No tier-1 runbook or no in-scope ADR (the tool returns INVALID_INPUT): report the metric as `n/a`, not 100.

## Handover
Fill `goal` (release gate decision input), `completed`, `remaining` (each unmet target with the owner skill), `artifacts` (report path, digest, producer `A15`), `verification` (`verified` only when every metric has a tool result). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent the assignment routes to (normally `A12`, the release owner), A01 when unknown.

<!-- generated from prompts/skills/a15-docs/coverage-report.md; edit the source and run scripts/build_agents.py -->
