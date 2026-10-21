---
name: "a22-gitops--image-update-automation"
description: "Use when a workload should follow new image versions through Git; pick a semver, alphabetical or numerical policy, require prod digests and attested images."
disable-model-invocation: true
---

# Image update automation

## When to Use

- A `gitops.reconcile` task sets up or reviews automated image bumps (Flux `ImageRepository`, `ImagePolicy`, `ImageUpdateAutomation`, or Argo CD Image Updater with Git write-back) for one workload.
- Not for a single human-chosen promotion (`skill://a22-gitops--env-promotion-pr`) and not for the signing or scanning of the image (A18 owns `supply.attestation.bundle`, A11 the `build.artifact`). You consume both and refuse images without them.
- The bot may commit to a non-prod branch (L2). Anything that changes the production overlay goes through a PR for L3 review; automation never writes to the production branch directly.

## Procedure

1. Read the `build.artifact` (repository, tags, digest) and the `supply.attestation.bundle` (signature and provenance result per digest) with `read`. A digest without a passing attestation verdict is not eligible: BLOCKED needs=input for A18, never assumed.
2. Pick the policy (to feed `gitops_image_policy`): semver range for released services (`>=1.4.0 <2.0.0` or `^1.4`, Masterminds syntax; prereleases are ignored unless the range names one), `numerical` with `order: asc` for build-number tags, `alphabetical` for timestamp tags (`asc` selects the greatest, as in Flux). Never pick `latest` or a branch tag.
3. Call `gitops_image_policy` with `image` (the reference exactly as in the manifest), `candidate_tags` (the tags from the registry listing in the task), `policy` (`type`, `range`, `order`) and `require_digest` true for production. Read `current_in_range`, `selected_tag`, `update_available`, `eligible_count`, `digest_pinned`, `floating`. `eligible_count` 0 or `selected_tag` empty means the range matches nothing: fix the range, do not widen it blindly.
4. Decision rule on the `gitops_image_policy` output: non-prod may follow the policy (tag plus digest recorded); production must have `digest_pinned` true and `floating` false, and a major-version jump (`selected_tag` major above the current) is not automated: it becomes a human PR. `selected-older-than-current` (minor, so `verdict: warn`) means the policy selects a tag older than the running one: a human confirms it before any write-back. Run the tool again with the current image and `candidate_tags` containing only the selected tag to prove a stable result (idempotent: same input, same `selected_tag`).
5. Wire the automation. Flux: `ImagePolicy` per image, `ImageUpdateAutomation` limited to a path with `update.strategy: Setters` and setter markers in the manifests, committing to a branch named `<seat-prefix>/<task_id>` or a dedicated bot branch. Argo CD Image Updater: `write-back-method: git` to a non-prod branch, `update-strategy` semver or digest. Credentials are references to existing Secrets, never values; the Image Updater and Flux API versions in use must match the cluster (check `kubectl api-resources`, defer to it).
6. Call `gitops_app_sync_policy` with `resource` and `environment` for the Application or Kustomization that consumes the bumped files: production `auto_sync` or `prune_enabled` true is a finding, and `revision_class` floating means a bump reaches production without a PR.
7. Call `gitops_kustomize_check` with the overlay `kustomization` and `resources`: `images:` entries must name an image that a resource really uses (`unresolved_patch_count` 0). Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json --require-digest`; every `images:` finding (floating tag, tag-only reference) must also appear as `floating` or not `digest_pinned` in step 3, otherwise E-CONTRACT.
8. Write the manifests and the policy notes with `write`, open the PR, never merge; call `shared_artifact_digest` on each changed file.

## Outputs

- `gitops.manifests`: ImagePolicy and automation objects, per-environment policy table (type, range, digest rule), selected tag and verdicts, script counts.

## Failure Handling

- INVALID_INPUT: fix the named field (`policy` type missing, `range` with a semver syntax error) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; images not evaluated stay on their current pin.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; keep the current pin.
- The tool does not talk to a registry: tag lists come from the task; a stale list gives a stale `selected_tag`. Tell the reviewer.
- Never retry a bot commit loop by editing its branch by hand; production write-back is BLOCKED needs=human-approval (L3).

## Handover

`to_agent` A11 (CI owns the artifacts the policy selects; A24 verifies the digest rule). Fill `goal`, `completed`, `decisions` (policy per environment), `artifacts` (files with digests, producer A22), `open_questions`, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a22-gitops/image-update-automation.md; edit the source and run scripts/build_agents.py -->
