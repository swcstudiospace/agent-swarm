---
name: a22-gitops--helm-to-kustomize-migration
description: Use when a Helm workload moves to Kustomize base and overlays with no behaviour change; prove rendered-object equality and value parity per environment.
owner: a22-gitops
tools: [gitops_helm_values_drift, gitops_kustomize_check, gitops_secret_scan, gitops_sync_wave_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Helm to Kustomize migration

## When to Use

- A `gitops.manifest` task converts a chart-based app (per-environment `values-<env>.yaml`) into `base/` plus `overlays/<env>/`, keeping the same running objects.
- Not for upgrading the chart (a change of behaviour: ordinary promotion) and not for keeping Helm and adding overlays (`helmCharts` generator or an Argo CD Helm source stays as is). Chart-owned Secrets follow `skill://a22-gitops--secret-externalization`.
- Writing the files is L2. The first production sync of the converted app is L3: a migration must show an empty diff before it.

## Procedure

1. Render the current state per environment with `bash`: `helm template <release> <chart> -n <ns> -f values-<env>.yaml --include-crds --kube-version <cluster version> > rendered/<env>.yaml`. Use the cluster's real version and `--api-versions` where the chart branches on capabilities. Read the files with `read`; no render, no migration.
2. Call `gitops_helm_values_drift` with `environments` (name plus parsed values), `required_keys` (image, resources, probes), `invariant_keys` (what must not differ between stages) and `reference` (the lowest environment). Everything that differs in `findings` becomes an overlay patch; keys equal everywhere go to the base. `diverging_value_count` of 0 across all stages means the overlays are namespace and image only.
3. Create `base/` with `write` from the lowest environment render, one file per object, with a `kustomization.yaml` listing them, then one `overlays/<env>/kustomization.yaml` per stage using `patches` (not deprecated `patchesStrategicMerge`) with explicit `target` and `namespace`. Helm hooks become Argo CD hooks: `helm.sh/hook: pre-install,pre-upgrade` maps to PreSync.
4. Call `gitops_kustomize_check` per overlay with `kustomization` and `resources` (the base objects). Gates: `unresolved_patch_count` 0, no duplicate id, no namespace collision, no deprecated-field finding. A removed `app.kubernetes.io/managed-by: Helm` label is intended; keep `app.kubernetes.io/name`.
5. Prove equality per environment: `kustomize build overlays/<env>` against `rendered/<env>.yaml`, comparing objects after sorting by kind, name and namespace with `diff` (`bash`); only the Helm labels, annotations and the release Secret may differ. Any other difference is a defect or a deliberate change you list for a reviewer. Count objects on both sides; counts must match.
6. Call `gitops_secret_scan` with `manifests` (the base and overlay objects). Any plaintext Secret or secret-looking env value copied from chart defaults blocks the PR; `exposure_count` must be 0.
7. Call `gitops_sync_wave_check` with the base `manifests`; chart hooks and weights become waves, so the `plan` order must match what Helm did (CRDs first). Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <app-dir> --json --require-digest` and cross-check: its `kustomize` and `secrets` finding counts must agree with steps 4 and 6.
8. Open the PR (`gh pr create`, branch `<seat-prefix>/<task_id>`), never merge. State in it that adopting live Helm-owned objects needs a first sync with the same names and, in Argo CD, `Replace=false` and no prune. Call `shared_artifact_digest` on the base and overlay files.

## Outputs

- `gitops.manifests`: base and overlays, per-environment equality table (object counts, differing fields), tool verdicts and script counts.

## Failure Handling

- INVALID_INPUT: fix the named field (fewer than two `environments`, empty `required_keys` entry) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; an environment not rendered is not migrated.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`. No `helm` or `kustomize` binary: BLOCKED needs=tool, never hand-render.
- Charts using `lookup` or random values (`randAlphaNum`) cannot be reproduced offline: name the objects, ask the owner.
- The cut-over of production ownership (removing the Helm release record) is L3 and BLOCKED needs=human-approval.

## Handover

`to_agent` A11 (CI builds and validates the new layout). Fill `goal`, `completed`, `decisions` (what moved to base), `artifacts` (files with digests, producer A22), `open_questions` (non-reproducible objects), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
