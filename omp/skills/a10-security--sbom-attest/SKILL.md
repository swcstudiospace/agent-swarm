---
name: "a10-security--sbom-attest"
description: "Use when A11 delivers a build.artifact with an SBOM and you must attest it: diff against the last release, check pins and KEV, pin the digest."
disable-model-invocation: true
---

## When to Use
- A `build.artifact` from A11 arrives with an SBOM (Syft output per the A10 stack) and the release needs an `sbom.attestation`. Goal: SBOM and signing on 100 % of release artifacts.
- Time-based comparison of whole artifacts. A single new package before code exists is `skill://a10-security--dep-request`.

## Procedure
1. Call `shared_handover_read` with the build `task_id`. Take the SBOM path, the artifact digest, the signing evidence (Cosign) and the previous release's SBOM path (an open question if absent).
2. Call `shared_artifact_digest` with `path` set to the SBOM file (it must lie in the workspace or the swarm directory). This digest is what the attestation covers; a mismatch with the digest in A11's handover blocks attestation.
3. Read the SBOM with `read` and build `components` as `[{name, version}]` (omit `version` when the SBOM has none; at least one component).
4. Call `sec_sbom_summary` with `components`. `all_pinned` false with `unpinned` names means the SBOM is not attestable as pinned (only exact dotted-numeric versions count as pinned; ranges, wildcards and tags such as `latest` do not); keep the list.
5. Call `sec_sbom_diff` with `before` (previous release components) and `after` (this release). Report `added`, `removed`, `changed` (`from`, `to`) and `newly_unpinned`; `drift` true needs a one-line justification per entry from A11's handover or an open question. A repeated component name returns INVALID_INPUT naming the list; rename entries with their path (for example `name@path`) and retry once.
6. Call `sec_kev_match_count` with `cve_ids` (from Grype or Trivy output the assignment supplies, or from `pip-audit --format json` or `npm audit --json` run through `bash`; CVE ids only) and `kev_ids` (the KEV feed the assignment supplies; state its `data_as_of`). A match is blocking. No vulnerability data or no feed: refuse, never attest on silence.
7. Decide: attest only when the SBOM was read in full (no PARTIAL_SUCCESS), `all_pinned` is true, no KEV match, the digest matches and A11's handover shows the signature. Otherwise refuse and list the reasons.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover). Finish with `yield`: the `sbom.attestation` fields below in the result summary.

## Outputs
- `sbom.attestation`: SBOM digest, component count, drift summary, KEV result with `data_as_of`, signing evidence seen, decision `attested|refused`, reasons. The spec target is zero expired attestations at release time (A10 spec section 6); no expiry period is defined for it, so state the time of issue.

## Failure Handling
- `sec_sbom_summary` PARTIAL_SUCCESS: an SBOM not fully read is not attested.
- DEPENDENCY_UNAVAILABLE after two retries: report `skipped:tool-missing` and refuse.
- Signing is A11's step (Cosign); you verify its presence in the handover, you do not sign.
- This skill issues no gate verdict; it never touches `swarm_gate`.

## Handover
`shared_handover_write` to A12 with `goal` (SBOM attestation for the release), `completed` (summary, diff, KEV checks), `remaining` (fixes when refused), `decisions` (attested or refused with reason), `artifacts` (kind `sbom.attestation` or `sbom`, uri the SBOM path, digest from step 2, producer A11 for the SBOM), `blockers` (unpinned names, KEV ids), and `verification` {status `verified` or `partial`; checks `sec_sbom_summary:all_pinned=<bool>`, `sec_sbom_diff:drift=<bool>`}.

<!-- generated from prompts/skills/a10-security/sbom-attest.md; edit the source and run scripts/build_agents.py -->
