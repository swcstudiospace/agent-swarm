---
name: "a11-devops--cost-guardrail"
description: "Use when per-environment cost telemetry arrives and actual spend must be checked against the modeled cost under the 120 percent downscale rule."
disable-model-invocation: true
---

# Cost guardrail

## When to Use

- Read-only billing telemetry gives an `actual_cost` for an environment that has a `modeled_cost` (both from the task inputs or a read-only billing query the task names).
- Rule (agents/A11-devops.md section 4.4): above 120 percent of modeled cost means auto-downscale and a report to A03 and A13. Drift is `skill://a11-devops--drift-reconcile`.

## Procedure

1. Call `devops_env_tier` with `{"env_name": "<name>"}`. Ephemeral and staging may be downscaled at L2. For `production` the downscale is an `iac.change` at L3: run steps 2 and 3 only to report the ratio, then continue with `skill://a11-devops--prod-infra-change`.
2. Call `devops_cost_overrun` with `{"modeled_cost": <number above zero>, "actual_cost": <number>}` in the same currency and period. Note `ratio`, `overrun` and `action`.
3. Read `ratio` against the two levels: at most 1.10 meets the target (section 6, within 110 percent of model); above 1.10 up to 1.20 is a watch item and is only reported; `overrun` true (above 1.20) means `action` `auto-downscale`.
4. For `auto-downscale` on a non-production tier, call `devops_change_gate` with `{"env_tier": "<tier>"}`; require `allowed`. Then `edit` the IaC to reduce replicas, node size or TTL and run `python3 scripts/devops_ci_check.py --task-id $TASK --correlation-id $CORR --json --path <iac-dir>` to keep the change lint-clean. The pipeline applies the change (your guard refuses `terraform apply`); the downscale counts as applied only once the pipeline reports it.
5. Run `devops_cost_overrun` again with the next telemetry reading after the downscale and record whether `ratio` fell.
6. Write `cost.report` (single-writer artifact) as a file in the repository workspace outside `.swarm/` (your guard refuses writes there), at the path the task names; when none is named use `<iac-dir>/records/<env_id>.cost.json` (a convention of this skill, not a runtime path). Call `shared_artifact_digest` with `{path}`; then see Handover.

## Outputs

- `cost.report`: `env_id`, `modeled_cost`, `actual_cost`, `ratio`, `overrun`, `action`, the downscale applied, the follow-up `ratio`.

## Failure Handling

- `modeled_cost` zero or missing (INVALID_INPUT, field `modeled_cost`): do not guess a model; BLOCKED `needs=input` for A03's cost model.
- Billing data absent: report `skipped:tool-missing`; do not downscale on missing data.
- `devops_change_gate` not `allowed`: report only, no edit.
- DEPENDENCY_UNAVAILABLE: retry twice, then report unchecked.

## Handover

Fill `goal` (re-model capacity or accept the cost), `completed` (downscale), `remaining` (A03 re-model), `decisions` (`ratio`, `action`), `artifacts` (cost report with digest, producer A11), `constraints`, `verification`. The prompt has A01 route the overrun to A03 and A13: call `shared_handover_validate`, then `shared_handover_write` to A01.

<!-- generated from prompts/skills/a11-devops/cost-guardrail.md; edit the source and run scripts/build_agents.py -->
