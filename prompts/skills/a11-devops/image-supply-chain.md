---
name: a11-devops--image-supply-chain
description: Use when assembling a deployment.manifest and every container image must be digest-pinned, signed and carry an SBOM before it can be listed for deploy.
owner: a11-devops
tools: [devops_image_tag_check, shared_handover_bundle, shared_artifact_check, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Image supply chain

## When to Use

- A release (A12) or environment needs a `deployment.manifest` that lists images (03-agents/A11-devops.md section 3).
- Covers the images at deploy time (immutability, signatures, SBOM). Repo and workflow lint is `skill://a11-devops--pipeline-lint`.

## Procedure

1. Call `shared_handover_bundle` with `{task_id}` and list the images named in `artifacts` and the build.artifact records, plus the signed `verdicts` (A10 produces `sbom.attestation` and `security.gate.verdict`).
2. For each image reference call `devops_image_tag_check` with `{"image_ref": "<ref>"}`. `verdict` `pass` with `reason` `digest-pinned` is accepted. `version-tag` and `commit-sha-tag` pass the tool on convention only (it cannot know the registry enforces immutability): take the digest from the build.artifact record `image` and rewrite the reference to `@sha256:<64 hex>`. A `fail` (`no-tag`, `mutable-tag`, `unrecognized-tag`) is a finding, not an entry.
3. Call `shared_artifact_check` with `{task_id, outputs: [{kind, uri, digest, producer}, ...]}` for the build records and any SBOM file in the bundle. Non-file URIs (registry references, attestations) come back `unchecked`: say so, never assume they are fine.
4. `signatures_verified` is true only when you verified each image yourself and recorded the result, for example `cosign verify <image@sha256:...>` through `bash` exiting 0; an `unchecked` URI is not a verification. `sbom_attached` is true only when an A10 `sbom.attestation` artifact with a digest is in the bundle and its check is `ok`, or an attestation verifier you ran confirmed it. No verifier binary available: `skipped:tool-missing`, both flags false, which blocks (fail closed, prompt constraints).
5. Turn each failed image into a `major` severity and call `shared_verdict` with `{"severities": [...]}`; a `fail` verdict means no manifest is handed over.
6. Assemble the `deployment.manifest` fragment: `env`, `release_id`, `images` (name to `oci://...@sha256:...`), `signatures_verified`, `sbom_attached`, `policy_bundle` taken from the task constraints, never invented. Write it as a file in the workspace outside `.swarm/` (the guard refuses writes there), at the path the task names; when none is named use `<iac-dir>/records/<release_id>.manifest.json` (a convention of this skill).
7. Call `shared_artifact_digest` with `{path}` for the manifest file, then see Handover.

## Outputs

- `deployment.manifest` fragment and its digest; list of rejected images with `reason`.

## Failure Handling

- Whitespace or bad digest (INVALID_INPUT, field `image_ref`): correct once, then drop the image and report it.
- Registry unreachable: DEPENDENCY_UNAVAILABLE, then `skill://a11-devops--dependency-outage`; never ship an unchecked image.
- Missing signature or SBOM: ask A10 through the handover; do not set the flags true.

## Handover

Fill `goal` (deploy exactly these images), `completed`, `remaining` (missing signature or SBOM), `artifacts` (manifest, digest, producer A11), `decisions` (per-image `reason`), `constraints` (digest-pinned only), `verification`. Call `shared_handover_validate`, then `shared_handover_write` to A12.
