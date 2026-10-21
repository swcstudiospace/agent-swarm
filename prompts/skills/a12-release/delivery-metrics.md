---
name: a12-release--delivery-metrics
description: Use when a weekly report or metrics request needs change failure rate, deploy frequency and lead time P95 over a window, compared with the spec targets.
owner: a12-release
tools: [rel_change_failure_rate, rel_deploy_frequency, rel_lead_time_p95, shared_handover_validate, shared_handover_write]
---

# Delivery metrics

## When to Use

- A request for the weekly delivery report, or A13/A14 asks for release health over a window (spec section 6: change failure rate under 10 percent, deployment frequency at least daily capability, lead time P95 under 2 h, rollback MTTR under 10 min).
- Windowed over many releases. One release's evidence is `skill://a12-release--release-record-seal`.

## Procedure

1. Use `glob` on `release-records/*.record*.json` (where `skill://a12-release--release-record-seal` writes; a convention, not a runtime path) and `read` each record in the window, keeping only the highest-numbered record of a `supersedes` chain. Take `promoted_at`, `rolled_back_at`, `breach_at` and the issue times in `verdicts` (`<verdict>@<time>`). A hot-fix or incident link counts as a failure only when the task inputs name it.
2. Build `outcomes` (one boolean per change, true = failed: the record has `rolled_back_at`) and call `rel_change_failure_rate` with `{outcomes}`. The target is `rate` below 0.10.
3. Build `deploy_days` (the ISO date of each `promoted_at`; repeated days count) and call `rel_deploy_frequency`. A `per_week` of 7 or more reads as daily capability. This reading of "at least daily" is this skill's convention; say so in the report.
4. Build `durations_hours` (latest gate verdict time to `promoted_at`, per release, computed with `bash` `date -d`) and call `rel_lead_time_p95`. The target is `p95_hours` below 2. A record whose `verdicts` carry no times is excluded and counted.
5. For rollback MTTR, collect `rolled_back_at` minus `breach_at` per rolled-back record in hours and call `rel_lead_time_p95` a second time. Report `median_hours` and `p95_hours` times 60 as minutes and mark the 10 minute target met only when both are under it.
6. Compare each value returned by `rel_change_failure_rate`, `rel_deploy_frequency` and `rel_lead_time_p95` with its target and write the metrics block: value, target, met (true or false), sample size `count`/`total`. A window of fewer than 5 releases is flagged as thin evidence, not as a pass.
7. Do not fill an empty series: `rel_change_failure_rate`, `rel_deploy_frequency` and `rel_lead_time_p95` reject an empty array, so report that metric as `no data`.

## Outputs

- Metrics block with four values, their targets, `met` flags, window dates and sample sizes.

## Failure Handling

- `rel_deploy_frequency` INVALID_INPUT on a date: correct the entry from its record once, else drop it and list it as skipped.
- One tool DEPENDENCY_UNAVAILABLE twice: report that metric as `skipped:tool-missing`; never compute it by hand.
- Records without `promoted_at` are excluded and counted.

## Handover

- `to_agent`: A13 (A14 gets the same document in a second write). `goal`: delivery health for the window. `completed`: the four metrics with targets. `open_questions`: excluded records and thin samples. `decisions`: the daily-capability reading and the MTTR rule. `verification`: verified only when all four calls ran.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.
