---
name: "a21-cluster-ops--drain-and-cordon-plan"
description: "Use when named nodes must be cordoned and drained (maintenance, OS patch, pool replacement) and a PDB-aware wave plan with remaining capacity is needed."
disable-model-invocation: true
---

# Drain and cordon plan

## When to Use

- A node set (a pool, a zone, named nodes) leaves service for kernel or OS patching, instance replacement, an upgrade batch or a pool shrink, and you must say whether evictions will succeed and what capacity remains.
- Plan and verification only. The drain itself is a production mutation: non-prod L2, production L3 (human approval); nothing here runs `kubectl cordon` or `kubectl drain` against a production context. Deleting the nodes or the pool afterwards is L4 when it destroys data. Choosing the pool size is `skill://a21-cluster-ops--node-pool-resize`.

## Procedure

1. Take the explicit context and node list from the task. Read-only: `kubectl --context <ctx> get pods -A -o json --field-selector spec.nodeName=<node>` per node (or all pods and filter), `kubectl get pdb -A -o json`, and `kubectl get pv -o json` to spot local or zonal volumes. No explicit context or node list: BLOCKED needs=input.
2. Count `pods_on_drained_nodes` per PDB (pods matched by the PDB selector sitting on the drained nodes; DaemonSet pods and mirror pods are not evicted and are not counted). Call `k8s_drain_feasibility` with `pdbs` (`{name, namespace, expected_pods, current_healthy, pods_on_drained_nodes}` plus EXACTLY ONE of `min_available`, `min_available_percent`, `max_unavailable`, `max_unavailable_percent`).
3. Decide from the `k8s_drain_feasibility` `verdict`: `feasible` (every PDB `free`: its pods fit within `disruptions_allowed` in one wave); `throttled` (some PDB `throttled`: its `waves` = ceil(pods / allowed), and `max_waves` is the number of drain rounds; each wave needs the replacements Ready before the next); `blocked` (a PDB allows 0 disruptions with pods on the nodes, including one already below `expected_pods`; evictions return 429 and the drain hangs). A `blocked` verdict is never overridden with `--disable-eviction` or by deleting the PDB: the owner (A22) changes the PDB or scales the workload first. An `unsatisfiable` finding (`min_available` above `expected_pods`) is a PDB bug to fix.
4. Check what is left: call `k8s_nodepool_capacity` with the pools AFTER the drained nodes are removed (`nodes` reduced by the drain count, as if they were gone), `workloads` from the live requests, `tolerated_node_failures` 1. `at_risk` means the evicted pods cannot be rescheduled: shrink the batch to what `recommended_nodes` allows. Batch size per round is `max_surge + max_unavailable` as in `k8s_upgrade_plan`; if the drain belongs to an upgrade, call `k8s_upgrade_plan` and use its `node_pool` step `batches`, and keep the drain rounds inside each batch.
5. Cross-check `k8s_drain_feasibility` against `k8s_upgrade_plan`: the `pods_on_drained_nodes` total over all PDBs must not exceed the pods you counted on the nodes in step 1; and a pool whose `pdb_min_disruptions_allowed` (upgrade plan input) is 0 must show a `blocked` PDB here. A disagreement between the two tools is E-CONTRACT: report both.
6. Call `k8s_maintenance_window` with `proposed_start`, `duration_minutes` (`max_waves` times the task's per-wave time, plus the node-return time it gives) and the task's windows and blackouts. `allowed: false` means use `earliest_compliant_start`.
7. Write the runbook with `write` to `cluster-reports/<cluster>/drain-plan.md`: order (cordon all batch nodes first, then drain with `--ignore-daemonsets`, a `--timeout`, and `--dry-run=server` first; `--delete-emptydir-data` only where the task confirms the emptyDir data is disposable), the waves, stop conditions (a PDB 429 longer than the timeout), uncordon steps. Digest it with `shared_artifact_digest`. Production: `shared_escalate` with `{task_id, reason_code: "policy", summary: "prod node drain awaiting L3 approval", needs: ["human-approval"]}`.

## Outputs

- `cluster.upgrade.plan` (drain variant): per-PDB status and waves, remaining capacity verdict, window, runbook with digest.

## Failure Handling

- INVALID_INPUT (`pdbs`: not exactly one selector, `current_healthy` above `expected_pods`, duplicate PDB): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; the unchecked PDBs count as `blocked` for the decision. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Pods with no PDB and a single replica, local storage and zonal volumes are not modelled by the tools: list them as risks from the cluster read, do not call them safe.
- Drain, eviction and uncordon commands are never retried by this agent; a human runs them under L3.

## Handover

`to_agent` A22 (owner of the PDBs and workloads; A12 and A14 read the plan). `goal` (change the blocking PDBs, then run the drain in the stated waves), `completed`, `decisions` (batch size, waves), `artifacts` (runbook, digest, producer A21), `blockers` (blocked PDB names, `human-approval`), `constraints` (window). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a21-cluster-ops/drain-and-cordon-plan.md; edit the source and run scripts/build_agents.py -->
