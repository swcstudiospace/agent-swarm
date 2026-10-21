---
name: a21-cluster-ops--autoscaler-tuning
description: Use when HPA, VPA, Cluster Autoscaler or Karpenter settings need a sanity audit: missing requests, VPA conflicts, saturation and node-pool ceilings.
owner: a21-cluster-ops
tools: [k8s_autoscaler_sanity, k8s_nodepool_capacity, k8s_quota_fit, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Autoscaler tuning

## When to Use

- An `incident.alert` (pods Pending, HPA stuck at max), an A20 `rightsizing.plan` or a new `platform.claim` involves HPA, VPA, Cluster Autoscaler or Karpenter, and you must say which bounds are wrong.
- Settings audit and recommendation. Editing an HPA or a NodePool resource is A22 (workloads) or A11 (IaC); production edits are L3. Months-long node growth is `skill://a21-cluster-ops--capacity-forecast`.

## Procedure

1. Collect read-only with explicit `--context` and `-A`: `kubectl get hpa -A -o json` (min, max, current replicas, metric type, target), `kubectl get vpa -A -o json` (`updatePolicy.updateMode`, `resourcePolicy.controlledResources`), the pod requests behind each target, and the pools with their `min_nodes`, `max_nodes`, `current_nodes` and owner (`cluster-autoscaler`, `karpenter` or `none`). Read the task's `incident.alert` with `read` for the symptom.
2. Call `k8s_autoscaler_sanity` with `hpas` (`{name, min_replicas, max_replicas, current_replicas, metric, target_pct, cpu_request_m, memory_request_mib, pool, vpa_mode, vpa_resources}`; `metric` is `cpu_utilization`, `memory_utilization` or `custom`; `target_pct` is required for the first two) and `pools` (`{name, min_nodes, max_nodes, current_nodes, allocatable_cpu_m, allocatable_memory_mib, managed_by}`).
3. Triage the `k8s_autoscaler_sanity` findings by id and severity. `blocker` `hpa/<name>/no-request`: a utilization HPA on a pod with no request never scales; the fix is a request (find the value with `skill://a21-cluster-ops--capacity-forecast` data, not by guessing). `major` `vpa-conflict`: VPA `Recreate`, `Auto` or `InPlaceOrRecreate` controls the same resource the HPA targets; recommend VPA `Off` (recommendation only) or a custom metric. `major` `at-max` (HPA or pool): ceiling reached, raise it only after the next step. `minor`: `fixed` min equals max, `target-above-request` above 100%, `vpa-initial`, `two-node-autoscalers` (Cluster Autoscaler and Karpenter must own disjoint node sets).
4. For each `hpa-demand-exceeds-ceiling` or `at-max` pool, quantify the fix: call `k8s_nodepool_capacity` with the pool at the candidate `max_nodes` and `workloads` equal to each HPA at `max_replicas` (`replicas` = `max_replicas`). Use `recommended_nodes` as the minimum sensible `max_nodes`; `at_risk` at the candidate means it is still too low.
5. Cross-check the two tools in ONE direction only: for any `pool/<name>/hpa-demand-exceeds-ceiling` finding from `k8s_autoscaler_sanity` on a managed pool (HPA demand at `max_replicas` above `max_nodes` times allocatable), `k8s_nodepool_capacity` at the CURRENT `max_nodes` with the same requests must report `recommended_nodes` greater than `max_nodes` (a `pool/<name>/max-nodes` finding); if it does not, that is E-CONTRACT (check that both used the same requests) and you report both. The reverse does not hold: `k8s_nodepool_capacity` also reserves N+k failure headroom and the target utilization, so it may flag `max-nodes` where `k8s_autoscaler_sanity` is clean; that is a stricter threshold, not a disagreement. A `pool/<name>/at-max` finding (current nodes at `max_nodes`) is not checked this way.
6. Where a namespace quota may cap scale-out, call `k8s_quota_fit` with the HPA `max_replicas`, the container requests/limits and the `quota_hard`/`quota_used`. `max_replicas_fit` below `max_replicas` means the HPA ceiling is unreachable: recommend the quota change (owner A22) or the lower max.
7. Write the audit with `write` to `cluster-reports/<cluster>/autoscaler-audit.md`: finding id, object, severity, recommended bound with the tool value it comes from, owner, autonomy (non-prod change L2, prod L3). Digest it with `shared_artifact_digest`. You change no bound yourself.

## Outputs

- `cluster.capacity.report` section on autoscaling: verdict (`ok`, `attention`, `misconfigured`), findings with owners and recommended bounds, file digest.

## Failure Handling

- INVALID_INPUT (`min_replicas` above `max_replicas`, unknown `pool`, missing `target_pct`, duplicate names): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unaudited objects; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tools check configuration, not live behaviour (scale-up latency, Karpenter consolidation churn, expander choice, scale-down delay). Say so; ask A13 for traces if the alert needs them.
- Karpenter `NodePool` limits and Cluster Autoscaler `--nodes=min:max:group` use different names: map them to `min_nodes`/`max_nodes` explicitly and note the source flag or field.
- Never patch an HPA, VPA or pool; L3 for production.

## Handover

`to_agent` A20 (A25 reads the same report). `goal` (apply or reject the recommended bounds), `completed`, `decisions` (each bound with its source tool value), `artifacts` (audit file, digest, producer A21), `open_questions`, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
