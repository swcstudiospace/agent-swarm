---
name: "a22-gitops--app-of-apps-bootstrap"
description: "Use when a gitops.reconcile task puts a new cluster or environment under GitOps with a root Application or Flux path; check layout, sync policy and child order."
disable-model-invocation: true
---

# App-of-apps bootstrap

## When to Use

- A `gitops.reconcile` task names a cluster (explicit kube context) that has no root Application (Argo CD) or `flux-system` Kustomization yet, plus the environments it must serve.
- Not for adding a release to an existing environment (`skill://a22-gitops--env-promotion-pr`) and not for building the cluster itself (A21 owns cluster bootstrap; consume its `cluster.upgrade.plan` only for version facts). Policies from A24 (`cluster.policy.set`) are children of this tree, not authored here.
- Autonomy: non-prod first sync is L2. The first sync of a production root is L3 (human approval); this skill only opens the PR.

## Procedure

1. List repo files read-only with `bash` (`git ls-files`) and read CODEOWNERS with `read`. Call `gitops_repo_layout` with `paths`, `expected_envs`, `production_envs`, `clusters` (names registered in the task), `engine` (`argocd` or `flux`) and `codeowners` (the file text). `missing_envs` must be empty and `cluster_dirs` must equal `clusters`; `codeowners_evaluated` false means production ownership is unverified: BLOCKED needs=input. Any private-key file finding is a blocker.
2. Design the tree with `write`: `clusters/<cluster>/` holds the root (`apps` Application, or Flux `gotk-sync.yaml`); `apps/<name>/base` plus `overlays/<env>`; children grouped as platform (CRDs, namespaces), policies (A24) and workloads. Do not invent a cluster that is not in `clusters`.
3. Write the root. Argo CD: `spec.source.path: clusters/<cluster>/apps`, `targetRevision` a branch for non-prod, a tag or commit for prod, AppProject other than `default`, `destination.name` of the registered cluster. Flux: Kustomization `kustomize.toolkit.fluxcd.io/v1` with `interval` 10m or more, `prune` explicit, `serviceAccountName` set for production.
4. Call `gitops_app_sync_policy` with `resource` (the root object) and `environment`. Production decision rule: `auto_sync` and `prune_enabled` must both be false and `revision_class` must be `pinned` or `tag`; any blocker or major finding is fixed before the PR. Non-prod: `auto_sync` true is acceptable, `prune_enabled` stays false until `skill://a22-gitops--prune-safety-review` clears it.
5. Order the children with waves: namespaces and CRDs in the lowest wave, policy objects next, workloads last. Call `gitops_sync_wave_check` with `manifests` (the child Application or Kustomization objects of the root). `verdict: fail` stops; `plan` order must read platform, policies, workloads and `wave_count` must match your design.
6. Call `gitops_kustomize_check` per overlay with `kustomization` and `resources`. `unresolved_patch_count` above 0 is a failure, as are duplicate ids and namespace collisions.
7. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path clusters --json --require-digest`. Exit 0 is clean, exit 1 is `status: fail` (major or blocker findings, not a tool error), exit 2 is bad input. Cross-check: the script `apps` findings must agree with step 4 (for example both flag `prod-auto-prune`); a disagreement is E-CONTRACT, report both.
8. Commit on `<seat-prefix>/<task_id>` with `bash` and open the PR with `gh pr create`; never merge it. For a production cluster call `shared_escalate` with `reason_code: "policy"` and `needs: ["human-approval"]`. Call `shared_artifact_digest` on the root and layout files.

## Outputs

- `gitops.manifests`: root, per-cluster tree, tool verdicts and script `finding_count` with severities.
- PR URL and the L2/L3 label.

## Failure Handling

- INVALID_INPUT: fix the named field (empty `clusters`, unknown `engine`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked files; never call the tree `pass`.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing`. Without PyYAML the script reports YAML files as `skipped:tool-missing`: not clean.
- Wave ordering between Application objects needs Argo CD to assess Application health; behaviour depends on the Argo CD version, so state the version assumed and have a human confirm.
- L3/L4 steps: BLOCKED needs=human-approval; never retry a PR creation that succeeded.

## Handover

`to_agent` A11 (CI validates and builds on the tree; A23 and A24 read the same `gitops.manifests`). Fill `goal`, `completed`, `decisions` (engine, revision classes), `artifacts` (files with `shared_artifact_digest` digests, producer A22), `open_questions`, `verification` (`partial` until the production approval). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a22-gitops/app-of-apps-bootstrap.md; edit the source and run scripts/build_agents.py -->
