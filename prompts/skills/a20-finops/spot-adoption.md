---
name: a20-finops--spot-adoption
description: Use when a finops.rightsize task asks which workloads can move to spot or preemptible capacity and what share of replicas may run there, as a rightsizing.plan.
owner: a20-finops
tools: [finops_spot_suitability, finops_commitment_coverage, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Spot and preemptible adoption

## When to Use

- A `finops.rightsize` task asks whether stateless, batch or checkpointed workloads can run on spot (AWS Spot, GCP Spot VMs, Azure Spot) and what that saves after interruption cost.
- Not for the request sizes themselves (`skill://a20-finops--rightsizing-sweep`) or for node-pool and autoscaler limits (A21 owns cluster capacity; A11 applies node-pool changes).
- Autonomy: the suitability study is L2. Adding a spot node pool or changing a production workload's scheduling is L3, applied by A11 or A21 after approval.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A21 `cluster.capacity.report` (node pools, instance families, zones, disruption budgets) and the task's workload list. For each workload collect `replicas`, `on_demand_hourly` and `spot_hourly` (current prices from the task, per instance), `fault_tolerance` (`stateless`, `checkpointed` or `stateful`), `recovery_minutes` (time to be serving again after losing an instance) and, for `checkpointed`, `checkpoint_interval_minutes`. Missing prices or recovery times are BLOCKED `needs=input`.
2. Take `interruption_pct_month` (0 to 100) from the provider's interruption advisor or history for the instance type and region, as named by the task, using the upper bound of the published band (enter 100 for an open-ended top band: the workload is then `unsuitable` under the default maximum). The tool reads it as the expected interruptions per instance per month. A figure with no source in the task is BLOCKED `needs=input`, never a typical value.
3. Call `finops_spot_suitability` with `workloads` (all fields above, 1 to 200, unique `name`) and leave `max_interruption_pct` (default 20), `min_savings_pct` (default 30) and `on_demand_floor_pct` (default 20) unless the task sets them.
4. Read `workloads[].verdict` and `reason`, in the tool's order: `unsuitable` for `stateful-no-checkpoint`, `single-replica`, `interruption-rate-above-max` (above `max_interruption_pct`); `marginal` for `savings-below-minimum` (`savings_pct` after interruption overhead below `min_savings_pct`); else `suitable` (reason `ok`). Only `suitable` rows get `spot_replicas` (replicas minus the on-demand floor, at least 1 replica stays on demand) and a `monthly_saving`. `savings_pct` and `effective_spot_hourly` already include the lost time (recovery minutes plus, for `checkpointed`, half the checkpoint interval): do not subtract it again.
5. Cross-check the `finops_spot_suitability` totals: `monthly_saving_total` equals the sum of `workloads[].monthly_saving` within 0.01 per workload (rounding), and `suitable_count` plus `marginal_count` plus `unsuitable_count` equals `workload_count`. The tool uses 730 hours per month; a disagreement with a hand figure of 720 or 744 hours is a unit difference, state it.
6. Cross-check commitments: spot usage may not be eligible to draw down a commitment (confirm in the provider's terms and name it an open question). Call `finops_commitment_coverage` with `usage` of the family with the `spot_replicas` hours removed, `discount_pct` from the task and the existing `commitment`. `utilization_pct` falling below `breakeven_utilization_pct` (100 - `discount_pct`) means the spot move makes an existing commitment lose money: state the netting before recommending, and prefer keeping the on-demand floor replicas under the commitment.
7. State the runtime conditions the plan requires, as requirements, not as done: diversify across several instance types and zones, termination handling inside the provider's documented interruption notice (take its length from the provider docs, it differs by provider), PodDisruptionBudgets, graceful shutdown, and the on-demand floor. Write the `rightsizing.plan` with `write` at the path the task names, else `docs/finops/spot-<scope>.md`.
8. Production workloads: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "move production replicas to spot", "needs": ["human-approval"]}`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `rightsizing.plan` (spot section): verdict and reason per workload, `spot_replicas`, `monthly_saving`, commitment netting, runtime requirements; evidence is the tool outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (`checkpointed` work needs `checkpoint_interval_minutes`, names must be unique) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name unpriced workloads.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Spot prices and interruption data change over time: date the inputs in the plan and defer to the tool for every figure.
- Notice periods, advisor bands and commitment eligibility are provider facts the tool does not hold: cite the provider documentation as an open question when unsure.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A21` (consumes `rightsizing.plan`). `goal`: add the spot capacity to the named node pools after approval.
- `decisions`: verdicts and floor; `artifacts`: plan digest, producer `A20`; `blockers`: `human-approval`; `constraints`: keep the on-demand floor; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.
