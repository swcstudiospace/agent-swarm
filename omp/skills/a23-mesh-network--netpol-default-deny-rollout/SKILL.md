---
name: "a23-mesh-network--netpol-default-deny-rollout"
description: "Use when namespaces lack NetworkPolicy default-deny and a staged ingress-then-egress rollout with DNS and flow proofs and a netpol.coverage.report is needed."
disable-model-invocation: true
---

# NetworkPolicy default-deny rollout

## When to Use

- A `mesh.netpol` task or an A10 finding reports namespaces where pods accept every connection (no default-deny) or where an allow-all rule re-opens them.
- The policy design is L2. Applying NetworkPolicies to a production cluster is L3 (`prod_mesh_change`); deleting one is L4.
- Not for mesh identity rules (`skill://a23-mesh-network--authz-default-deny`) and not for Pod Security or RBAC (A24).

## Procedure

1. Run `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir> --json` and read `netpol-default-deny`. Findings use `NETPOL-NO-DEFAULT-DENY-INGRESS|EGRESS`, `NETPOL-DEFAULT-DENY-OVERRIDDEN-*`, `NETPOL-ALLOW-ALL-*` and `NETPOL-ALLOW-ANY-CIDR-*` (minor: an `ipBlock` /0 rule without ports).
2. Gather live state read-only with explicit context and namespace: `kubectl --context <ctx> -n <ns> get networkpolicy,pods -o json` and `get ns -o json`. Check that the CNI enforces NetworkPolicy (Calico, Cilium, Antrea and others do; flannel alone does not): unknown enforcement is BLOCKED needs=input, because an unenforced policy is a false assurance.
3. Call `mesh_netpol_coverage` with `network_policies`, `pods` (`[{namespace, name, labels, ip?, ports?}]`) and `namespaces`, first with `require_egress` false, then true. Keep `uncovered_namespaces` and the per-namespace `namespaces` rows. Ingress without default-deny is major; egress without it is minor (major when `require_egress` true); `NETPOL-DEFAULT-DENY-OVERRIDDEN-*` is major (policies are additive, one allow-all re-opens every pod). Without `policyTypes`, Kubernetes isolates egress only when the `egress` list is non-empty: write `policyTypes: [Ingress, Egress]` explicitly on a default-deny.
4. Cross-check: the namespaces flagged by the script for the same manifests must equal `uncovered_namespaces`. A gap is E-CONTRACT (the script reads manifests, the tool read live objects).
5. Derive allows from the `architecture.blueprint` and observed flows (CNI flow logs or `istio_tcp_connections_opened_total` by source and destination). Always include egress to DNS (kube-dns on port 53 over UDP and TCP, selected by namespace label `kubernetes.io/metadata.name: kube-system`), and ingress from the gateway and monitoring namespaces where they are real callers. Node-to-pod kubelet probes and some host-network traffic are treated differently per CNI: defer to the CNI docs and test.
   Draft the manifests with `write`, one default-deny file per namespace and direction.
6. Stage per namespace: (a) ingress default-deny plus the allows, (b) egress default-deny plus the allows. Prove each critical flow with `mesh_netpol_reachability`: `source` and `destination` as `{namespace, name}`, `port`, `protocol` (TCP default). `reachable` must be true for every required flow and false for one deliberate negative test per namespace. Read `egress_isolated` and `ingress_isolated` to see which side blocks, and `reason`.
7. For egress, call `mesh_egress_coverage` when the namespace has external dependencies: pass `destinations` (`[{host, port, protocol}]`) and the ServiceEntries; a CIDR/FQDN allow in the NetworkPolicy and the ServiceEntry must describe the same destinations (standard NetworkPolicy has no FQDN match: that needs the CNI extension, or an egress gateway). Mismatch is E-CONTRACT.
8. Re-run `mesh_netpol_coverage` on the proposed set: `uncovered_namespaces` must be empty, no `NETPOL-DEFAULT-DENY-OVERRIDDEN-*`. Write the report to `mesh-reports/<cluster>/netpol-coverage.md` with `write` (per namespace before/after, flows proven, rollback = revert the commit), digest it with `shared_artifact_digest`. Production apply: `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod NetworkPolicy default-deny awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `netpol.coverage.report` (consumed by A10) and `mesh.policy.set` (policy manifests): coverage per namespace, flows proven, stage order.

## Failure Handling

- INVALID_INPUT (pod without `namespace`, unknown `protocol`, source pod not in `pods`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; namespaces not analysed are reported as uncovered. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- `mesh_netpol_reachability` does not model CNI extensions (CiliumNetworkPolicy, Calico GlobalNetworkPolicy, FQDN rules) or AdminNetworkPolicy: those are risks, never "covered".
- Never retry an apply; deleting a policy is L4.

## Handover

- `to_agent`: A10 (consumes `netpol.coverage.report`); A22 receives the manifests. `goal`: namespaces and stage. `completed`, `decisions`, `artifacts` (report, manifests, digests), `open_questions` (CNI enforcement, unobserved flows), `verification`.
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/netpol-default-deny-rollout.md; edit the source and run scripts/build_agents.py -->
