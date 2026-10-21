---
name: a12-release--monitoring-degraded-halt
description: Use when canary telemetry or the flag service is missing or down and you must halt at the current step instead of promoting blind.
owner: a12-release
tools: [rel_guardrail_breaches, shared_status_report, shared_handover_validate, shared_handover_write]
---

# Halt when monitoring is degraded

## When to Use

- A `monitoring.degraded` broadcast, an unreadable `deploy.telemetry`, or a sample that lacks a guardrail metric (spec section 5: pipeline down means halt at the current step, fail closed).
- No breach has been observed. When a breach is visible too, rollback comes first: `skill://a12-release--auto-rollback`.

## Procedure

1. Call `rel_guardrail_breaches` with whatever the last sample contains as `observed` and the plan limits (`error_rate_max`, `p95_ms_max`, `slo_burn_max` only) from `.swarm/releases/<release_id>.plan.json` (use `read`). Read `missing_metrics` (`error_rate`, `p95_ms`, `slo_burn`). A non-empty list, or an unreadable record, means the guardrails cannot be evaluated. Empty `missing_metrics` with `breached` false means monitoring is fine: this skill does not apply.
2. When `rel_guardrail_breaches` reports `breached` true in the same call, stop and use `skill://a12-release--auto-rollback`. A breach beats a halt.
3. Halt: use `read` on the plan file to confirm the current `step_pct`, then state in your result that no `promote.command` is issued for the next step; the release stays at that step with the canary traffic unchanged. Nothing needs writing to disk, and your guard refuses writes into `.swarm/`.
4. Decide the flag service case: the plan file has no flag field, so `read` the task inputs for whether the release depends on feature flags. A deploy that relies on flags is blocked while the flag service is down, a non-flag deploy may proceed (spec section 5); when the inputs do not say, treat it as flag-dependent and state that.
5. Call `shared_status_report` with `{task_id, kind: "blocked", step: "halted at <step_pct>%: monitoring degraded", percent: step_pct}`.
6. Add a finding of kind `gate`, severity `major`, to your final json block and to the `blockers` of the `shared_handover_write` document: "monitoring.degraded: promotion halted at <step_pct>%" with `missing_metrics` as evidence, and report FAILED with E-DEP and the current step.
7. Ask A13 for restored telemetry through `shared_handover_write` (see Handover); A13 side is `skill://a13-observability--pipeline-degraded`.

## Outputs

- A halt record: release, step, missing metrics, the finding, and the state E-DEP.

## Failure Handling

- Telemetry returns later: re-run step 1 with the fresh sample and continue with `skill://a12-release--canary-step-decision`; never reuse the old sample.
- Never fill a missing metric from memory or an earlier step.
- `shared_status_report` DEPENDENCY_UNAVAILABLE: the halt still stands; report it in the result.

## Handover

- `to_agent`: A13. `goal`: restore the guardrail telemetry for `<release_id>`. `completed`: halted at the step. `remaining`: telemetry back, then a decision. `blockers`: the missing metrics. `constraints`: the release does not advance until a full sample is judged. `verification`: verified (the halt itself is certain).
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.
