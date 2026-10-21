---
name: a21-cluster-ops--cluster-bootstrap-baseline
description: Use when a new cluster comes from a cloud.architecture, iac.change or platform.claim and its baseline (version, pools, quotas, window) must be verified.
owner: a21-cluster-ops
tools: [k8s_version_skew, k8s_nodepool_capacity, k8s_quota_fit, k8s_maintenance_window, shared_artifact_digest, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Cluster bootstrap baseline

## When to Use

- A new cluster or environment is designed (`cloud.architecture` from A16), provisioned as code (`iac.change` from A11) or requested by a tenant (`platform.claim` from A17), and the lifecycle side of its baseline must be checked before it takes workloads.
- Verification and plan only. Provisioning belongs to A11 (non-prod L2, production L3); admission and policy baselines (PSA, network policy, RBAC) belong to A24; networking to A23. A cluster is never deleted from this skill: delete or rebuild is L4.

## Procedure

1. Read the inputs with `read`: the architecture (`cloud.architecture`), the IaC change, the claim. Extract the intended Kubernetes version, the number of control-plane instances, the node pools (size, allocatable, `max_nodes`, surge settings), the add-on set, the maintenance windows and the tenant namespaces. A missing field is BLOCKED needs=input; never default it.
2. Version check: call `k8s_version_skew` with the planned `apiserver_versions` (every control-plane instance) and `components` (kubelet per pool, kube-proxy, kubectl of the pipeline). Require `verdict: compliant` and `control_plane_upgrade_safe: true`; the first upgrade after bootstrap must be possible. Prefer a minor inside the three newest (take that from the fleet report, `skill://a21-cluster-ops--fleet-drift-report`, or the task).
3. Sizing check: call `k8s_nodepool_capacity` with `pools` and the initial system and tenant `workloads` (replicas and requests from the claim), `tolerated_node_failures` 1, `max_utilization_pct` 85. Control-plane add-ons (CoreDNS, CNI, metrics) belong in `daemonset_cpu_m` / `daemonset_memory_mib` or `workloads`. A baseline pool with `status: at_risk`, or a pool of 1 node holding system workloads, fails the baseline.
4. Tenancy check: for each namespace in the claim call `k8s_quota_fit` with `replicas`, `containers`, `quota_hard`, `quota_used` and `limit_range`. `rejected` (defaults or minimum/maximum violate the LimitRange) and `over_quota` fail the claim; the namespace must carry both a ResourceQuota and a LimitRange with default requests, or pods without requests are unschedulable-by-quota.
5. Manifest check: run `python3 scripts/k8s_manifest_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest-dir> --target-version <planned version> --json` on the baseline manifests. Exit 1 (`status: fail`) means a removed API, a workload without requests, or an input not analysed (a `scan-incomplete` major, including YAML unread without PyYAML): blocks. Never call the directory clean while `skipped` or a `scan-incomplete` finding names an input.
6. Upgrade readiness: call `k8s_maintenance_window` with `proposed_start`, `duration_minutes` (the first planned upgrade length, 180 unless the task says otherwise) and the declared `windows` and `blackouts`. No declared window at all means the cluster has no lifecycle contract: BLOCKED needs=input.
7. Cross-check where tools overlap: the pools sized in step 3 must be the pools in the IaC change (names and `nodes`), and the number of `apiserver_versions` entries sent to `k8s_version_skew` must equal the control-plane instance count in both the architecture and the IaC (an odd number, 3 for a highly available production control plane, when etcd is self-managed). A disagreement between the architecture and the IaC is E-CONTRACT, report both.
8. Write the baseline verdict with `write` to `cluster-reports/<cluster>/bootstrap-baseline.md`: each check, tool, verdict, findings. Production: call `shared_escalate` with `{task_id, reason_code: "policy", summary: "prod cluster bootstrap awaiting L3 approval", needs: ["human-approval"]}`. Digest the file with `shared_artifact_digest`.

## Outputs

- `cluster.fleet.status` entry for the new cluster: baseline verdict per check (version, sizing, tenancy, manifests, window), blockers, file digest.

## Failure Handling

- INVALID_INPUT from any tool: fix the named `field` (for example unknown pool in `workloads`, request above limit) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; the baseline is `partial`, not passed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Script exit 2 / E-INPUT: fix `--path` or `--target-version`.
- Provider-specific control-plane sizing and managed add-on defaults are not in these tools; defer to the provider's documentation named in the architecture.
- L3/L4: production creation BLOCKED needs=human-approval; cluster deletion never.

## Handover

`to_agent` A24 (policy baseline next; A13 reads the status). `goal` (apply the policy baseline to the new cluster), `completed`, `decisions`, `artifacts` (verdict file, digest, producer A21), `blockers`, `open_questions`, `constraints` (production pending approval). Call `shared_handover_validate`, then `shared_handover_write`.
