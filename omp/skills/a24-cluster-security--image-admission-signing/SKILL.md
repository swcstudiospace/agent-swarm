---
name: "a24-cluster-security--image-admission-signing"
description: "Use when workload images must be limited to allowed registries, pinned by digest and matched to verified signature digests before an admission rule enforces it."
disable-model-invocation: true
---

# Image admission and signature policy

## When to Use

- A `kpol.admission` task: define which registries a cluster may pull from, require `@sha256:` digests, and require a verified signature, using the `supply.attestation.bundle` from A18 and `gitops.manifests` from A22.
- A24 checks images against the verified digests it was given and authors the admission rule. It does not sign, verify provenance policy, or scan CVEs: signing and SBOM are A18, vulnerabilities are A10 (`vulnerability.report`). Generic policy authoring is `skill://a24-cluster-security--admission-policy-authoring`.
- Audit-mode rule and report: L2. Switching to enforce: L3.

## Procedure

1. Read A18's handover with `shared_handover_read` (`{task_id}`) and find the `supply.attestation.bundle` artifact. Call `shared_artifact_check` with `{task_id}` and check `ok` and `failed`: only a bundle whose digest matches may be used. Missing or failed: BLOCKED needs=input, name the artifact.
2. With `read`, extract the image digests the bundle reports as verified (signature and attestation verified by A18, for example with `cosign verify` against a stated certificate identity and OIDC issuer). Put them in `verified_digests` as complete digests (`sha256:` plus 64 hex; a truncated one is refused). Digests not in the bundle never go in. The tool matches a digest alone, not the repository it was verified for: keep only digests A18 verified for the image reference in the manifest, and say so.
3. Take `allowed_registries` from the task (hosts or `host/path` prefixes) and `require_digest` and `require_signature` booleans: production defaults are true for both (this skill's default; staging may use `require_digest` false).
4. Call `kpol_image_admission_check` with `workloads` (all container, init container and ephemeral container images are covered), `allowed_registries`, `require_digest`, `require_signature`, `verified_digests`. Read `image_count`, `finding_count`, `verdict`: `image.registry-not-allowed` (major), `image.not-pinned` (major when `require_digest`, else minor: a tag such as `latest` or a tag-only reference), `image.unsigned` (major: digest absent from `verified_digests`; a tag-only reference cannot carry signature evidence).
5. Cross-check `image_count` with the distinct image strings you extract with `grep` from the manifests (`image:` keys); a difference is E-CONTRACT. Tag-pinned images in the bundle must resolve to the digest the cluster actually runs: compare with `kubectl --context <ctx> get pods -A -o jsonpath` `imageID` fields read-only through `bash`.
6. Author the admission rule per `skill://a24-cluster-security--admission-policy-authoring` (for example a Kyverno `verifyImages` rule, a sigstore policy-controller `ClusterImagePolicy`, or a VAP on `image` patterns for registries and digest) in audit mode. Call `kpol_admission_policy_lint` on it: `admission.fail-open` and exclusions must be gone or justified, since a signature check that fails open protects nothing.
7. Flip: only when the check shows 0 `image.unsigned` and 0 `image.registry-not-allowed` for in-scope namespaces. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "image admission audit to enforce, L3", needs: ["human-approval"]}`. Write the report to `cluster-reports/<cluster>/image-admission.md` with `write` and digest it with `shared_artifact_digest`.

## Outputs

- `cluster.policy.set` (image variant): rule manifests in audit mode, registry list, digest and signature flags.
- `cluster.security.findings`: per-image findings with location `Kind/namespace/name#container`.

## Failure Handling

- INVALID_INPUT (`allowed_registries` empty, a malformed image reference): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked workloads count as unsigned. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool does not verify signatures, transparency log entries or attestations itself and does not resolve tags: it trusts `verified_digests`. Say it. Exact `verifyImages` and `ClusterImagePolicy` syntax differs by engine version: confirm with the lint and a server-side dry-run.
- Never retry a flip; L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A22 (applies the rule through GitOps); A18 gets gaps in its bundle in `open_questions`, A10 the findings. Fill `goal`, `completed`, `decisions`, `artifacts` with digests, `blockers`, `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/image-admission-signing.md; edit the source and run scripts/build_agents.py -->
