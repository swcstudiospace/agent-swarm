---
name: a21-cluster-ops--capacity-forecast
description: Use when a capacity question needs a node-count and quota forecast under stated growth scenarios, with headroom per pool and namespace and where it ends.
owner: a21-cluster-ops
tools: [k8s_nodepool_capacity, k8s_quota_fit, k8s_autoscaler_sanity, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Capacity forecast

## When to Use

- A task (a `platform.claim` from A17 for a new tenant, an A20 or A25 question, an `incident.alert` of pending pods) asks how much capacity a cluster needs over a horizon under growth you are given.
- Growth rates come from the task; you never invent them. A concrete change to one pool is `skill://a21-cluster-ops--node-pool-resize`; HPA and autoscaler bounds alone are `skill://a21-cluster-ops--autoscaler-tuning`. Analysis only: L2 anywhere, it changes nothing.

## Procedure

1. Read the task with `read`: horizon, scenarios (for example baseline, +20%, +50% replicas) and the date or step at which each applies. No growth figure supplied: BLOCKED needs=input; "use 20%" is not yours to choose.
2. Collect current state read-only with explicit `--context`: node pools (allocatable, `daemonset_*`, `nodes`, `max_nodes`) and workloads (`replicas`, requests) via `kubectl get nodes -o json` and `kubectl get pods -A -o json`.
3. Per scenario call `k8s_nodepool_capacity` with workloads' `replicas` scaled by the scenario factor, rounded up (`ceil`), `tolerated_node_failures` 1 and `max_utilization_pct` 85 unless the task sets others. Record per pool `status`, `utilization_after_failure_pct` and `recommended_nodes`. The first scenario with `at_risk` or a `pool/<name>/max-nodes` finding is where headroom ends; give the scenario, not an invented date, unless the task maps scenarios to dates.
4. Call `k8s_quota_fit` for each tenant namespace that grows: `replicas` (scaled), `containers` (`{name, cpu_request_m, memory_request_mib, cpu_limit_m, memory_limit_mib}`), `quota_hard` and `quota_used` (from `kubectl get resourcequota -n <ns> -o json`, keys such as `requests_cpu_m`, `pods`) and `limit_range`. `max_replicas_fit` below the scaled replicas, or `verdict: over_quota`, means the quota binds before the nodes do; `rejected` is a LimitRange or request problem that fails regardless of size.
5. Call `k8s_autoscaler_sanity` with `hpas` and `pools` (`min_nodes`, `max_nodes`, `current_nodes`, `managed_by`). A `pool/<name>/hpa-demand-exceeds-ceiling` finding or `at-max` means autoscaling cannot deliver the forecast even if nodes exist on paper.
6. Cross-check the two views of the same ceiling: for every pool where `k8s_nodepool_capacity` has `recommended_nodes` above the `max_nodes` it was given, it must carry a `pool/<name>/max-nodes` finding (same input, same tool: a missing finding is E-CONTRACT, report it). `k8s_autoscaler_sanity` uses a looser test (HPA demand at `max_replicas` against `max_nodes` times allocatable, no failure headroom, no target utilization), so it may be clean where `k8s_nodepool_capacity` flags the ceiling; report that as the stricter reading, not as a disagreement. The opposite case, a `pool/<name>/hpa-demand-exceeds-ceiling` finding with no `max-nodes` finding in `k8s_nodepool_capacity` for the same requests and `max_nodes`, is E-CONTRACT: report both.
7. Write `cluster-reports/<cluster>/capacity-forecast.md` with `write`: table scenario x pool (`recommended_nodes`, status), quota binding points, ceilings, assumptions copied from the task. Digest it with `shared_artifact_digest`. Buying or ordering capacity is not yours: recommendations go to A20 and a human; nothing is applied.

## Outputs

- `cluster.capacity.report`: per scenario and pool `recommended_nodes`, first at-risk scenario, quota bind points, assumptions, finding ids, file digest.

## Failure Handling

- INVALID_INPUT from any tool: fix the named `field` (for example duplicate pool name, request above limit in `containers`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; forecast only the completed pools and name the gap.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` for that dimension and say the forecast omits it.
- Requests only: these tools use requested resources, never observed usage. Do not present headroom as real utilization; when observed usage matters, ask A13 for it.
- `k8s_quota_fit` `max_replicas_fit` of -1 means the quota does not track the checked dimensions: say "unbounded by quota", not a number.

## Handover

`to_agent` A25 (A20 and A23 consume the same `cluster.capacity.report`). `goal` (check the resilience impact of the forecast ceilings), `completed`, `decisions` (scenarios run and their factors), `artifacts` (report, digest, producer A21), `open_questions` (unsupplied growth, usage data), `verification` (`partial` when a dimension was skipped). Call `shared_handover_validate`, then `shared_handover_write`.
