---
name: "a02-requirements--publish-gate"
description: "Use when the spec is about to be marked VALIDATED; run lint, GWT, traceability, NFR coverage and the DoD check, then publish or block with findings."
disable-model-invocation: true
---

# Publish Gate

The last check before VALIDATED. It authors nothing except the traceability matrix: it aggregates the other
skills' results into one verdict and blocks on any failure. Gates downstream (A08, A09, A12) enforce the
criteria this gate lets through. VALIDATED is a spec marker, not a Task Store state: a passing task finishes IN_REVIEW.

## When to Use

- Stories, criteria and NFRs are drafted and any clarification round is finished or the spec is PROVISIONAL.
- Re-run after every edit that follows a BLOCKED verdict.

## Procedure

1. Call `shared_handover_read` with `task_id`; confirm `status` is `ok` and take the artifact paths from `handover.artifacts`.
2. Run `python3 scripts/req_lint.py --root <repo> --file docs/acceptance.md --task-id <task_id> --json`. Copy every finding's severity into a list.
3. Call `req_gwt_check` with `text` set to the content of `docs/acceptance.md`. `missing_gwt` must be 0 and `criteria` above 0.
4. Call `req_criteria_testability` with `criteria`. `manual_ratio` above 0.3 or any `unmeasurable_ids` is a finding.
5. Build the traceability matrix from the files: one link `{requirement_id: US-<n>, artifact_id: AC-<n>}` per criterion, from its `story:` field in `docs/acceptance.md`, and one `{NFR-<n>, <verified_by>}` per NFR. Use `write` to save it as `docs/requirements/traceability.md` (convention path; other skills read it). Call `req_traceability_coverage` with `requirement_ids` (all `US-` and `NFR-` ids), those `links` and `target` 1.0. `meets_target` must be true.
6. Call `req_nfr_coverage` with `nfrs` and `required_kinds` for the categories and regimes in `handover.constraints`. `complete` must be true.
7. Call `shared_verdict` with `severities` set to the lint finding severities plus `major` for each of steps 3 to 6 that failed (an empty list or only `minor` and `info` passes; any `major` fails). Never override the result.
8. If the task has acceptance statements, call `shared_dod_check` with `result` set to a summary object of steps 2 to 7 and `criteria` set to those statements; any `unknown` is reported, not assumed met. With no statements the tool returns INVALID_INPUT field `criteria`: say so and skip it.
9. Call `shared_artifact_digest` with the spec `path` (and the matrix), then `shared_handover_validate` with `{document, draft: true}` and `shared_handover_write`.

## Outputs

- VALIDATED (task result IN_REVIEW) with the evidence list, or BLOCKED with findings by check.
- Spec digest and the `shared_verdict` result.

## Failure Handling

- A required check that cannot run (TIMEOUT or DEPENDENCY_UNAVAILABLE after one retry): BLOCKED with E-DEP; record an optional check as skipped:tool-missing.
- `shared_verdict` fail: BLOCKED; never override the verdict.
- Provisional spec: gate it as PROVISIONAL, never VALIDATED.
- Do not waive a failing check, whoever asks.

## Handover

Fill `completed` (checks passed), `blockers` (each failing check with its tool and finding), `artifacts` (kind
spec, path, digest, `producer` `A02`) and `verification` (`status` verified only when every step passed, `checks` listing each
result). Call `shared_handover_write` with `to_agent` `A03` on VALIDATED (A04 reads the same handover by task id with `shared_handover_read`), otherwise `A01`; both are agent ids, never slugs.

<!-- generated from prompts/skills/a02-requirements/publish-gate.md; edit the source and run scripts/build_agents.py -->
