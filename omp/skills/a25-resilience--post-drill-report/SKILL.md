---
name: "a25-resilience--post-drill-report"
description: "Use when a drill, game day or chaos run has finished and needs a resilience.report: hypothesis result, measured RTO and RPO, defects and next due date."
disable-model-invocation: true
---

# Post-drill report

## When to Use

- A restore drill, DR rehearsal, game day or chaos experiment has been run by a human or by A12 and the record exists; the output is the report that turns it into decisions.
- Not for planning the next one: `skill://a25-resilience--game-day-plan`. Not for incident timelines of real outages: A13 owns those.
- Reporting is L2; it executes nothing.

## Procedure

1. Use `read` on the drill record, logs and metrics export named in the task: start and end in UTC, scenario, who acted, steady-state metrics before, during and after, abort events, measured restore minutes and data-loss window. Missing end time or measurements: BLOCKED needs=input; never reconstruct from memory.
2. Judge the hypothesis with `read` of the metrics export: per steady-state metric, was the threshold held (`confirmed`) or crossed (`refuted`)? Did the abort fire when its threshold was crossed? A run that crossed an abort threshold without stopping is a defect in the experiment, whatever the system did.
3. Call `resil_drill_cadence` with `{as_of, requirements: [{scenario, max_interval_days, require_evidence, target_rto_minutes, target_rpo_minutes}], drills: [... this drill added with scenario, performed_at, outcome, evidence_ref, measured_rto_minutes, measured_rpo_minutes]}`. Read `schedule[].state`, `due_at`, `days_until_due` and the findings `rto-target-missed`, `rpo-target-missed`, `latest-drill-failed`, `drill-without-evidence`, `rto-not-measured`. A drill without `evidence_ref` does not count, so the next due date does not move.
4. For restore drills call `resil_restore_rto` with the planning inputs that were used for the estimate. Compare `estimated_with_margin_minutes` with `measured_rto_minutes`: a measured value above the estimate means the model is optimistic (record which input was wrong); above `target_rto_minutes` is a blocker for the scenario regardless of the estimate.
5. For every surprise (alert late, runbook step wrong, dependency missed) add a defect row: finding, evidence, owner agent, the skill that fixes it. Then call `resil_fmea_rpn` with the affected modes re-scored from the drill evidence: detection falls only when an alert fired in time during the drill, occurrence is not changed by a drill. Compare `max_rpn` with the earlier FMEA.
6. Use `read` on the previous `resilience.report` for this service and carry its unresolved defects forward with their age; close one only with evidence from this drill. Call `shared_open_findings` with `{task_id}` to see failing gate verdicts other gates hold on the task (stale ones are replaced) and name them in the report.
7. Use `write` for `resilience-reports/<service>/drill-<date>.md`: scenario, UTC timeline, hypothesis result, measured versus target RTO and RPO, defects with owners, next `due_at`, links to evidence. Call `shared_artifact_digest` with `{path}` for it and for each evidence file you cite and keep in the workspace.
8. State the verdict per scenario from `resil_drill_cadence` (`outcome` and findings), never from tone: `passed`, `partial` or `failed`. Any target missed or any `blocker` makes it at most `partial`.

## Outputs

- `resilience.report`: the drill report with verdict, measured numbers, defect list and digests.

## Failure Handling

- INVALID_INPUT (timestamps without UTC offset, `performed_at` after `as_of`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the scenarios not evaluated. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Estimate and measurement disagree by an order of magnitude: suspect a unit error (MiB against GiB) first and report E-CONTRACT with both values.

## Handover

`to_agent` A12 (consumes `resilience.report`). `goal`: use the verdict in the release decision. `completed`, `decisions` (verdict per scenario), `artifacts` (report with digest, producer A25), `open_questions` (open defects), `verification` (`verified` only with evidence for every figure). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a25-resilience/post-drill-report.md; edit the source and run scripts/build_agents.py -->
