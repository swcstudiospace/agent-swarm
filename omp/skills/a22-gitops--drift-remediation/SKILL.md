---
name: "a22-gitops--drift-remediation"
description: "Use when live objects differ from Git and a drift.report is needed; classify each diff as benign, drift or dangerous and pick ignore, self-heal or investigate."
disable-model-invocation: true
---

# Drift remediation

## When to Use

- An Application is `OutOfSync`, a Flux Kustomization or HelmRelease reports drift, or A21 `fleet-drift-report` lists workloads whose live state differs from Git. You hold the field-level differences (`argocd app diff`, `flux diff kustomization`, `kubectl diff`).
- Not for removing objects that left Git (`skill://a22-gitops--prune-safety-review`) and not for cluster-wide node or version drift (A21).
- Non-prod re-sync to Git: L2. Production sync: L3. A `dangerous` finding is never synced first: it is investigated, and may be a security incident for A24.

## Procedure

1. Collect diffs read-only with an explicit context and namespace: `argocd app diff <app> --refresh` or `flux diff kustomization <name> --path <dir>` through `bash`. Never `argocd app sync` or `kubectl apply` at this stage. Record for each diff `kind`, `name`, `namespace`, `path` (dotted, `$` for the whole object), `change` (`added`, `removed`, `changed`) and `field_manager` when shown.
2. Read the Application or Kustomization with `read` and take its `ignoreDifferences` (Argo: `jsonPointers`, `jqPathExpressions`, `managedFieldsManagers`) as dotted prefixes. Call `gitops_drift_classify` with `diffs` and `ignore_differences`.
3. Decision rule on the `gitops_drift_classify` output: `verdict: clean` and `recommended_action: ignore` means only server-set fields, HPA-written `spec.replicas` (`field_manager: kube-controller-manager`) and API defaults; `drifted` means `self-heal`: Git wins; `dangerous` means `investigate` before any sync. RBAC, NetworkPolicy and admission objects, Secret data, Service exposure, security contexts, service accounts and images are always `dangerous` when changed outside Git.
4. Sort `classified` by class. For every `dangerous` entry ask who changed it: `kubectl --context <ctx> -n <ns> get <kind> <name> -o yaml` and read `metadata.managedFields` manager and time, plus the cluster audit log through A24. State the finding, never guess the actor. A changed image that A12 or A22 did not promote goes to `shared_escalate` with `reason_code: "policy"`.
5. For `drifted` entries decide Git or live, and record it with `write`: if the live change is intended (an emergency fix), the fix is a PR to Git, not a sync. If it is not, plan self-heal. Accepted noise gets a narrow `ignoreDifferences` entry (one path, one kind); an entry of `.` or `/` hides all drift and is refused.
6. Call `gitops_app_sync_policy` with `resource` and `environment`: `auto_sync` plus `selfHeal` in production silently reverts live fixes, and Flux `driftDetection.mode` not `enabled` means a HelmRelease never reverts. Check that the policy matches your decision.
7. If self-heal would also delete objects (`removed` or `added` diffs of whole resources), call `gitops_prune_risk` with `pruned` (the live objects), `environment`, `automated`, `desired_count`, `allow_empty`; `autonomy_required` of L4 is a hard stop for this skill.
8. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <app-dir> --json` to confirm the Git side itself is sound (a broken overlay looks like drift). Write the report with `write`, then `shared_artifact_digest`.

## Outputs

- `drift.report`: per resource counts (`benign_count`, `drift_count`, `dangerous_count`), `verdict`, `recommended_action`, owner and chosen path (PR, self-heal, investigate), autonomy level.

## Failure Handling

- INVALID_INPUT: fix the named diff field (`change` outside added, removed, changed) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unclassified diffs are not `clean`.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The classifier is rule based: an unusual CRD field falls in `drift`, not `dangerous`; a reviewer checks custom resources with security meaning.
- L3/L4 steps: BLOCKED needs=human-approval. Never sync, patch or delete live objects from this skill.

## Handover

`to_agent` A13 (alerts and trends on drift). Fill `goal`, `completed`, `decisions` (class per resource, ignore entries), `artifacts` (report with digest, producer A22), `open_questions` (unknown actors), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a22-gitops/drift-remediation.md; edit the source and run scripts/build_agents.py -->
