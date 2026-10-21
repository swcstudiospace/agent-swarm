---
name: a17-platform-engineer--dora-baseline
description: Use when a platform.scorecard task needs DORA four-key metrics banded elite to low over a window, with failure and recovery events cross-checked.
owner: a17-platform-engineer
tools: [plat_dora_bands, plat_golden_path_coverage, plat_catalog_lint, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# DORA baseline

## When to Use

- A `platform.scorecard` task asks for deployment frequency, lead time for changes, change failure rate and failed deployment recovery time of one service group over a stated window (a baseline, or a before and after a golden-path migration).
- Per-service maturity levels are `skill://a17-platform-engineer--scorecard-rollout`. You analyse events you are given from `build.artifact` records (A11); you never run deployments or query production.
- Reading and publishing the baseline is L2.

## Procedure

1. Run `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json` and keep only services with an owner: a metric without an owner has nobody to act on it (`owner-missing` goes to `open_questions`).
2. `read` the event sources: `deployments` `[{id, committed_at, deployed_at}]` from the `build.artifact` records (commit time of the oldest change in the deployment, and the deploy time), and `incidents` `[{started_at, resolved_at, deployment_id}]` from A13 incident records. All times are ISO-8601 with an explicit offset (`2026-10-01T00:00:00+00:00`); an incident that a deployment caused carries its `deployment_id`, one that did not leaves it blank and falls outside the failure rate.
3. Choose the window (`window_start`, `window_end`) from the task, at least 28 days (this skill's convention, so one bad week does not set the band). State it. Call `plat_dora_bands` with it.
4. Read `deployment_count`, `deployments_per_day`, `median_lead_time_hours`, `change_failure_rate`, `median_recovery_hours` and the four bands plus `overall_band`. The tool's bands, its reading of the DORA 2023 cluster profiles (DORA publishes profiles, not cut-offs, and re-clusters every year): frequency elite at 1 per day or more, high at 1 per week or more, medium at 1 per month or more; lead time elite under 24 h, high under 168 h, medium under 720 h; failure rate elite up to 5 percent, high up to 10, medium up to 15; recovery elite under 1 h, high under 24 h, medium under 168 h. `overall_band` is the lowest band that was computed. Recovery time runs from `deployed_at` to the latest `resolved_at` of the incidents of that deployment, not from the incident start.
5. Cross-check the `plat_dora_bands` arithmetic: `failed_deployment_count` is at most `deployment_count`; `change_failure_rate` times `deployment_count` equals `failed_deployment_count` within the 4-place rounding; `deployments_per_day` times the window length in days equals `deployment_count` within the same rounding. `unresolved_failure_count` above 0 makes `recovery_band` `low` and raises `unresolved-failures` (major): never drop an open incident to improve the band.
6. `plat_dora_bands` with `deployment_count` 0 returns `no-deployments` (major) and every band `none`: report "cannot be banded", never a number. A recovery band `none` means no failures, not elite.
7. Call `plat_dora_bands` a second time on each half of the window. Bands that differ between halves are a trend, to be reported with both results, not one blended figure.
8. Optionally split by adoption: call `plat_golden_path_coverage` with the service list and `target_ratio`, then run step 3 separately for on-path and off-path services, so the comparison is on measured bands only. DORA publishes performance profiles, not normative cut-offs; the limits above are the tool's reading and a reviewer may retune them.
9. `write` the baseline and call `shared_artifact_digest` with `{path}`.

## Outputs

- `platform.scorecard` (delivery section): the four values, four bands, `overall_band`, window, event counts, halves comparison.

## Failure Handling

- INVALID_INPUT (`committed_at` after `deployed_at`, incident before its deployment, `deployment_id` not in `deployments`, `window_end` not after `window_start`, offset missing): fix the named field once, else BLOCKED `needs=input` for the event source.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the missing services.

## Handover

`to_agent`: `A13` (SLO and incident follow-up) or `A14` when remediation is debt. Fill `goal`, `completed`, `decisions` (window, exclusions), `artifacts` (digest, producer `A17`), `open_questions` (unowned services, open incidents), `verification`. End with `shared_handover_validate`, then `shared_handover_write`.
