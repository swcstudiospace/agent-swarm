---
name: "a24-cluster-security--admission-policy-authoring"
description: "Use when a new ValidatingAdmissionPolicy, Kyverno or Gatekeeper rule must be written and proven fail-closed in audit mode before any enforcement."
disable-model-invocation: true
---

# Admission policy authoring

## When to Use

- A `kpol.admission` task wants a new or changed admission control (ValidatingAdmissionPolicy plus Binding, Kyverno `ClusterPolicy`, Gatekeeper ConstraintTemplate plus Constraint, or a validating webhook).
- This skill authors and proves in non-blocking mode. The mode flip is L3. Pod Security labels are `skill://a24-cluster-security--pss-enforce-rollout`; fixture suites are `skill://a24-cluster-security--policy-as-code-testing`; image rules are `skill://a24-cluster-security--image-admission-signing`.

## Procedure

1. Read the requirement and `gitops.manifests` (A22) with `read` and `grep`; get the server version from `cluster.fleet.status`. Pick the engine: ValidatingAdmissionPolicy (CEL, `admissionregistration.k8s.io/v1`, GA in 1.30) when the rule is a pure object check; Kyverno or Gatekeeper when the rule needs mutation, generation, context lookups or parameter data. Record why.
2. Write the policy so it starts non-blocking: VAP binding `validationActions: [Warn, Audit]`; Kyverno `validationFailureAction: Audit`; Gatekeeper `enforcementAction: dryrun` (or `warn`). Scope it by `matchConstraints` or `match` to the namespaces in the task; never exclude `kube-system` silently, list every exclusion.
3. Call `kpol_admission_policy_lint` with `policies` (every policy, binding and webhook manifest involved, at most 500). Read `enforcing`, `audit_only`, `finding_count`, `verdict` and each id: `admission.fail-open` (major, `failurePolicy: Ignore`), `admission.policy-unbound`, `admission.binding-orphan`, `admission.param-not-found-allow`, `admission.namespace-exclusion`, `admission.identity-exclusion`, `admission.audit-only` (minor, expected at this stage), `admission.invalid-action` (major: a VAP binding with both `Deny` and `Warn`, an unknown mode, or a Gatekeeper `scoped` constraint whose deny is not at `validation.gatekeeper.sh`, `vap.k8s.io` or `*`). Any blocker or major is fixed before moving on. A webhook needs `failurePolicy: Fail`, `timeoutSeconds` 1 to 30, `sideEffects` `None` or `NoneOnDryRun`, `admissionReviewVersions`.
4. Build one must-block and one must-pass fixture per rule. For a rule that restates a Pod Security control, call `kpol_pss_check` with `workloads` set to those fixtures and `level` `baseline` or `restricted`: the must-block fixture shows the finding id, the must-pass shows none. Any other rule (labels, CEL, images) is proven only by step 5; say so.
5. Prove the policy on a non-prod context with `bash`: `kubectl --context <ctx> -n <ns> apply --dry-run=server -f <fixture>`. Under audit-only modes both fixtures are admitted and the violation shows as a warning or an audit annotation; the must-block fixture must produce it, the must-pass one not. A result that differs from the lint or the fixtures is E-CONTRACT: report both.
6. Compute the readiness number: after at least 7 days (this skill's rule) in audit, `violations` from `kubectl get` on the policy report or constraint `status.violations` must be 0 for in-scope namespaces, or each remaining one has an owner. Then call `shared_escalate` with `{task_id, reason_code: "policy", summary: "flip <policy> audit to enforce, L3", needs: ["human-approval"]}`. Production flips are L3 always; you do not flip.
7. Write the policy files, fixtures and the lint output to `cluster-reports/<cluster>/admission-<name>.md` and the manifests under the task path with `write`; digest them with `shared_artifact_digest`.

## Outputs

- `cluster.policy.set` (admission variant): policy manifests in audit mode, fixtures, lint counts, flip request.
- `cluster.security.findings`: remaining lint findings with severities.

## Failure Handling

- INVALID_INPUT (`policies` empty or over 500, a non-object item): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked policies count as failed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- CEL expression correctness and Kyverno JMESPath are not evaluated by the lint; only server-side dry-run proves them. When no non-prod context exists, say the rule is unproven.
- Never retry an apply or flip; L3 or L4 action: BLOCKED needs=human-approval. Never set `failurePolicy: Ignore` to silence an outage.

## Handover

`to_agent` A22 (applies through GitOps). A10 gets the findings. Fill `goal`, `completed`, `decisions` (engine, scope, exclusions), `artifacts` with digests, `blockers` (`human-approval`), `verification` (lint and dry-run results). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/admission-policy-authoring.md; edit the source and run scripts/build_agents.py -->
