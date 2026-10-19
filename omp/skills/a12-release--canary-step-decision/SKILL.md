---
name: "a12-release--canary-step-decision"
description: "Use when deploy.telemetry for a canary step arrives and you must decide promote, hold or rollback from error rate, p95, SLO burn and soak time."
disable-model-invocation: true
---

# Canary step decision

## When to Use

- A13 sends `deploy.telemetry` (its `deploy_telemetry` block: `release_id`, `step_pct`, `error_rate`, `p95_ms`, `verdict` continue, hold or rollback-suggested, `confidence`) and the next step is waiting on you.
- You stop at the decision. The rollback itself is `skill://a12-release--auto-rollback`; unreadable telemetry is `skill://a12-release--monitoring-degraded-halt`.

## Procedure

1. Use `read` on the telemetry record and on `.swarm/releases/<release_id>.plan.json` (the file `skill://a12-release--plan-canary` found). Note `step_pct`, `error_rate`, `p95_ms`, the plan `guardrails` (`error_rate_max`, `p95_ms_max`, `slo_burn_max`, `soak_min`) and the burn rate: A13's `evidence.burn_rate` is a string such as `14.2x`, strip the `x`. The A13 `verdict` is advice; you decide.
2. Call `rel_guardrail_breaches` with `observed: {error_rate, p95_ms, slo_burn}` and `limits: {error_rate_max, p95_ms_max, slo_burn_max}` copied from the plan (exactly those three keys: `soak_min` is refused there). A non-empty `missing_metrics` means the sample cannot be judged: hold at the current step and use `skill://a12-release--monitoring-degraded-halt`. `breached` true means rollback.
3. Call `rel_canary_verdict` with `canary_error_rate` (telemetry `error_rate`), `baseline_error_rate` (the pre-canary window A13 supplied), `error_rate_max` (plan), `max_regression` (task inputs), `soak_observed_min` and `soak_required_min` (plan `soak_min`). Take `soak_observed_min` from the time since A11 confirmed this step (see `skill://a12-release--resume-partial-promotion` for confirmation), computed with `bash` `date`. A baseline or `max_regression` that is not in the task inputs is BLOCKED needs=input; never invent either.
4. Call `rel_rollback_trigger` with `burn_rates` (the series A13 supplied for this step, or `[slo_burn]` when you have one sample), `slo_burn_max` (plan) and `consecutive_required` from the task inputs, or 1 when absent (any sample over the limit triggers). `triggered` true means rollback.
5. Combine the outputs of `rel_guardrail_breaches`, `rel_canary_verdict` and `rel_rollback_trigger`, most restrictive wins: rollback beats hold beats promote. Reasons come from the tool output (`breaches`, `decision`/`reason`, `breach_index`).
6. Call `shared_status_report` with `{task_id, kind: "progress", step: "canary <step_pct>% <decision>", percent: step_pct}` so the live bar shows the decision.
7. Act on the decision: for `promote`, `read` the plan `steps_pct` for the entry after `step_pct` and report the next-step `promote.command` (`release_id`, `step_pct`) in your result. You hold no signing tool, so it is unsigned: A01 must route it for signing, and A11 rejects an unsigned command. For `hold`, repeat this skill at the next telemetry; for `rollback`, continue with `skill://a12-release--auto-rollback`.

## Outputs

- One decision (`promote`, `hold` or `rollback`) with `step_pct`, the combined reason, and the numbers that drove it.

## Failure Handling

- `rel_canary_verdict` `hold` with `soak_incomplete`: not a failure; wait for the soak time.
- Any tool DEPENDENCY_UNAVAILABLE twice: `hold`, never `promote`; report the tool name.
- Telemetry older than the step window: treat as missing, hold.

## Handover

- `to_agent`: A01. `goal`: next step for release `<release_id>`. `completed`: step and metrics judged. `remaining`: the next canary step or the rollback. `decisions`: decision with the three tool outputs as rationale. `verification`: verified only when all three tools ran.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.

<!-- generated from prompts/skills/a12-release/canary-step-decision.md; edit the source and run scripts/build_agents.py -->
