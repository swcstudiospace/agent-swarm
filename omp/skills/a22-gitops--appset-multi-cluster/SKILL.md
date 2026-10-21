---
name: "a22-gitops--appset-multi-cluster"
description: "Use when one workload must deploy to several clusters via an Argo CD ApplicationSet; check generator, cluster selection, template keys and rollout order."
disable-model-invocation: true
---

# ApplicationSet for several clusters

## When to Use

- A `gitops.manifest` task fans one app out over environments or clusters (a `clusters`, `git` directory or `matrix` generator) instead of hand-copied Applications.
- Not for a single cluster (`skill://a22-gitops--app-of-apps-bootstrap`) and not for mesh routing across clusters (A23 `mesh.policy.set`, consumed as constraints). Serverless configs from A19 (`serverless.config`) that vary per cluster enter as generator values.
- Non-prod ApplicationSet changes: L2. A production-capable ApplicationSet creates and can delete production Applications: L3 to apply, and its deletion or a selector that stops matching is L4.

## Procedure

1. List the clusters registered in Argo CD from the task (names and labels on the cluster secrets, such as `env=prod`, `region=eu`); never read cluster credentials. Read the current ApplicationSet with `read`.
2. Choose the generator and write it with `edit`: `clusters` with a `selector` for "every cluster with label X", `git` directories for one folder per environment, `matrix` (exactly 2 children) for cluster x directory, `merge` with `mergeKeys` to override per cluster. Use `goTemplate: true` with `goTemplateOptions: ["missingkey=error"]` so a missing key fails instead of rendering an empty string.
3. Call `gitops_appset_check` with `applicationset`, `clusters` (`{name, labels}` for each registered cluster) and `environment`. Read `generator_count` and `matched_cluster_count`. Findings that block: `selector-matches-no-cluster` (generated Applications, and with the resources finalizer their workloads, disappear), `all-clusters-selected` (major, a match-all selector: reported even when `clusters` is empty, so every cluster including prod is selected), unknown template keys, duplicate Application names or destinations, generator type not exactly one.
4. Cross-check the match from `gitops_appset_check`: `matched_cluster_count` must equal the number of clusters you expect from step 1 by label. A different number is E-CONTRACT: fix the selector, never the expectation. An empty `clusters` input yields `-1` (unknown): supply the list.
5. Set the sync policy of the template: production templates carry no `automated` block; use `preserveResourcesOnDeletion: true` in the ApplicationSet `syncPolicy` so deleting the set leaves the workloads, and `applicationsSync: create-update` where deletions of Applications must not happen from Git. Call `gitops_app_sync_policy` with `resource` (one rendered Application) and `environment`; its `findings` repeat the Application-spec rules.
6. Order the rollout with `edit`: `strategy.type: RollingSync` (a progressive-sync feature that must be enabled on the controller, off by default, Beta since Argo CD v3.3: enable `--enable-progressive-syncs` and verify before relying on it; it forces automated sync off on every generated Application, which `gitops_appset_check` reports as `template-auto-sync-ignored-by-rolling-sync`), `matchExpressions` on a label such as `env` and `maxUpdate: 1` for production steps. `gitops_appset_check` flags empty `rollingSync.steps` and step labels the template does not set (`rolling-sync-empty`, blocker) and a multi-Application production set without RollingSync (`no-progressive-sync`, minor).
7. Verify the repository side: call `gitops_repo_layout` with `paths`, `expected_envs`, `production_envs`, `clusters` and `engine: argocd`; each cluster you target must have its `clusters/<name>/` directory and each generated path must exist (`cluster_dirs`). Call `gitops_kustomize_check` for the overlay each generated Application points to.
8. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json`; its `apps` findings must not exceed those of steps 3 and 5. Open the PR, never merge; call `shared_artifact_digest` on the ApplicationSet file.

## Outputs

- `gitops.manifests`: ApplicationSet, generator table, matched clusters, rollout order, tool verdicts and script counts.

## Failure Handling

- INVALID_INPUT: fix the named field (`applicationset` not `argoproj.io/v1alpha1`, a cluster entry without `name`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; generators fed by the remote system (`scmProvider`, `pullRequest`, `plugin`) produce keys the tool cannot see: name them unverified.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Progressive sync and Go template details differ by Argo CD version: defer to the tool output and the installed version's docs.
- L3/L4: BLOCKED needs=human-approval; never delete an ApplicationSet from this skill.

## Handover

`to_agent` A23 (mesh policy per cluster follows the cluster set; A24 and A11 read the same `gitops.manifests`). Fill `goal`, `completed`, `decisions` (generator, selector, rollout steps), `artifacts` (files with digests, producer A22), `open_questions`, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a22-gitops/appset-multi-cluster.md; edit the source and run scripts/build_agents.py -->
