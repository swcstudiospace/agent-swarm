---
name: "a24-cluster-security--tenant-isolation-audit"
description: "Use when tenant namespaces in a shared cluster must be audited for PSA labels, quotas, default-deny NetworkPolicy and cross-tenant RBAC."
disable-model-invocation: true
---

# Tenant isolation audit

## When to Use

- A multi-tenant cluster (namespace per tenant) is onboarding a tenant, changing its quotas or policies, or is audited periodically; inputs are `gitops.manifests` (A22) and `mesh.policy.set` (A23).
- Read-only audit (L2). Single-namespace PSA work is `skill://a24-cluster-security--pss-enforce-rollout`; RBAC-only review is `skill://a24-cluster-security--rbac-least-privilege-review`. Namespaces are not hard isolation: say so for hostile tenants (shared kernel, nodes and control plane).

## Procedure

1. Take the explicit context and tenant namespace list from the task (up to 100). Collect read-only with `bash`: `kubectl --context <ctx> get ns,resourcequota,limitrange,networkpolicy,rolebinding -A -o json`, or the manifest files. No tenant list: BLOCKED needs=input.
2. Call `kpol_tenant_isolation_check` with `tenant_namespaces`, `manifests` (Namespace, ResourceQuota, LimitRange, NetworkPolicy, RoleBinding; at most 1000) and `required_psa_level` (`restricted` unless the task says `baseline`). Read `tenant_count`, `isolated_count`, `finding_count`, `verdict`. A tenant is isolated only without a major or blocker finding.
3. Map `kpol_tenant_isolation_check` ids to severity: `tenant.psa-enforce-missing`, `tenant.psa-enforce-invalid`, `tenant.psa-enforce-weak`, `tenant.quota-missing`, `tenant.quota-incomplete`, `tenant.ingress-default-deny-missing`, `tenant.netpol-allow-all-ingress`, `tenant.netpol-all-namespaces`, `tenant.cross-tenant-binding`, `tenant.namespace-missing` are major; `tenant.egress-default-deny-missing`, `tenant.limitrange-missing`, `tenant.quota-no-pod-count` minor; `tenant.psa-version-unpinned` and `tenant.psa-warn-audit-missing` info. `tenant_count - isolated_count` must equal the number of distinct tenants with a major finding; a mismatch is E-CONTRACT.
4. For each tenant, call `kpol_rbac_effective_access` with `roles`, `bindings`, `subject` `{kind: "ServiceAccount", name: "default", namespace: "<tenant A>"}` and `checks` against another tenant (`{verb: "get", resource: "secrets", namespace: "<tenant B>"}`, `{verb: "list", resource: "pods", namespace: "<tenant B>"}`). Every `allowed: true` is a cross-tenant finding; it must also appear as `tenant.cross-tenant-binding` unless it comes from a ClusterRoleBinding or a group the tool does not judge (for example `system:authenticated`), in which case report the gap.
5. Verify workload posture inside each tenant: call `kpol_pss_check` with that tenant's `workloads` and `level` equal to `required_psa_level`; `highest_compliant_level` below it with an enforce label present means pods already violate or will fail to restart. An absent `highest_compliant_level` means the tenant had no pod spec to check: it is unchecked, not compliant.
6. NetworkPolicy is only an API object: it is enforced only by a CNI that implements it. State the CNI from `cluster.fleet.status` or mark enforcement unproven. When the mesh policy set from A23 is present, check AuthorizationPolicy and PeerAuthentication namespaces with `grep` for cross-tenant principals and list them as findings for A23; do not edit mesh policy.
7. Write the per-tenant table (isolated yes/no, failing ids, owner) to `cluster-reports/<cluster>/tenants.md` with `write` and digest it with `shared_artifact_digest`. Proposed manifests (default-deny, quota, labels) are diffs for A22; applying them is L3 in production.

## Outputs

- `cluster.security.findings` (tenant): per-tenant isolation status, ids, severities, evidence from both tools.
- `cluster.policy.set` (tenant baseline): proposed namespace labels, quota, LimitRange and default-deny diffs.

## Failure Handling

- INVALID_INPUT (`required_psa_level` outside baseline or restricted, more than 100 namespaces or 1000 manifests): fix the named field or split the audit; else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; the unchecked tenants are not reported isolated. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Node, storage, DNS and kernel sharing are not modelled. Cluster-scoped objects a tenant can create (CRDs, webhooks) are out of scope: list them as unchecked.
- Never apply or delete anything; L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A10 (findings). A22 receives the proposed diffs and A23 the mesh gaps in `open_questions`. Fill `goal`, `completed`, `decisions`, `artifacts` with digests, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/tenant-isolation-audit.md; edit the source and run scripts/build_agents.py -->
