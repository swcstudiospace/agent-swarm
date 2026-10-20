---
name: "a18-supply-chain--sbom-attest-publish"
description: "Use when a build.artifact ships with a CycloneDX or SPDX SBOM that must be validated, diffed against the last release and checked as a signed attestation."
disable-model-invocation: true
---

# SBOM validate, diff and attestation check

## When to Use

- A11 hands over a `build.artifact` with an SBOM (CycloneDX 1.x or SPDX 2.x JSON) for a release, and the `sbom.bundle` plus a `supply.attestation.bundle` check are due before A10 or A12 consume it.
- A10's own attestation of the SBOM (`skill://a10-security--sbom-attest`) uses the A10 tools; this skill produces the structural and attestation evidence it can cite. Verifying provenance of a consumed artifact is `skill://a18-supply-chain--provenance-verify-on-consume`.
- You never sign or attach attestations: the build identity does that in A11's pipeline. Changing a signing key is L4.

## Procedure

1. Call `shared_handover_read` with the build `task_id`. Take the artifact digest (`sha256:<hex>`), the SBOM path, the previous release's SBOM path and the attestation records the pipeline verified (predicate type, subject digest, signed, signature_verified). Missing previous SBOM: record "first release", skip step 4.
2. Use `read` to load the SBOM text; the tool limit is 262144 characters. Larger: BLOCKED needs=input for a split or per-layer SBOM, never truncate.
3. Call `supply_sbom_validate` with `document` set to that text. Decide on `verdict`, `blocking_count` and `ntia_compliant`: `fail` blocks; `ntia_compliant` false names the unsatisfied `ntia_elements` (`covered` of `total`), the seven NTIA minimum elements of the 2021 baseline (supplier, component name, version, other unique identifiers, dependency relationship, author of SBOM data, timestamp); CISA's 2026 minimum elements replace that baseline and add fields (for example component hash and license) which this tool does not check, so `ntia_compliant` true is not full 2026 compliance: say so in the bundle. Also read `sbom.dangling-reference`, `sbom.duplicate-bom-ref` and `sbom.no-root` (all major) and `format`/`spec_version` (CycloneDX 1.2-1.7, SPDX 2.2/2.3 are known; `sbom.unknown-spec-version` is minor).
4. Call `supply_sbom_diff` with `before` the previous SBOM text and `after` this one. `downgrade_count` above 0 is a major finding (`sbom.version-downgrade`): require a reason from the build handover or list it as an open question. Direction follows semver precedence (a release replaced by its own pre-release is a downgrade); versions that are not semver (epochs, PEP 440, distro revisions) come back `changed` without a direction: compare those by hand. `license_changed_count` above 0 goes to `skill://a18-supply-chain--license-exception`. `verdict` `review` needs a human-readable summary of `added`/`removed`.
5. Cross-check the two tools: call `supply_sbom_validate` on the previous SBOM too; then `component_count` of `after` minus `component_count` of `before` must equal `added_count` minus `removed_count`. A gap means a component identity the diff merged or split (duplicate names): E-CONTRACT with both outputs, no verdict until explained. First release: the check is skipped and recorded.
6. Call `shared_artifact_digest` with `path` of the SBOM. Its digest must match the SBOM digest in the build handover; a mismatch blocks, the SBOM is not the one that was built.
7. Call `supply_attestation_bundle` with `artifact_digest`, `attestations` (one entry per attestation the pipeline already verified; `signature_verified` true only when the verification output says so, never assumed) and `required_kinds` `["sbom"]` (add `"provenance"` for releases that need it). `complete` false lists `missing_kinds`; `attestation.digest-mismatch` means the attestation covers another artifact.
8. Decide and write the `sbom.bundle` (SBOM digest, validation counts, diff counts, NTIA status) and the attestation bundle result. Publish is A11's step: state exactly which command it must run (`cosign attest --type cyclonedx` or the SPDX equivalent, keyless identity of the build workflow); do not run it.

## Outputs

- `sbom.bundle`: SBOM digest, `format`, `spec_version`, `component_count`, `ntia_compliant`, diff counts, decision.
- `supply.attestation.bundle`: `required_count`, `satisfied_count`, `missing_kinds`, per-kind reason, with the cited tool output.

## Failure Handling

- INVALID_INPUT on `document` (not JSON or not a recognised format): BLOCKED needs=input; XML or tag-value SBOMs must be converted by the producer.
- PARTIAL_SUCCESS: cite `completed_fraction` and the part not checked; the bundle is not complete.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never certify a check that did not run.
- No verified signature evidence: `supply_attestation_bundle` correctly fails; that is a finding, not a tool error.
- Any request to sign, to re-sign with another key or to waive a missing attestation: BLOCKED needs=human-approval (L3 waiver, L4 key).

## Handover

- `to_agent`: A10 (consumes both bundles); write a second handover to A14 for `sbom.bundle`. `goal`: gate or patch on this evidence; `completed`: checks run; `decisions`: downgrades and NTIA gaps accepted by nobody (flagged); `artifacts`: bundles with digests (producer A18); `verification`: verified only when every cross-check agreed.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/sbom-attest-publish.md; edit the source and run scripts/build_agents.py -->
