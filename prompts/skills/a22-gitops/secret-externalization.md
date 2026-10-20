---
name: a22-gitops--secret-externalization
description: Use when plaintext Secrets or credential-looking values sit in a GitOps repo; replace them with Sealed, External or SOPS references without printing values.
owner: a22-gitops
tools: [gitops_secret_scan, gitops_kustomize_check, gitops_app_sync_policy, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Secret externalization

## When to Use

- `scripts/gitops_repo_check.py` or an A10 or A24 finding reports `secrets:plaintext-secret:*`, a credential pattern, a secret-looking env var or a private-key file in a manifest repository, or a task asks to make a repo safe to publish.
- Not for secret detection in application code (A10) and not for cluster encryption at rest or RBAC (A24). Rotation of the leaked value is a human step: this skill never reads, prints or rewrites a secret value.
- Writing references is L2. A production secret change and any history rewrite (`git filter-repo`, force-push) are L4: BLOCKED needs=human-approval.

## Procedure

1. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json` and keep only the `secrets` check findings: `file`, `location` and the Secret `name`. Never open the value; the scanner does not echo it and neither do you.
2. Call `gitops_secret_scan` with `manifests` (the parsed objects of those files, in batches of 200). Read `exposure_count`, `reference_count`, `verdict`, `findings`. Cross-check: the number of plaintext findings must match step 1 for the same files; a difference is E-CONTRACT (report both counts).
3. Pick the mechanism per cluster, from the task: External Secrets Operator (`ExternalSecret` referencing a `SecretStore` or `ClusterSecretStore`; use the `apiVersion` the cluster serves, check `kubectl api-resources`), Sealed Secrets (`SealedSecret`, `bitnami.com/v1alpha1`, sealed with the cluster's public certificate), or SOPS (Flux Kustomization `spec.decryption.provider: sops` with a `secretRef` to the decryption key; an `ENC[...]` value in every field and a top-level `sops` block). A reference to a store is preferred in production because the value never enters Git.
4. Replace each plaintext Secret with a reference object using `edit`: same `metadata.name`, `namespace` and type so workloads keep working; env values that were literal become `valueFrom.secretKeyRef`. Remove any `secretGenerator` literal. Do not create the sealed or encrypted payload yourself without the public key: BLOCKED needs=input for the owner.
5. Call `gitops_secret_scan` again on the edited objects. Expected: `exposure_count` 0 and `reference_count` equal to the Secrets replaced. `gitops_kustomize_check` with `kustomization` and `resources` then confirms renames did not break patch targets (`unresolved_patch_count` 0, no duplicate id).
6. Call `gitops_app_sync_policy` with `resource` and `environment`: in Flux, `decryption.provider` other than `sops` is a blocker, and a production Kustomization without `serviceAccountName` applies as cluster-admin. For Argo CD no secret plugin is implied: say which mechanism decrypts at sync time.
7. The leaked value is compromised even after removal, because Git history keeps it. Write a rotation request in the PR text (secret name, owner, systems that use it, no value) and call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]`, summary naming the files. History rewrite is proposed, not executed.
8. Re-run the script on the branch: no `secrets` finding may remain (exit 0 or only non-secret findings). Open the PR, never merge, and call `shared_artifact_digest` on each changed file.

## Outputs

- `gitops.manifests`: reference objects, before and after `exposure_count`, mechanism chosen, rotation request. No secret value anywhere.

## Failure Handling

- INVALID_INPUT: fix the named field (`manifests` item without `kind`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unscanned files are unknown, not clean.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; the PR stays `draft`.
- Scanner patterns are heuristics (names, known formats, `user:password@` in URLs): a clean scan is "none found". Placeholders such as `CHANGEME` are ignored by design; a reviewer checks those files.
- Never retry an escalation; never paste a value into a handover, PR or log.

## Handover

`to_agent` A24 (secrets policy and rotation follow-up). Fill `goal`, `completed`, `decisions` (mechanism per cluster), `artifacts` (changed files with digests, producer A22), `open_questions` (rotation, history rewrite), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
