---
name: a22-gitops--sync-wave-ordering
description: Use when an Argo CD Application applies CRDs, namespaces, hooks and workloads together and sync stalls; order phases and waves so dependencies exist first.
owner: a22-gitops
tools: [gitops_sync_wave_check, gitops_kustomize_check, gitops_app_sync_policy, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Sync wave ordering

## When to Use

- One Application bundles Namespaces, CRDs and their custom resources, migration Jobs or configuration that workloads need, and a sync fails with "no matches for kind", hangs on an unhealthy wave, or applies a workload before its ConfigMap.
- Not for ordering between Applications of different repositories (use the app-of-apps root: `skill://a22-gitops--app-of-apps-bootstrap`) and not for Flux, which orders with `dependsOn` between Kustomizations. Add-on CRD upgrades from A21 `cluster.upgrade.plan` are consumed as facts about versions.
- Editing waves in a non-prod app is L2; the production sync that applies them is L3.

## Procedure

1. Render the objects of the one Application with `bash` (`kustomize build <overlay>` or `helm template --include-crds`) into a file and read it with `read`. Keep the objects of exactly one Application; hooks come from `argocd.argoproj.io/hook`.
2. Call `gitops_sync_wave_check` with `manifests` (the rendered objects, parsed). Read `plan` (groups in sync order), `wave_count` and `findings`. Argo CD runs phase PreSync, then Sync, then PostSync; inside a phase, ascending `argocd.argoproj.io/sync-wave` (default 0, negatives allowed), then kind order, then name.
3. Fix each `gitops_sync_wave_check` finding class by rule: Namespace after its contents or CRD in the same or a later wave than its custom resources: give Namespaces and CRDs a lower wave (-2 for Namespaces, -1 for CRDs is a common convention, not a requirement); workloads before the ConfigMap, Secret, ServiceAccount or PVC they reference: raise the workload wave above the dependency. A wave that does not parse is silently 0 in Argo CD: treat as `major`.
4. Hooks, written with `edit`: a database migration is a `PreSync` Job with `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation` (or `HookSucceeded`). A hook without a delete policy blocks the next sync with a name clash. SyncFail hooks must be idempotent.
5. Apply the fix as annotations in the base or a Kustomize patch with `edit`. Call `gitops_kustomize_check` with `kustomization` and `resources`: a patch target that matches nothing means your annotation never lands (`unresolved_patch_count` above 0 fails).
6. Re-run `gitops_sync_wave_check` on the fixed render. Expected: `verdict: pass`, `wave_count` as designed, no finding for Namespace, CRD or dependency order. Count of resources must equal the first run (`resource_count`); a difference means you dropped objects.
7. Call `gitops_app_sync_policy` with `resource` and `environment`. Waves only matter if `CreateNamespace=true`, `ServerSideApply=true` and `SkipDryRunOnMissingResource=true` are set the way the plan needs: a custom resource applied in a wave before its CRD exists fails the dry run unless the last option is set on that resource. Check the option names in `findings` (`sync-option-value`, `sync-option-unknown`).
8. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <app-dir> --json`. Cross-check: its `waves` findings must equal the tool findings of step 6; a mismatch is E-CONTRACT. Write the final order into the PR text and call `shared_artifact_digest` on the edited files.

## Outputs

- `gitops.manifests`: annotated manifests, wave table (`plan`), hook list, tool verdicts and script counts.

## Failure Handling

- INVALID_INPUT: fix the named object (missing `apiVersion`, `kind` or `metadata`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; objects not parsed are unchecked, not ordered.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`. Without PyYAML the script skips YAML files.
- Argo CD waits a short delay between waves and health-gates each one (the delay is configurable); exact defaults depend on the version: defer to the cluster's Argo CD docs and say so.
- Never retry a sync from this skill; production sync is BLOCKED needs=human-approval.

## Handover

`to_agent` A24 (policy and CRD owners verify nothing admission-blocks the new order; A11 and A23 read the same `gitops.manifests`). Fill `goal`, `completed`, `decisions` (wave per kind), `artifacts` (manifests with digests, producer A22), `open_questions`, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
