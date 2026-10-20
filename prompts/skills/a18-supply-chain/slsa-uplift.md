---
name: a18-supply-chain--slsa-uplift
description: Use when a build's SLSA v1.0 Build level is below the required level and you must name the gaps and specify the pipeline changes that raise it.
owner: a18-supply-chain
tools: [supply_slsa_level, supply_provenance_match, supply_attestation_bundle, shared_handover_read, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# SLSA level uplift

## When to Use

- A `ci.pipeline` or `build.artifact` produces provenance (in-toto Statement, DSSE envelope or Sigstore bundle) and the required SLSA Build level (0 to 3) is not met, or no level target exists yet.
- Verifying one artifact before promotion is `skill://a18-supply-chain--provenance-verify-on-consume`. Rotating or replacing signing keys is L4 (`signing_key_change`) and never part of this skill.
- A18 specifies what the pipeline must emit; A11 owns and changes the pipeline. Changes you propose to workflow files are L2 (`image_policy`) as a `code.patch` or a written spec, never merged by you.

## Procedure

1. Call `shared_handover_read` with the build `task_id`; take the provenance path, `artifact_digest` and the pipeline identity. Use `read` on the provenance file. No provenance file: the level is 0, go to step 6 with the full L1 list.
2. Establish the verification facts honestly. `signature_verified` is true only when a verifier ran and said so (`slsa-verifier verify-artifact` or `cosign verify-attestation`, run with `bash` when installed, with the expected builder identity and source repository). `trusted_builder_ids` comes from the task's policy list of hosted builders. `isolated_build` and `signing_key_isolated` are true only when the platform documents or attests them; unknown means false. Never default any of the four to true.
3. Call `supply_slsa_level` with `provenance` (the file text), the four facts, and `required_level` from the task (none supplied: BLOCKED needs=input; the repo default is not guessed). Read `level`, `meets_required`, `predicate_version` (`v0.2` is mapped by builder id and build type, `slsa.predicate-v0.2` info), `builder_id`, `build_type`, `subject_count` and `next_level_gaps`.
4. Decide from the `supply_slsa_level` output only: L1 means well-formed provenance (subject digest, builder id, build type, v1 `externalParameters`); L2 adds a verified signature and a builder id on the trusted list (the tool's house rule for "hosted build platform"); L3 adds an isolated build and an unreachable signing key. `slsa.l<n>-<name>` findings (major) exist only up to `required_level`; a level at or above target with `blocking_count` 0 needs no uplift.
5. Cross-check with `supply_provenance_match` using `provenance`, `artifact_digest` and `artifact_name`: `matched` must be true. A high level for a provenance whose subject is another digest is worthless: treat `provenance.digest-not-in-subjects` as level 0 and report E-CONTRACT against step 3. Then call `supply_attestation_bundle` with `required_kinds` `["provenance"]` and the verified attestation record; `complete` must agree with `signature_verified`.
6. Use `write` for the uplift spec, one line per `next_level_gaps` entry, each with the owner and the check that proves it: build only on a hosted platform with a reusable workflow (the slsa-github-generator generic or container generator, or GitHub artifact attestations produced from a reusable workflow, which is how those reach L3: confirm against the platform docs and re-assess with step 3 rather than trusting the label); pin the builder identity in the verifier; sign keyless with the workflow identity, never with a key the build steps can read.
7. After A11 ships a change, re-run steps 3 and 5 on the new provenance. The uplift is closed only when `meets_required` is true and `provenance_match` passed for the new artifact. Call `shared_artifact_digest` with the report path and provenance path.

## Outputs

- `supply.attestation.bundle`: level before and after, `required_level`, gaps with owners, the verifier output behind each fact, `provenance_match` result.

## Failure Handling

- INVALID_INPUT naming `provenance` (not a Statement, bad DSSE payload): BLOCKED needs=input for the raw file from A11.
- PARTIAL_SUCCESS or DEPENDENCY_UNAVAILABLE: cite `completed_fraction`; retry once, then `skipped:tool-missing`; no level is reported from a failed call.
- `slsa-verifier` or `cosign` missing: `signature_verified` stays false and the level is capped at L1; say so.
- Unsure how a platform implements isolation: do not claim L3; defer to the tool output and the platform's own attestation.
- Any key creation, rotation or trust-root change: BLOCKED needs=human-approval (L4).

## Handover

- `to_agent`: A11 (owns the pipeline change). `goal`: implement the gaps; `completed`: level assessment; `remaining`: each gap; `artifacts`: spec and provenance with digests (producer A18); `constraints`: no key changes; `verification`: partial until step 7. The finished bundle is consumed by A10, A12, A22 and A24.
- End with `shared_handover_validate` then `shared_handover_write`.
