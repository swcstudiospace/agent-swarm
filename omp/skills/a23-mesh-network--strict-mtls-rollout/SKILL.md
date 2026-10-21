---
name: "a23-mesh-network--strict-mtls-rollout"
description: "Use when a mesh.policy task moves a namespace or the mesh from PERMISSIVE to STRICT mTLS and needs coverage proof, client checks and an L3 flip plan."
disable-model-invocation: true
---

# Strict mTLS rollout

## When to Use

- A `mesh.policy` task asks for `PeerAuthentication` mode `STRICT` on one namespace, a workload set or the whole mesh.
- Designing the policy is L2 (`policy_design`). Applying the flip on a production cluster is L3 (`mtls_strict_flip`): this skill never applies it.
- Not for authorization rules (`skill://a23-mesh-network--authz-default-deny`) nor for cluster-wide admission/RBAC (A24).

## Procedure

1. Run `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir from gitops.manifests> --min-strict-pct 100 --json`. Exit 0 = ok, exit 1 = fail (a major or blocker finding, or an incomplete scan: `scan/incomplete`), exit 2 = taxonomy error. Read the `mtls-coverage` entry of `checks`: `skipped:no-input` means the tree has no Istio security resource, so there is nothing to flip: BLOCKED needs=input.
2. Build the live inputs with read-only commands (`kubectl --context <ctx> -n <ns> get peerauthentication,pods -o json`; always name context and namespace). Call `mesh_mtls_coverage` with `peer_authentications` (all PeerAuthentication manifests, mesh-wide ones from `root_namespace` included), `workloads` (`[{namespace, name, labels}]`, one per pod template) and `min_strict_pct` 100. Keep `strict_pct`, `permissive_count`, `disabled_count`, `port_exception_count`, `non_strict_workloads`; `strict_pct` has 2 decimals, while the finding text shows the share rounded down to 4, so quote each as printed.
3. Cross-check against step 1: `workload_count` here must equal `counts.workloads` of the script when both read the same tree. A difference is E-CONTRACT (live pods vs committed manifests): report both, decide only on the live set.
4. Decide the blockers. `MTLS-DISABLED` (mode DISABLE) and `MTLS-PORT-EXCEPTION` with a DISABLE port are major: each needs an owner and a reason, or is removed before the flip. `MTLS-PERMISSIVE` is the thing being fixed (minor). `MTLS-CONFLICT` (major): same-level policies tie on `creationTimestamp` or are undated, with different modes or port rules; the effective one is unknown, so the workload is not counted strict. Give them distinct timestamps or remove the duplicates before proposing anything.
   Take the ids from the `mesh_mtls_coverage` findings and name an owner for each exception.
5. Check clients. Call `mesh_destination_policy` with `destination_rules` and `workloads` (`[{namespace, service, labels}]`). Any `DR-TLS-DISABLE` that targets the namespace to flip breaks under STRICT: change it to `ISTIO_MUTUAL` or remove it. From the pod list, name every pod without an `istio-proxy` container (and, for ambient, without `istio.io/dataplane-mode: ambient`): its calls into a STRICT workload are rejected, so it needs injection or a port-level exception first.
6. Propose the new policy in scope order mesh < namespace < workload (workload policies win, then namespace, then the root-namespace one). Re-run `mesh_mtls_coverage` with the proposed `PeerAuthentication` added: `strict_pct` must reach the stage target (100 for the target namespace), `permissive_count` of that namespace must be 0, and `non_strict_workloads` may only list workloads outside the scope. No improvement means the policy is shadowed by an older workload-level one.
7. Require a plaintext gate: the share of `istio_requests_total` (destination reporter; the source reporter shows `unknown`) with `connection_security_policy` other than `mutual_tls` towards the target namespace must be 0 over one full traffic cycle (at least 7 days). No such evidence supplied: BLOCKED needs=input; never assume it. Call `mesh_authz_lint` with the namespace's `policies` as a side check: `AUTHZ-ALLOW-ALL` blocker there must be known before STRICT hides plaintext callers.
8. Write the flip plan with `write` to `mesh-reports/<cluster>/mtls-<namespace>.md`: stage, manifest diff, verification query, rollback (revert the commit so the mode returns to PERMISSIVE; deleting the resource is L4). For production call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod STRICT mTLS flip awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as BLOCKED. Call `shared_artifact_digest` on the plan and on the proposed manifests.

## Outputs

- `mesh.policy.set` (strict-mTLS variant): proposed PeerAuthentication manifests, before/after `strict_pct`, client findings, plan with digest; BLOCKED needs=human-approval on production.

## Failure Handling

- INVALID_INPUT (`workloads` empty, unknown `mode`, selector key other than `matchLabels`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked workloads count as non-strict. DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` and decide nothing on that facet.
- Script `skipped:tool-missing` (PyYAML absent): YAML was not read, so the scan is incomplete (`scan/incomplete`, status fail) until JSON-rendered manifests are supplied.
- Never retry an apply; the flip is a human L3 step. Ambient-mode and sidecar-less pods are not modelled by the tools: list them as risks.

## Handover

- `to_agent`: A22 (delivers the manifests); A24 is informed for the policy audit trail. `goal`: the stage and scope to flip. `completed`: checks run. `decisions`: stage target, exceptions kept with owners. `artifacts`: manifests and plan with `shared_artifact_digest` digests. `open_questions`: missing plaintext evidence, sidecar-less pods. `verification`: verified only when steps 2 and 3 agree.
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/strict-mtls-rollout.md; edit the source and run scripts/build_agents.py -->
