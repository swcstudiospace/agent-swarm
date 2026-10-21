---
name: a20-finops--rightsizing-sweep
description: Use when a finops.rightsize task asks to size Kubernetes workload requests from observed p95/p99 usage and price the saving as a rightsizing.plan for A21.
owner: a20-finops
tools: [finops_rightsize, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Rightsizing sweep

## When to Use

- A `finops.rightsize` task supplies per-workload requests and observed usage (A13 `deploy.telemetry`, A21 `cluster.capacity.report`) and wants a priced recommendation.
- Not for node counts or autoscaler limits (A21 owns cluster capacity), for moving workloads to spot (`skill://a20-finops--spot-adoption`) or for idle whole resources (`skill://a20-finops--idle-cleanup-proposal`).
- Autonomy: the plan is L2. Applying new requests in production is L3 and is done by A11 or A21 after human approval; you never patch a manifest or a live object.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A21 `cluster.capacity.report` and the A13 `deploy.telemetry`. Take per workload (1 to 200, unique `name`) `replicas`, requests (`cpu_request_m`, `mem_request_mib`) and the usage percentiles. This skill requires a window of at least 14 days that spans a full weekly cycle (a convention; the tool has no window input); a shorter window is BLOCKED `needs=input`. Name the context and namespace on every workload row; production rows are marked.
2. Run `python3 scripts/finops_cost_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest-dir> --json`. A `k8s-missing-requests` finding (major) means a container has no cpu or memory request to size against (a LimitRange or admission default is not evaluated): list it apart as "needs first request", not as a saving. `k8s-missing-limits` (minor) concerns memory limits only; CPU limits are deliberately not required.
3. Choose `cpu_basis`: `p99` for latency-critical or bursty services, `p95` for batch or tolerant ones; state the choice per tier in the plan. Prices (`cpu_price_per_core_hour`, `mem_price_per_gib_hour`) come from the task rate card; none supplied is BLOCKED `needs=input`.
4. Call `finops_rightsize` with `workloads` (all eight fields, `cpu_p95_m` not above `cpu_p99_m`, same for memory), `cpu_basis`, the two prices and leave `cpu_headroom_pct`, `mem_headroom_pct` at the default 15 (the Vertical Pod Autoscaler recommender margin) and `tolerance_pct` at 10, unless the task states otherwise.
5. Read `workloads[].action` (`downsize`, `upsize`, `mixed` when CPU and memory move in opposite directions, `keep`). With the default headroom, CPU is `ceil(usage x 1.15)` (usage is the `cpu_basis` percentile) and memory is `ceil(mem_p99 x 1.15)`: memory is not compressible, so memory is always sized on p99. A change inside `tolerance_pct` is `keep`, except memory whose p99 is above its request: that is always `upsize`. A dimension that is kept (inside the band, or a withheld downsize) reports its current request, rounded up to a whole unit, in `cpu_recommended_m` or `mem_recommended_mib`, so applying every `*_recommended` value changes only what `action` names. The tool applies no floor above 1 m or 1 MiB: apply the platform minimums of the task. A finding `RS-<name>-mem-peak` (major) means p99 memory is above the request: an eviction or OOM-kill candidate, so put this upsize first in the plan regardless of cost. A CPU downsize is withheld (`keep`) while p99 is above the request (`RS-<name>-cpu-peak`, minor).
6. Cross-check: run `finops_rightsize` again with the other `cpu_basis`. `monthly_saving_net` equals `monthly_saving_gross` minus `monthly_extra_cost`, and the sum of `workloads[].monthly_saving` matches it within 0.01 per workload (rounding). Report the p95-versus-p99 spread of `monthly_saving_net`. A `monthly_saving_net` at or below 0 is no saving: do not sell the plan as one, but upsizes flagged `mem-peak` stay recommended as reliability fixes, listed apart from the savings.
7. Write the `rightsizing.plan` with `write` at the path the task names, else `docs/finops/rightsizing-<scope>.md`: per workload the current and `*_recommended` values, action, saving, rollout order (upsizes first, then downsizes in small batches of workloads, for example a quarter per step, a convention of this skill), the rollback (previous requests) and the exact context and namespace. State that nothing was applied.
8. Production rows need L3 approval: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "apply production request changes", "needs": ["human-approval"]}`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `rightsizing.plan`: per-workload recommendation, net monthly saving, risks (`mem-peak`), rollout and rollback; evidence is the `finops_rightsize` outputs and the scan counts.

## Failure Handling

- INVALID_INPUT: fix the named field (a p95 above its p99 is a data error: ask for a re-export) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name the workloads without usage data.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never estimate a recommendation by hand.
- Script exit 1 is a major finding, not an error; exit 2 means fix `--path`.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.
- A usage window containing a deploy, incident or load test skews percentiles: say so and defer the figure to a clean window.

## Handover

- `to_agent`: `A21` (consumes `rightsizing.plan`). `goal`: apply the approved request changes in the named context and namespace.
- `completed`, `decisions` (basis per tier), `artifacts` (digest, producer `A20`), `blockers` (`human-approval`), `constraints` (upsizes first, prod is L3), `verification` (`partial` until A21 confirms).
- End with `shared_handover_validate` then `shared_handover_write`.
