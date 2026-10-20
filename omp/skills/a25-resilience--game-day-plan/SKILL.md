---
name: "a25-resilience--game-day-plan"
description: "Use when a resilience.chaos task needs a game day: ordered scenarios, roles, abort path and cumulative error-budget cost, delivered as a chaos.plan."
disable-model-invocation: true
---

# Game day plan

## When to Use

- A `resilience.chaos` task asks for a game day: several experiments in one window with people on call, before a launch or after an `incident.alert` from A13.
- Not for one experiment spec: use `skill://a25-resilience--chaos-experiment-design`. Not for a DR failover: use `skill://a25-resilience--dr-failover-runbook`. SLOs, alerts and incidents stay with A13.
- Staging runs are L2, production runs are L3. This skill plans; it never injects a fault.

## Procedure

1. Use `read` on the task inputs: `resilience.design` (A16 failure scenarios), `slo.manifest` (target and window), `deploy.telemetry` and open `incident.alert` (A13), `cluster.capacity.report` (A21). No SLO target or window: BLOCKED needs=input.
2. Call `resil_fmea_rpn` with `{"rpn_threshold": <task policy>, "modes": [{id, severity, occurrence, detection}, ...]}`, one mode per candidate scenario. When the task gives no threshold use 100 and record it as an assumption in `decisions`. Plan the scenarios whose `ranked[].action_required` is true, highest `rpn` first, stopping at `pareto_count` when more than 6 qualify.
3. Call `resil_drill_cadence` with `{as_of, requirements: [{scenario, max_interval_days, require_evidence}], drills: [...]}` from the drill log. Scenarios with `schedule[].state` of `never-drilled` or `overdue` run first; `compliant` ones stay only when `action_required`.
4. Per scenario call `resil_chaos_spec_check` with `environment` `staging` (all required fields of its contract: steady_state, abort_conditions, affected_instances, total_instances, max_blast_percent, freeze_windows ...). Keep it only when `blockers` is 0 and `required_autonomy` is `L2`; otherwise fix the named finding id.
5. For production candidates call `resil_chaos_budget_gate` one scenario at a time in plan order, with `budget_remaining_fraction` and `current_burn_rate` taken from A13 evidence (`obs_budget_remaining` output, never recomputed here). Feed each `remaining_after_fraction` into the next gate call as `budget_remaining_fraction`. `decision` `no-go` removes the scenario from production and keeps its staging run.
6. Re-run `resil_chaos_spec_check` with `environment` `production` and `error_budget_gate_passed` true only for `go` scenarios. `required_autonomy` must be `L3`; a lone `approval-missing` blocker is expected until a human approves. `freeze-window` or `manual-abort-only` blockers mean re-schedule or wire an automatic abort.
7. Use `write` to create `chaos-plans/<service>/game-day.md`: scenario table (order, environment, start in UTC, duration, blast percent, abort metric, rollback), roles (commander, operator, observer, scribe), comms and stop word, gate numbers per scenario. Call `shared_artifact_digest` with `{path}` for it.
8. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "production game day awaiting L3 approval", "needs": ["human-approval"]}` when any production scenario survives step 6; return its `result_patch` as the BLOCKED result. A staging-only plan can complete.

## Outputs

- `chaos.plan`: the game-day document with per-scenario spec-check `findings`, gate `decision` and `cost_fraction`, and the file digest.

## Failure Handling

- INVALID_INPUT on any `resil_` tool: fix the named `field` (timestamps need an explicit UTC offset) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the scenarios not evaluated. DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` and treat that gate as not passed.
- No A13 budget evidence: the production gate is not passed; plan staging only.
- L3 production run or any L4 step: BLOCKED needs=human-approval; never retry a non-idempotent fault injection.

## Handover

`to_agent` A12 (consumes `chaos.plan`). `goal`: run the game day inside the approved window. `completed`: tools run. `decisions`: threshold assumption, gate `decision` per scenario. `artifacts`: plan with digest, producer A25. `open_questions`: approvals and unmeasured budget inputs. `verification`: `partial` until a human approves. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a25-resilience/game-day-plan.md; edit the source and run scripts/build_agents.py -->
