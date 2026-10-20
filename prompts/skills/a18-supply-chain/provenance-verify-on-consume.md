---
name: a18-supply-chain--provenance-verify-on-consume
description: Use when an artifact is about to be promoted or deployed and its provenance, signer identity and required attestations must be verified against policy first.
owner: a18-supply-chain
tools: [supply_provenance_match, supply_slsa_level, supply_signature_policy, supply_attestation_bundle, shared_handover_read, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Verify provenance on consume

## When to Use

- A `build.artifact` from A11 is going to promotion (A12) or deployment (A22, A24) and the consumer needs one verified-or-refused record: digest in provenance, SLSA level, trusted signer, required attestation kinds.
- Not for raising the level of a pipeline: `skill://a18-supply-chain--slsa-uplift`. Not for writing a signer policy: `skill://a18-supply-chain--signature-policy-rollout`. The security gate and its verdict are A10's: this skill is evidence, never `swarm_gate`.
- Verification is L2 (`image_policy`). A waiver for a missing or unverified attestation is L3 (`policy_exception`), human only; any key or trust-root change is L4.

## Procedure

1. Call `shared_handover_read` with the build `task_id`. Take `artifact_digest` (`sha256:<hex>`, the image digest, never a tag), the artifact name, the attestation files and the policy: `required_level`, `trusted_builder_ids`, signer rules, `required_kinds`. A tag instead of a digest, or a missing policy item, is BLOCKED needs=input; resolve the tag to a digest with `bash` (`crane digest` or `docker buildx imagetools inspect`) only to report it, then use the digest.
2. Verify cryptographically with `bash`, read-only and only when the tool exists: `cosign verify-attestation --type slsaprovenance1 --certificate-identity <exact identity> --certificate-oidc-issuer <issuer> <image>@sha256:<hex>` (check `--help` for the type names of the installed version) or `slsa-verifier verify-image`. Record `signature_verified` per attestation from the exit status and output only. Tool missing: false, and say so.
3. Call `supply_provenance_match` with `provenance` (file text), `artifact_digest` and `artifact_name`. `matched` false is a refusal: `provenance.digest-not-in-subjects` and `provenance.digest-mismatch` are blockers (the provenance describes another artifact), `provenance.name-mismatch` is major.
4. Call `supply_slsa_level` with the same `provenance`, `signature_verified`, `trusted_builder_ids`, `isolated_build`, `signing_key_isolated` (true only when the platform attests it) and `required_level`. Refuse when `meets_required` is false; the `slsa.l<n>-*` findings name why.
5. Call `supply_signature_policy` with `signatures` (`signer`, `issuer`, `verified`, `in_transparency_log` from step 2), `rules`, `min_valid_signatures` and `require_transparency_log`. Refuse on any blocker (`signature.verification-failed`, `signature.required-signer-missing`) or a major (`signature.not-in-transparency-log`, `signature.too-few`); `signature.unexpected-signer` (minor) is reported and needs a human to look.
6. Call `supply_attestation_bundle` with `artifact_digest`, `attestations` (`predicate_type`, `subject_digest`, `signed`, `signature_verified`) and `required_kinds`. Cross-check: a `provenance` kind that is `complete` must agree with step 3 `matched` and step 4 `signature_verified`; `attestation.digest-mismatch` here and `matched` true is E-CONTRACT.
7. Decide. Verified only when steps 3 to 6 each returned `pass` or `complete` and no cross-check disagreed. Call `shared_verdict` with every finding severity from all four calls as evidence (not the gate verdict); `fail` means refuse. Write the record with `write` and call `shared_artifact_digest` for it.

## Outputs

- `supply.attestation.bundle`: `decision` (verified or refused), the four tool results summarised with finding ids, verifier command and exit status, `as_of`, artifact digest.

## Failure Handling

- INVALID_INPUT naming `provenance` or `artifact_digest`: fix the field once (digest must be `algorithm:hex`), else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; a partially checked artifact is refused, not verified.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; a skipped check means not verified.
- Any waiver (unsigned legacy image, missing SBOM): BLOCKED needs=human-approval (L3); never promote on your own evidence.
- Two sources disagree about the digest or builder: E-CONTRACT; list both and refuse.

## Handover

- `to_agent`: A12 when the artifact is for release, else the assigner; A10, A22 and A24 consume the same bundle. `goal`: promote only if `decision` is verified; `completed`: checks; `decisions`: refusal reasons; `artifacts`: record with digest (producer A18); `constraints`: digest not tag; `verification`: verified only when all checks ran.
- End with `shared_handover_validate` then `shared_handover_write`.
