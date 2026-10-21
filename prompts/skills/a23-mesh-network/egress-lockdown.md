---
name: a23-mesh-network--egress-lockdown
description: Use when outbound traffic must move from ALLOW_ANY to REGISTRY_ONLY with ServiceEntries; match observed destinations and plan the L3 flip.
owner: a23-mesh-network
tools: [mesh_egress_coverage, mesh_netpol_coverage, mesh_dns_ndots, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Egress lockdown

## When to Use

- A task asks to stop workloads from reaching arbitrary external hosts: set `outboundTrafficPolicy.mode: REGISTRY_ONLY` and register each allowed destination as a `ServiceEntry`.
- Allow-list design is L2. Flipping the mode on a production mesh is L3 (`prod_mesh_change`); removing a ServiceEntry that is in use is L4.
- Not for in-cluster pod-to-pod isolation (`skill://a23-mesh-network--netpol-default-deny-rollout`).

## Procedure

1. Observe first, with the mode still `ALLOW_ANY`: read destinations that left the mesh from telemetry (Istio standard metrics with `destination_service_name` `PassthroughCluster` for unregistered traffic, `BlackHoleCluster` once REGISTRY_ONLY drops it) and access logs, over at least 14 days or two business cycles. Build `destinations` as `[{host, port, protocol}]` (protocol TCP, HTTP, HTTPS, TLS). No telemetry supplied: BLOCKED needs=input; a guessed list means a production outage.
2. Draft one `ServiceEntry` per dependency: exact `hosts`, the real `ports` with the right `protocol` (HTTP port 80, TLS or HTTPS port 443), `resolution: DNS` (never `NONE` on a TCP port without `addresses`: Istio then allows any IP on that port), `location: MESH_EXTERNAL`, and an `exportTo` limited to the owning namespace. Wildcards only for a documented need (a SaaS with per-tenant subdomains), never `*` or a top-level-domain wildcard.
   Write the drafts with `write` under the manifest directory the task names.
3. Call `mesh_egress_coverage` with `destinations`, `service_entries` and `outbound_policy` `REGISTRY_ONLY`. Keep `uncovered` (each is a destination that will be dropped), `unused_entries` and the findings. Blockers: `EGRESS-BLOCKED` (major, an uncovered destination), `EGRESS-WILDCARD-ALL` and `EGRESS-WILDCARD-TLD` (major), `EGRESS-PROTOCOL-MISMATCH` (major, a TLS destination declared on an HTTP port or the reverse), `SE-WILDCARD-DNS` (major, wildcard with DNS resolution and no `endpoints`), `SE-TCP-NONE-OPEN` (major, resolution NONE on a TCP port without `addresses`), `SE-RESOLUTION-INVALID`. `EGRESS-UNUSED-ENTRY` is info: remove only after human L4 approval.
4. Run the same call with `outbound_policy` `ALLOW_ANY`: it must show the same `covered_count`, only with `EGRESS-UNREGISTERED` (minor) instead of `EGRESS-BLOCKED`. A different `covered_count` between the two runs is E-CONTRACT.
   Both runs use `mesh_egress_coverage` with identical `destinations` and `service_entries`.
5. Layer the network side: with the pods and policies, call `mesh_netpol_coverage` with `require_egress` true. A mesh `REGISTRY_ONLY` only restrains sidecar-proxied traffic; a pod can bypass its sidecar, so the namespace also needs an egress default-deny (`uncovered_namespaces` empty) or an egress gateway with a NetworkPolicy that only allows traffic to that gateway. State which one the design relies on.
6. Resolve names correctly: call `mesh_dns_ndots` with the pod's `ndots`, `namespace`, `search_domains` and `names` (`{name, target: "external"}`) for the registered hosts; a ServiceEntry host must be the name the application actually resolves, with the lookups in `total_lookups` unaffected by the allow-list. Large `wasted_lookups` is a hint for `skill://a23-mesh-network--dns-tuning`.
7. Stage: namespace by namespace, or first `ALLOW_ANY` plus alerts on `PassthroughCluster` traffic, then `REGISTRY_ONLY` per mesh. Rollback is setting the mode back to `ALLOW_ANY` (committed revert). Write the plan to `mesh-reports/<cluster>/egress-allowlist.md` with `write`, digest it and the ServiceEntries with `shared_artifact_digest`.
8. Production flip is L3: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod REGISTRY_ONLY flip awaiting L3 approval", "needs": ["human-approval"]}` and return `result_patch` as BLOCKED.

## Outputs

- `mesh.policy.set` (egress variant): ServiceEntries, `uncovered` list (must be empty to flip), network-layer plan, rollback.

## Failure Handling

- INVALID_INPUT (port outside 1..65535, `spec.hosts` empty, `outbound_policy` not REGISTRY_ONLY or ALLOW_ANY): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unmatched destinations stay `uncovered`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- IP-based and non-DNS destinations, SNI-only TLS routing and egress gateways are modelled only by host and port: defer to the tool and flag the rest as risk.
- Never retry the flip; a rejected destination found after the flip means rollback under approval.

## Handover

- `to_agent`: A22 (manifests) and A24 (policy audit). `goal`: mesh and namespaces. `completed`, `decisions` (wildcards kept and why), `artifacts` (ServiceEntries, plan, digests), `open_questions` (telemetry gaps), `verification` (both policy modes agree).
- Tools: `shared_handover_validate` then `shared_handover_write`.
