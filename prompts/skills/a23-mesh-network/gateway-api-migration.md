---
name: a23-mesh-network--gateway-api-migration
description: Use when Ingress or VirtualService edge routes move to Gateway API and Gateways, HTTPRoutes, attachment and ReferenceGrants must be proven valid before cutover.
owner: a23-mesh-network
tools: [mesh_gateway_api_check, mesh_ingress_tls, mesh_netpol_reachability, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Gateway API migration

## When to Use

- A task moves north-south routing from `Ingress` (or Istio Gateway/VirtualService) to `gateway.networking.k8s.io` `Gateway` and `HTTPRoute`.
- Writing the manifests is L2; DNS or traffic cutover on production is L3 and never done here.
- Not for weighted version shifts (`skill://a23-mesh-network--traffic-shifting-plan`) nor TLS hygiene alone (`skill://a23-mesh-network--ingress-tls-hardening`).

## Procedure

1. Inventory the source: `grep`/`read` the Ingress manifests and their controller annotations. Every annotation is a behaviour the HTTPRoute must reproduce or the plan must declare dropped (rewrite, timeouts, body size, auth). The `ingress2gateway` tool (kubernetes-sigs) can draft the first conversion; treat its output as a draft and keep its warnings in the plan. Pick the `gatewayClassName` from the installed classes (`kubectl --context <ctx> get gatewayclass`), never a guessed name.
2. Draft `Gateway` objects (one listener per hostname and port, `allowedRoutes.namespaces.from: Selector` or `Same`, never `All` for shared edge) and one `HTTPRoute` per application namespace with `parentRefs` including `sectionName`. Cross-namespace backends and certificates need a `ReferenceGrant` in the target namespace.
   Write the drafts with `write`, one file per Gateway and one per namespace route set.
3. Run `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir> --json` and read the `gateway-references` check. Exit 1 = major/blocker present.
4. Call `mesh_gateway_api_check` with `gateways`, `routes`, `reference_grants` and `namespaces` (`[{name, labels}]`, needed for Selector scopes). Blocking findings (major): `GW-NO-CLASS`, `GW-LISTENER-PORT-CONFLICT`, `GW-LISTENER-TLS-MISSING`, `GW-LISTENER-TLS-UNUSED` (tls set on an HTTP, TCP or UDP listener), `GW-CERT-NO-GRANT`, `GW-ROUTES-SCOPE`, `ROUTE-PARENT-MISSING`, `ROUTE-SECTION-MISSING`, `ROUTE-NOT-ACCEPTED` (an HTTPRoute attaches only to HTTP and HTTPS listeners; a TLS listener takes TLSRoute), `ROUTE-BACKEND-NO-GRANT`, `ROUTE-BACKEND-NO-PORT`. Keep `unattached_routes` (must be empty) and the info findings `GRANT-UNUSED` and `GW-ROUTES-ALL-NAMESPACES` as cleanup items.
5. Cross-check: the script's gateway counts and finding ids must equal the tool's for the same manifests; a gap is E-CONTRACT. Also confirm each `ReferenceGrant` is the narrowest one (named `to.name`, one `from.namespace`).
   Compare finding ids one by one against the `mesh_gateway_api_check` output.
6. TLS on the new listeners: map every HTTPS listener to `listeners` for `mesh_ingress_tls` (`name`, `hostnames`, `terminates_tls`, `min_tls_version`, `certificate_name`, `certificate_sans`, `redirect_http_to_https`) and require no major. Gateway API has no HSTS field: if the old Ingress added HSTS, add it with a `ResponseHeaderModifier` filter and say so.
7. Check the data path: with the cluster pods and NetworkPolicies, call `mesh_netpol_reachability` with `source` = the gateway pod (`{namespace, name}`), `destination` = a backend pod and its `port`. `reachable: false` means the gateway namespace needs an ingress allow in the application namespace before cutover.
8. Plan a parallel run (both stacks live, new Gateway on its own address), a verification request set per host and path, and the cutover as a DNS or load-balancer weight change with rollback to the old address. Write `mesh-reports/<cluster>/gateway-migration.md` with `write`, digest it and the manifests with `shared_artifact_digest`. Production cutover: `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod edge cutover awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `mesh.policy.set` (Gateway API variant): Gateways, HTTPRoutes, ReferenceGrants, dropped-annotation list, cutover and rollback plan with digests.

## Failure Handling

- INVALID_INPUT (route without `parentRefs`, namespace list lacking `name`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked routes are not migrated. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Which `apiVersion` (v1 vs v1beta1 for ReferenceGrant) and which features (timeouts, retries, GRPCRoute) are supported depends on the installed CRD bundle and the implementation: read them from the cluster and defer to the tool verdict; say it is unverified otherwise.
- Never apply or retry a cutover; DNS changes are human L3.

## Handover

- `to_agent`: A22 (manifests) and A24 for policy audit. `goal`: routes migrated and the cutover condition. `completed`, `decisions` (dropped annotations), `artifacts` (manifests, plan, digests), `open_questions`, `verification` (script and tool agree).
- Tools: `shared_handover_validate` then `shared_handover_write`.
