---
name: "a20-finops--anomaly-triage"
description: "Use when a finops.model task reports a cost spike or alert on a running service and the cause must be sized and correlated with a release or telemetry change."
disable-model-invocation: true
---

# Spend anomaly triage

## When to Use

- A daily-spend alert, a budget notification or a person reports that a service costs more than usual and someone must say how much, since when and probably why.
- Not for setting up the alerting (`skill://a20-finops--budget-guardrail-setup`), for the SLO or incident side of a release (A13 owns SLOs and telemetry) or for fixing the cause (the owning agent does; you recommend).
- Autonomy: analysis is L2. Any change to the service, budget or limits is L3 and belongs to A11, A21 or the owner.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A12 `release.record` and the A13 `deploy.telemetry` of the service. Note release or config changes with their instants inside the last 14 days (this skill's window), and the telemetry windows (traffic, error rate) you will compare. No series from the task is BLOCKED `needs=input`.
2. Call `finops_spend_anomaly` with `daily_spend` (oldest first, one value per consecutive calendar day, no gaps; the tool needs 7 to 366 values, this skill asks for 28 or more so the median is stable), `start_date` (YYYY-MM-DD of the first value) and the default `threshold` 3.5 (modified z-score 0.6745 x (value - median) / MAD, flagged when |z| is above the threshold). Read `verdict` (`anomalous` or `normal`), `anomalies` (`index`, `date`, `value`, `z_score`, `direction`), `spike_count`, `excess_spend` and `scale_method`.
3. Classify each row of `finops_spend_anomaly` `anomalies`. Upward with |`z_score`| of at least 2 x `threshold` (7 at the default) is major, other upward is minor, downward is information only (a drop may be a metering gap: ask). `scale_method` `mad` is normal; `mean_ad` means the MAD was 0 (over half the days equal the median); `constant` means identical values and no anomaly can be found: report that limit, not "normal".
4. Check seasonality: the tool uses one median for the whole series, so a weekly cycle can look like anomalies. If every anomaly `date` falls on one weekday (or pair) and the series has 49 or more days (the tool needs 7 points), rerun `finops_spend_anomaly` with only that weekday: every 7th value from the first such day, `start_date` = that day. That series is weekly, so ignore the returned `date` (the tool assumes consecutive days): the real day is the first day + 7 x returned `index`. `normal` proves a weekly pattern, not an incident. Under 49 days seasonality is untested: say so and ask for a longer series.
5. Correlate: the first anomalous `date` against the release and config instants of step 1. A change within 24 hours before it is the leading hypothesis (a convention of this skill); none is "unexplained, open". Call `finops_unit_cost` for a median day: `costs` (`category`, `amount` of that day), `volume` (requests served that day from telemetry, above 0), `unit` `request`; note its `unit_cost`. Call it again for the anomalous day with that `unit_cost` as `target_unit_cost`: `over_target` raises `UC-TARGET` (minor; major above 1.25 x the target) and means a cost regression; unit cost flat while volume rose means a traffic effect, not a defect.
6. Cross-check the impact: call `finops_budget_burn` with `budget`, `spend_to_date`, `period_start`, `period_end`, `as_of` (ISO-8601 with an explicit offset; `as_of` after `period_start`, not after `period_end`) and `recent_daily_spend` (the last 7 days, including the spike) and then without it. `projected_total` with the spike in the run rate (`run_rate_source` `recent_daily_spend`) versus without (`period_average`) shows the overrun risk if the cause persists; `verdict` `will_overrun` or `over_budget` is stated with `overrun_at` (empty when already over budget). Check `excess_spend`: it equals the sum of (`value` - `median`) over the `anomalies` with `direction` `up`, i.e. their summed `value` minus `median` x `spike_count`, within rounding; a mismatch is a tool defect to report (E-CONTRACT), never to adjust.
7. Write the `cost.report` with `write` at the path the task names, else `docs/finops/anomaly-<service>-<date>.md`: the series window, anomalies, `excess_spend`, correlation, unit-cost comparison, projection, hypotheses ordered by evidence, and recommended owner actions (for example "revert release R", "lower log verbosity", "cap autoscaler maximum"; none executed by A20). Never state a cause you did not correlate.
8. When `will_overrun` or `over_budget` and the cause is still active, call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "active cost spike, owner action needed", "needs": ["human-approval"]}`: the action is an L3 change by someone else.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report`: anomaly list, excess spend, correlated change, projection, recommended owner actions; evidence is the tool outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (`start_date` must be YYYY-MM-DD, series at least 7 days) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name missing days.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no verdict by eye.
- Billing data can arrive late: treat the newest day as provisional when the export is incomplete, and say so.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A17` (consumes `cost.report`; the platform owner of the service informs the team). `goal`: act on the named hypothesis.
- `decisions`: classification and hypothesis; `artifacts`: report digest, producer `A20`; `open_questions`: unexplained days; `verification`: `partial` unless the cause was confirmed by telemetry.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a20-finops/anomaly-triage.md; edit the source and run scripts/build_agents.py -->
