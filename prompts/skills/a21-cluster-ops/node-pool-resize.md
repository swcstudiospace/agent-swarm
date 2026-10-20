---
name: a21-cluster-ops--node-pool-resize
description: Use when a rightsizing.plan or iac.change resizes node pools and you must prove the new node count keeps N+k headroom and the drain is feasible.
owner: a21-cluster-ops
tools: [k8s_nodepool_capacity, k8s_drain_feasibility, k8s_maintenance_window, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Node pool resize

## When to Use

- A `rightsizing.plan` (A20) proposes fewer or smaller nodes, or an `iac.change` (A11) changes a pool's node count, machine type or `max_nodes`, and you must say whether the pool still holds its requests.
- Verification only: the change itself is applied through A11's pipeline. Non-prod pool: L2. Production pool: L3, human approval. Deleting a pool that still holds workloads is cluster-scale destruction: L4.
- Growth over months is `skill://a21-cluster-ops--capacity-forecast`; one drain of named nodes is `skill://a21-cluster-ops--drain-and-cordon-plan`.

## Procedure

1. Read the proposed change with `read` (the `rightsizing.plan` or `iac.change` artifact): pool names, current and proposed node counts, allocatable per node. Gather live requests read-only: `kubectl --context <ctx> get pods -A -o json`, summed by pool via node selectors; the context and pool names must be explicit. Missing allocatable values: BLOCKED needs=input, never estimate from the machine type.
2. Call `k8s_nodepool_capacity` for the CURRENT state: `pools` (`{name, nodes, allocatable_cpu_m, allocatable_memory_mib, daemonset_cpu_m, daemonset_memory_mib, max_nodes}`), `workloads` (`{name, pool, replicas, cpu_request_m, memory_request_mib}`), `tolerated_node_failures` 1 (2 for a production pool with more than 10 nodes if the task states it), `max_utilization_pct` 85 unless the task sets another. Baseline `status` and `utilization_after_failure_pct` per pool.
3. Call `k8s_nodepool_capacity` again with the PROPOSED `nodes` and allocatable. Decision per pool: `status: ok` accepts; `tight` (above `max_utilization_pct` after losing `tolerated_node_failures` nodes) is accepted only with the owner's note; `at_risk` (above 100% after the loss) or any `blocker` finding (`workload/<name>/exceeds-node`: one pod cannot fit a node) rejects the proposal. Use `recommended_nodes` as the counter-proposal, capped by `max_nodes`; a `pool/<name>/max-nodes` finding means the ceiling itself must rise.
4. Cross-check the `k8s_nodepool_capacity` output by hand for each pool: requested cpu divided by `(nodes_new - tolerated_node_failures) * (allocatable_cpu_m - daemonset_cpu_m)` (same for memory) must equal the larger of the two and that value `utilization_after_failure_pct` (within 0.1). Confirm you passed `nodes_new`, not `nodes_old` minus the removed count. A mismatch is E-CONTRACT: report both values.
5. For a scale-down the removed nodes are drained. Call `k8s_drain_feasibility` with `pdbs` (`{name, namespace, expected_pods, current_healthy, one of min_available / min_available_percent / max_unavailable / max_unavailable_percent, pods_on_drained_nodes}`) from `kubectl get pdb -A -o json`. `verdict: blocked` (0 disruptions allowed with pods on those nodes) stops the resize; `throttled` is allowed and `max_waves` sets the duration.
6. Call `k8s_maintenance_window` with `proposed_start`, `duration_minutes` (`max_waves` times the per-wave time the task states) and the task's `windows` and `blackouts`; `allowed: false` moves the start to `earliest_compliant_start`.
7. Write the verdict table (pool, old vs new nodes, `status` before and after, `recommended_nodes`, drain `verdict`, window) with `write` to `cluster-reports/<cluster>/node-pool-resize.md`; digest it with `shared_artifact_digest`. For a production pool call `shared_escalate` with `{task_id, reason_code: "policy", summary: "prod node pool resize awaiting L3 approval", needs: ["human-approval"]}`.

## Outputs

- `cluster.capacity.report` per pool: before and after utilization, `recommended_nodes`, drain verdict, window, accepted or rejected with finding ids.

## Failure Handling

- INVALID_INPUT (`pools`: daemonset requests leave no allocatable, unknown pool in `workloads`; `pdbs`: not exactly one selector): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction` and name the unchecked pools; do not accept them.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and the resize stays unapproved.
- Requests are scheduling facts, not observed usage: never claim real saturation. If A20's plan and your live requests disagree, E-CONTRACT; report both.
- Never retry or run a resize, `kubectl drain` or `kubectl cordon` yourself.

## Handover

`to_agent` A20 (A23 and A25 read the same report). `goal` (accept or rework the resize), `completed`, `decisions` (accepted pools, `recommended_nodes`), `artifacts` (report, digest, producer A21), `blockers` (rejected pools, `human-approval`), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
