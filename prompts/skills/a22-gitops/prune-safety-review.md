---
name: a22-gitops--prune-safety-review
description: Use when a sync, Git deletion or automated prune would delete live objects; list what goes, protect data-bearing kinds and stop at L4 for production.
owner: a22-gitops
tools: [gitops_prune_risk, gitops_app_sync_policy, gitops_drift_classify, gitops_sync_wave_check, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Prune safety review

## When to Use

- An Application shows resources to prune, a PR removes manifests, automated sync gains `prune: true` (Flux `prune: true`), or an app or ApplicationSet is about to be deleted.
- Not for live changes that Git does not know (`skill://a22-gitops--drift-remediation`) and not for undoing a promotion (`skill://a22-gitops--rollback-by-revert`).
- Non-prod prune: L2. Production sync: L3. Production prune of any unprotected object: L4, human approval with data owners; this skill never runs it.

## Procedure

1. Get the prune list read-only with an explicit context and namespace: `argocd app diff <app>` (objects present live, absent in Git) or `flux diff kustomization <name> --path <dir>`; or the `argocd app sync --dry-run --prune` preview. Record each object's apiVersion, kind, name, namespace and annotations. Count the objects of the desired state (`desired_count`) from `kustomize build`.
2. Call `gitops_app_sync_policy` with `resource` and `environment`. Note `prune_enabled` and whether `automated.allowEmpty` is set: `allow-empty-prune` and `prod-auto-prune` are blockers in production.
3. Call `gitops_prune_risk` with `pruned` (the live objects), `environment`, `automated` (true when automated sync with prune is configured), `desired_count` and `allow_empty`. Read `autonomy_required`, `protected_count`, `unprotected_count`, `findings`. Blockers by rule: pruning a Namespace or CRD (cascades to everything inside, every custom resource), a StatefulSet with `persistentVolumeClaimRetentionPolicy.whenDeleted: Delete`, a PVC in production, a Crossplane managed resource with `deletionPolicy: Delete`, an empty desired state with `allowEmpty` (the whole application goes).
4. Decision rule: `autonomy_required: L2` (non-prod) may proceed; `L3` (production, nothing unprotected) goes to the production sync approval; `L4` (production with `unprotected_count` above 0) stops: `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]`, and summary listing kinds and names. Do not downgrade by editing the input.
5. Protect what must stay: annotate with `argocd.argoproj.io/sync-options: Prune=false` (skip) or `Prune=confirm` (wait for a human); `PruneLast=true` on the app to delete last; for PVs confirm `persistentVolumeReclaimPolicy: Retain` with `kubectl get pv`; Flux uses the `kustomize.toolkit.fluxcd.io/prune: disabled` annotation. Re-run `gitops_prune_risk` with the annotations in place: `protected_count` must equal the objects you protected, and `findings` of kind `protected:` appear as info.
6. Check ordering on the way out: call `gitops_sync_wave_check` with `manifests` of the desired state; a Namespace or CRD still in a low wave while its contents leave Git is flagged, and `plan` shows the order resources are touched.
7. Cross-check the drift view: call `gitops_drift_classify` with the same objects as `diffs` (`path: "$"`, `change: "removed"` for objects missing live, `"added"` for objects only live). A `dangerous` class on an access-control or admission object should match a `prune-guardrail` finding from step 3; a mismatch is E-CONTRACT.
8. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <app-dir> --json`: `apps` findings on prune must agree with step 2. Write the review (objects, protection, level, approver needed) with `write`; call `shared_artifact_digest`.

## Outputs

- `drift.report` extension: pruned list, `autonomy_required`, protections added, escalation reference.

## Failure Handling

- INVALID_INPUT: fix the named field (`pruned` entry without `kind`, `desired_count` negative) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; objects not listed are unreviewed, so `autonomy_required` is a minimum.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and treat production prune as L4.
- Prune options and annotation names differ between Argo CD and Flux versions: defer to the installed version's docs.
- L4: BLOCKED needs=human-approval; never retry a delete, never run `--prune` or `kubectl delete`.

## Handover

`to_agent` A13 (watches the cluster after a prune). Fill `goal`, `completed`, `decisions` (level, protections), `artifacts` (review file with digest, producer A22), `open_questions` (data owners), `blockers` (`human-approval`), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
