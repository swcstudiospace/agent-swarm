---
name: a21-cluster-ops--cluster-upgrade-plan
description: Use when a cluster.upgrade task needs an ordered upgrade plan for a target version; check skew, removed APIs, node-pool batches and the maintenance window.
owner: a21-cluster-ops
tools: [k8s_version_skew, k8s_deprecated_api_scan, k8s_upgrade_plan, k8s_maintenance_window, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Cluster upgrade plan

## When to Use

- A task asks for a `cluster.upgrade.plan`: move one named cluster (context given) from `current_version` to a `target_version`.
- Planning only. Applying the upgrade on a production cluster is L3 (guarded, human approval); a non-prod cluster is L2. Add-on ranges go to `skill://a21-cluster-ops--addon-upgrade-matrix`, removed APIs in repo manifests to `skill://a21-cluster-ops--deprecated-api-remediation`, rollout of the workloads to A22.

## Procedure

1. Collect facts read-only: `kubectl --context <ctx> get nodes -o json` (kubelet versions per node), the apiserver versions and the node-pool settings from the task or the `iac.change` of A11. Production access is by the pipeline identity only. No explicit context: BLOCKED needs=input.
2. Call `k8s_version_skew` with `apiserver_versions` (one per control-plane instance) and `components` (`{name, kind, version, node}` for every kubelet, kube-proxy, controller-manager, scheduler, kubectl). `verdict: violations` or any `blocker` finding stops the plan: fix the skew first. Keep `control_plane_upgrade_safe`: false means a kubelet or kube-proxy would break the 3-minor limit (2 below 1.25) after the next hop, so those pools must move first.
3. Call `k8s_deprecated_api_scan` with `target_version` and `resources` (`{api_version, kind, name}` of every live object, from `kubectl get --raw /metrics` gauge `apiserver_requested_deprecated_apis` and the manifests) and `horizon_minors: 1`. `verdict: blocked` (`removed_count` above 0) blocks the upgrade; `attention` is carried as pre-work.
4. Call `k8s_upgrade_plan` with `current_version`, `target_version` and `node_pools` (`{name, version, nodes, max_surge, max_unavailable, pdb_min_disruptions_allowed}`). Keep `steps` in `order`. `control_plane_hops` must equal the minor distance (no minor is skipped; 1 for a patch-only upgrade). `verdict: blocked` (a `major` finding such as a PDB allowing 0 disruptions or `max_unavailable` at or above `nodes`) is a blocker; send the PDB question to `skill://a21-cluster-ops--drain-and-cordon-plan`.
5. Cross-check: for every `node_pool` step the plan's `target_version` minor must be within the skew limit of the control plane at that moment; re-run `k8s_version_skew` on the version set after each `control_plane` step and confirm `max_kubelet_lag` stays at 3 or below (2 for a kubelet below 1.25). A mismatch between the two tools is E-CONTRACT: report both values, state no verdict.
6. Call `k8s_maintenance_window` with `proposed_start`, `duration_minutes` (the sum of hops, verify steps and batches the task allows) and the `windows` and `blackouts` from the task. `allowed: false` means use `earliest_compliant_start`; no calendar supplied is BLOCKED needs=input, never "clear".
7. Write the plan file with `write` (task path, else `cluster-reports/<cluster>/upgrade-plan.md`): ordered steps, rollback point (etcd snapshot before the first hop; control-plane minors cannot be downgraded, so the rollback is restore-from-snapshot), the window, every finding id with its severity; list `blocker` and `major` findings first.
8. Autonomy: a production apply gets `shared_escalate` with `{task_id, reason_code: "policy", summary: "prod cluster upgrade awaiting L3 approval", needs: ["human-approval"]}`; a non-prod apply is L2 after the plan is reviewed. Cluster deletion or rebuild-as-upgrade is L4. Call `shared_artifact_digest` on the plan file.

## Outputs

- `cluster.upgrade.plan` (ordered steps, hops, batches, window, findings with ids) and the digest of the plan file.

## Failure Handling

- INVALID_INPUT from any tool: fix the named `field` (for example `target_version` older than current, or more than 6 minors ahead) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the missing checks, never mark the plan `ready`.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` and BLOCKED for that gate.
- The removal table is curated, not complete: say so, and treat a `clear` as "no known removal", not proof.
- L3/L4 steps: BLOCKED needs=human-approval; never run `kubeadm upgrade` or a cloud upgrade call yourself.

## Handover

`to_agent` A12 (release plans the rollout; A14 and A22 read the same artifact). Fill `goal` (approve and run the upgrade), `completed`, `decisions` (hops, pool order, window), `artifacts` (plan file, digest, producer A21), `open_questions`, `verification` (`partial` until every cross-check agreed). Call `shared_handover_validate`, then `shared_handover_write`.
