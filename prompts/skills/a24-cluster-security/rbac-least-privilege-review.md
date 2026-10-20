---
name: a24-cluster-security--rbac-least-privilege-review
description: Use when Roles, ClusterRoles and bindings must be reviewed for wildcards, escalation verbs and cluster-admin grants and tightened proposals are needed.
owner: a24-cluster-security
tools: [kpol_rbac_risk_scan, kpol_rbac_effective_access, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# RBAC least-privilege review

## When to Use

- A `kpol.rbac` task or a change to Role, ClusterRole, RoleBinding or ClusterRoleBinding manifests (`gitops.manifests`, `iac.change`) needs a least-privilege verdict, or a subject needs an effective-access answer.
- Review and proposals only (L2). Service-account tokens are `skill://a24-cluster-security--sa-token-hardening`; cross-tenant bindings are `skill://a24-cluster-security--tenant-isolation-audit`. Live RBAC edits and deleting a binding are never done here (production L3, delete L4).

## Procedure

1. Collect read-only with `bash`: `kubectl --context <ctx> get roles,rolebindings,clusterroles,clusterrolebindings -A -o json`, or take manifest files from the task. No explicit context or path: BLOCKED needs=input. Keep the `roles` (Role and ClusterRole, aggregated ones included) and `bindings` lists apart.
2. Run `python3 scripts/kpol_manifest_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json` and keep every `rbac.*` finding with its `location`. Exit 1 only means a major or worse finding.
3. Call `kpol_rbac_risk_scan` with `roles` and `bindings` (at most 500 each). Read `finding_count`, `verdict` and the ids: `rbac.admin-equivalent`, `rbac.cluster-admin-binding` (blocker cluster-wide, major namespaced), `rbac.system-masters`, `rbac.anonymous-binding` (blockers), `rbac.wildcard-verb`, `rbac.wildcard-resource`, `rbac.secret-read`, `rbac.pod-exec`, `rbac.nodes-proxy`, `rbac.token-request`, `rbac.escalate`/`bind`/`impersonate`, `rbac.broad-default-role-binding` (default `admin` or `edit` bound cluster-wide), `rbac.create-pods` (minor). Severities are the tool's house judgement. Cross-check: for the same files the id set must equal the `rbac.*` set of `scripts/kpol_manifest_scan.py`; a difference is E-CONTRACT.
4. Bootstrap objects (label `kubernetes.io/bootstrapping=rbac-defaults` on `cluster-admin`, `admin`, `edit`, `view` or a `system:` name; the same label on any other name exempts nothing) and the `cluster-admin` ClusterRole are skipped by the tool. Do not report the skipped ones clean; only report bindings that point at them.
5. For every blocker or major binding call `kpol_rbac_effective_access` with `roles`, `bindings`, `subject` `{kind, name, namespace}` (`namespace` required for a ServiceAccount) and `checks` such as `{verb: "get", resource: "secrets", namespace: "<ns>"}`, `{verb: "create", resource: "pods", subresource: "exec"}`, `{verb: "*", resource: "*"}`. Use `access_level` (`none`, `namespaced`, `cluster-wide`, `cluster-admin`) and each check's `allowed` and `granted_by`. A non-empty `unresolved_roles` means the answer is incomplete: say so, do not claim least privilege.
6. Compare the two tools: a binding flagged `rbac.binding-to-risky-role` must list its role in `roles` of the `kpol_rbac_effective_access` result for that subject (`granted_by` names only the first matching role); a mismatch is E-CONTRACT.
7. Propose the smallest fix per finding: explicit `verbs` and `resources` instead of `*`, `resourceNames` for secrets, a namespaced Role instead of a ClusterRole, a dedicated ServiceAccount instead of `default`. Replace `rbac.authorization.k8s.io/v1beta1` (removed in 1.22) by `v1`. Write the proposals as manifest diffs in `cluster-reports/<cluster>/rbac-review.md` with `write` and digest them with `shared_artifact_digest`.
8. Decision on the `kpol_rbac_risk_scan` findings: any blocker is a hard stop for the change; major needs a fix or a registered exception (`skill://a24-cluster-security--exception-lifecycle`, L4). You never waive, accept or sign.

## Outputs

- `cluster.security.findings` (RBAC): findings with id, severity, location, `access_level` evidence.
- `cluster.policy.set` (RBAC variant): proposed replacement Roles and bindings, with digest.

## Failure Handling

- INVALID_INPUT (`subject.namespace` missing, more than 500 roles): fix the named field or split the input; else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; name the unreviewed subjects. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Webhook and aggregated-authorizer decisions (Node, ABAC, external authorizers) are not modelled: effective access is RBAC only. State it.
- Never apply, edit or delete a Role or binding yourself; L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A10 for the findings (A22 gets the proposed diffs as `cluster.policy.set`). Fill `goal`, `completed`, `decisions` (accepted proposals), `artifacts` (report and digest, producer A24), `open_questions` (unresolved roles), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
