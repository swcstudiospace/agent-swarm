---
name: "a23-mesh-network--mesh-upgrade-canary"
description: "Use when the service mesh control plane or data plane must be upgraded; plan a revision-based canary with policy before/after diff, headroom check and rollback."
disable-model-invocation: true
---

# Mesh upgrade canary

## When to Use

- A `mesh.policy` task, a release note or A21's `cluster-upgrade-plan` calls for moving Istio (or the same pattern for another mesh) to a new minor or patch version.
- Planning is L2. Installing a new control-plane revision or relabelling production namespaces is L3 (`prod_mesh_change`); removing the old revision is L4.
- Not for Kubernetes version upgrades (A21 `cluster-upgrade-plan`) nor for application rollouts (A12).

## Procedure

1. Pin the facts: current and target mesh version, install method (Helm or `istioctl`), the Kubernetes versions each supports (from the vendor support table: read it, state the source, never recall it), and the injection labels in use (`istio-injection=enabled` or `istio.io/rev=<revision>`). Take the Kubernetes version and node headroom from the `cluster.capacity.report` (A21) and the manifests from `gitops.manifests` (A22). A target that skips a minor version the vendor does not allow is BLOCKED needs=input.
2. Baseline before touching anything: run `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir> --json` and save the result as `before`. Then call `mesh_mtls_coverage` (`peer_authentications`, `workloads`) and `mesh_authz_lint` (`policies`, `root_namespace`) and keep `strict_pct`, `permissive_count`, `namespaces_without_default_deny` as the baseline numbers. Any existing blocker is fixed or accepted by the owner first; an upgrade must not mask it.
3. Read the target version's release and upgrade notes for removed or changed fields (deprecated API versions, default changes in `meshConfig`, `PeerAuthentication` or telemetry behaviour). For every change, `grep` the repository manifests; a removed field in use is a finding that blocks. Anything you cannot verify from the notes is listed as unverified, not assumed safe.
4. Plan the canary revision: install the new control plane beside the old one with `--revision <new>` (for example `1-24-0`, no in-place upgrade), run a precheck/`istioctl analyze` per namespace (read-only), then move a low-risk namespace first by relabelling `istio.io/rev=<new>` and restarting its workloads. Order: dev, staging, a non-critical production namespace, then critical ones, with at least one full traffic cycle between stages. The old control plane keeps serving the others during all stages.
5. After each stage, re-run the three baseline tools on the manifests as they will stand: `mesh_mtls_coverage` `strict_pct` must not drop below baseline, `mesh_authz_lint` finding count must not rise, and `mesh_traffic_split` on the `virtual_services` and `destination_rules` of the moved namespace must stay `pass`. A regression stops the next stage; compare against the `before` script output field by field.
6. Capacity: two control planes and restarted workloads need headroom: take control-plane replicas and requests, and the sidecar restart wave size, from `cluster.capacity.report`. Restart in waves of at most 10 percent of the pods per namespace (this skill's default), respecting PDBs; no capacity data is BLOCKED needs=input for A21.
   Take the pod counts per namespace from the `workloads` input of `mesh_mtls_coverage` to size the waves.
7. Define the stop and rollback rules: abort when 5xx rate towards the moved namespace rises more than 1 percentage point over baseline for 10 minutes or any new `403` appears in the mesh authorisation metrics. Rollback is relabelling the namespace to the old revision and restarting its workloads. Removing the old revision comes only after all namespaces have run one release cycle on the new one, and is L4.
   Record the thresholds in the runbook with `write`.
8. Write the runbook (stages, commands as text, checks, rollback) to `mesh-reports/<cluster>/mesh-upgrade-<target>.md` with `write`; digest it and the `before` result with `shared_artifact_digest`. Production: `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod mesh revision rollout awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `mesh.policy.set` (upgrade variant): staged runbook, before/after baselines, breaking-change list, rollback and removal conditions.

## Failure Handling

- INVALID_INPUT from any tool (manifest not an object, `workloads` empty): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; the stage is not cleared on unchecked data. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tools check policy objects, not the proxy version, xDS health or CNI/ambient behaviour: those are listed as unverified and need the operator's checks.
- Installs, relabels and restarts are never retried by this agent; they are human L3 steps, deletion is L4.

## Handover

- `to_agent`: A22 (manifests) and A21 (cluster owner informed of waves). `goal`: versions and stages. `completed`, `decisions` (wave size, stop rules), `artifacts` (runbook, baseline, digests), `open_questions` (unverified notes), `verification` (baseline numbers).
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/mesh-upgrade-canary.md; edit the source and run scripts/build_agents.py -->
