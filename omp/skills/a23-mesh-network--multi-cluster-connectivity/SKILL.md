---
name: "a23-mesh-network--multi-cluster-connectivity"
description: "Use when clusters must join one mesh or routed network; prove pod, service and node CIDRs do not collide and choose gateway or routed connectivity."
disable-model-invocation: true
---

# Multi-cluster connectivity

## When to Use

- A `mesh.policy` task, a `cloud.architecture` from A16 or a capacity change from A21 adds a cluster to a mesh, peers VPCs or enables pod-to-pod routing between clusters.
- Design is L2. Creating peering, gateways or trust on production is L3 and a human step; cluster CIDR assignment itself belongs to A21, not this skill.
- Not for single-cluster policy (`skill://a23-mesh-network--strict-mtls-rollout`).

## Procedure

1. Collect the CIDR facts from `cluster.capacity.report` (A21) and `cloud.architecture` (A16): per cluster `name`, `pod_cidrs`, `service_cidrs`, `node_cidrs`, each as a CIDR with host bits zero (`10.0.0.0/16`, not `10.0.0.5/16`). A cluster missing from those artifacts: BLOCKED needs=input for A21; never read CIDRs from memory.
2. Choose `connectivity`: `gateway` when clusters are on different networks and traffic crosses east-west gateways (Istio TLS passthrough on port 15443, endpoints reached via the gateway address); `routed` when pod IPs are routed directly (flat VPC peering, Cilium Cluster Mesh, Submariner). Without a decision from the blueprint, evaluate both in the next step and recommend `gateway` as the safer default.
   Run `mesh_cidr_overlap` for both values in step 3.
3. Call `mesh_cidr_overlap` with `clusters` (`[{name, pod_cidrs, service_cidrs, node_cidrs}]`) and `connectivity`. Keep `overlap_count`, `verdict` and the findings (`CIDR-SAME-*` inside one cluster, `CIDR-CROSS-*` between clusters). Severities: pod/service overlap inside a cluster is a blocker; under `routed`, pod/pod, node/node, node/pod overlaps across clusters are blockers, service-vs-pod or node major, service/service minor; under `gateway` only node/node is major and the rest is info.
4. Cross-check by running the tool twice, `routed` and `gateway`: the `gateway` verdict must never be worse than `routed` for the same input. If it is, or `overlap_count` differs, E-CONTRACT: stop and report both.
   Both runs use `mesh_cidr_overlap` with identical `clusters`.
5. Decide: any blocker stops the plan (renumbering a live cluster is A21's L3/L4 change, never part of this skill); majors need an explicit accepted-risk note from the owner; info findings are recorded as future routed-networking constraints.
   Record each accepted major in the plan with `write`.
6. Trust and identity: state the shared root of trust (one root CA with per-cluster intermediates, or federated trust domains) and the mesh network label per cluster (`topology.istio.io/network`). Then call `mesh_mtls_coverage` with each cluster's `peer_authentications` and `workloads`: `strict_pct` must be 100 in both clusters before cross-cluster calls are enabled, because the east-west gateway path is mTLS only.
7. Call `mesh_authz_lint` with the AuthorizationPolicies of both clusters: principals named in policies must include the remote trust domain where cross-cluster callers exist (`<trust-domain>/ns/<ns>/sa/<sa>`); `namespaces_without_default_deny` must be empty before opening a path.
8. Write the connectivity plan to `mesh-reports/multi-cluster.md` with `write`: chosen mode, CIDR table, gateway addresses and ports (no credentials), trust model, steps and rollback (remove the remote secret/peering; deleting is L4), then `shared_artifact_digest`. Any production peering or trust change: `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod multi-cluster peering awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `mesh.policy.set` (multi-cluster variant): connectivity mode, overlap verdict, trust and authz prerequisites, plan with digest.

## Failure Handling

- INVALID_INPUT (a CIDR with host bits set, duplicate cluster `name`, `connectivity` not routed|gateway): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; clusters not analysed are not cleared. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- IPv6 and IPv4 ranges are compared only within the same family; a dual-stack overlap across families is not detected: say so.
- Which Istio versions support a given multi-cluster topology, and Cilium/Submariner specifics, are not checked by the tools: read the vendor release notes, and say it is unverified otherwise.

## Handover

- `to_agent`: A21 (CIDR and capacity owner) and A22 (manifests). `goal`: clusters and mode. `completed`, `decisions` (mode, accepted majors), `artifacts` (plan, digest, producer A23), `open_questions`, `verification` (both modes evaluated).
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/multi-cluster-connectivity.md; edit the source and run scripts/build_agents.py -->
