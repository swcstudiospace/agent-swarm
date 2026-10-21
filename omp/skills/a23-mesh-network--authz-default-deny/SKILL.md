---
name: "a23-mesh-network--authz-default-deny"
description: "Use when a mesh.policy task must add default-deny AuthorizationPolicies and replace wildcard or allow-all rules with least-privilege service-to-service allows."
disable-model-invocation: true
---

# AuthorizationPolicy default-deny

## When to Use

- A `mesh.policy` task needs namespaces to reject traffic that no `ALLOW` policy names, or an existing policy set contains `{}` rules, `principals: ["*"]`, open `ipBlocks` or `not*` negative matches.
- Policy design is L2; applying to a production cluster is L3 (`prod_mesh_change`).
- Not for L3/L4 packet filtering (`skill://a23-mesh-network--netpol-default-deny-rollout`) nor for Kubernetes RBAC (A24).

## Procedure

1. Run `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir> --json` and read the `authz-default-deny` check and its findings (`AUTHZ-NO-DEFAULT-DENY` is major). Exit 1 means a major/blocker finding or an incomplete scan (`scan/incomplete`) exists, not a tool failure.
2. Call `mesh_authz_lint` with `policies` (every AuthorizationPolicy as parsed JSON), `namespaces` (the target namespaces; when omitted only namespaces holding a policy are checked, so list every namespace) and `root_namespace` (default `istio-system`). Keep `mesh_wide_default_deny`, `namespaces_without_default_deny`, `verdict` and the finding ids. A namespace counts as default-deny when a namespace-wide or root-namespace ALLOW policy applies to it (empty `spec`, or specific rules) or a namespace-wide DENY policy has the rule `{}`; an ALLOW rule `{}` or one with a `not*` negative match cancels the first form, never the DENY one. A selector key other than `matchLabels` makes the policy scoped, which earns no namespace credit.
3. Cross-check: the namespaces the script names for `AUTHZ-NO-DEFAULT-DENY` must be exactly `namespaces_without_default_deny` of step 2. A difference is E-CONTRACT (different manifest sets): reconcile the inputs before going on.
   Locate the policy file of each differing namespace with `grep`.
4. Triage by id. `AUTHZ-ALLOW-ALL` (blocker when mesh-wide/unscoped, major when scoped), `AUTHZ-NEGATIVE-MATCH`, `AUTHZ-OPEN-IPBLOCK` and wildcard principal/namespace findings are majors that must be replaced, not waived. `AUTHZ-SELECTOR-UNSUPPORTED` (minor) means the selector is not Istio's `matchLabels`: fix it. `AUTHZ-NO-SOURCE`, `AUTHZ-BROAD-PATTERN` and `AUTHZ-DENY-NO-RULES` are minor: fix when touching the policy anyway. `AUTHZ-DENY-ALL` (info) blocks every request whatever an ALLOW says: confirm it is intended, it is not a rollout end state. `AUTHZ-SCOPE-CONFLICT` and `AUTHZ-CUSTOM-NO-PROVIDER` are broken policies: fix first.
   Cite the `mesh_authz_lint` finding text as evidence for every id you act on.
5. Derive the allow list from evidence: callers per workload from the `architecture.blueprint` and the `api.contract` (service names, methods, paths) plus observed `istio_requests_total{source_principal, destination_service}`. Write one `ALLOW` policy per workload with `from.source.principals` (spiffe identity `cluster.local/ns/<ns>/sa/<account>`) and `to.operation` methods and paths; never `*`. Callers without evidence get no rule and are listed as open questions.
6. Principal rules only work when the caller is authenticated by mTLS. Call `mesh_mtls_coverage` with the `peer_authentications` and `workloads` of the namespace: every destination named in an allow rule must be `strict` (`non_strict_workloads` empty for it), otherwise the rule can be bypassed by plaintext. If not, hold the policy behind `skill://a23-mesh-network--strict-mtls-rollout`.
7. Stage the default-deny last: first add the allows, then an empty-spec `ALLOW` policy named `default-deny` per namespace (`spec: {}`). Optionally use `action: AUDIT` or a dry-run annotation (`istio.io/dry-run: "true"`) one cycle before enforcement. Re-run `mesh_authz_lint` on the proposed set: `namespaces_without_default_deny` must be empty and no major remains. Use `mesh_netpol_reachability` only to explain a blocked flow that the mesh policy is not the cause of.
8. Production apply is L3: write the diff to `mesh-reports/<cluster>/authz-<namespace>.md` with `write`, call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod AuthorizationPolicy default-deny awaiting L3 approval", "needs": ["human-approval"]}`, and digest manifests and report with `shared_artifact_digest`.

## Outputs

- `mesh.policy.set` (authorization variant): allow policies, default-deny per namespace, before/after lint counts, unresolved callers.

## Failure Handling

- INVALID_INPUT (`policies` item not an object, unknown `action`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unlinted policies are not clean. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Ambient waypoint targetRefs, `CUSTOM` providers and JWT `requestPrincipals` semantics are only partly modelled: defer to the tool verdict and mark the rest as a risk.
- Applying a default-deny is not idempotent in effect (it drops traffic): never retry; BLOCKED needs=human-approval on L3.

## Handover

- `to_agent`: A24 (policy-as-code audit) with A22 delivering manifests. `goal`: namespaces and workloads covered. `completed`, `decisions` (allow list source), `artifacts` (policies, report, digests), `open_questions` (callers lacking evidence), `verification` (before/after lint counts).
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/authz-default-deny.md; edit the source and run scripts/build_agents.py -->
