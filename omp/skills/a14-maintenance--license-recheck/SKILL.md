---
name: "a14-maintenance--license-recheck"
description: "Use when a release.record is published and licence drift must be re-checked; flag copyleft and unapproved licences to A10 and verify the release entry."
disable-model-invocation: true
---

# Licence re-check per release

## When to Use

- A12 appends a `release.record` and the dependency set changed since the previous release (A14 spec section 7: licence re-check on every bump, SBOM per release).
- Not for the per-PR check inside one bump (`a14-maintenance--bump-merge`).

## Procedure

1. Use `read` on the `release.record` and the SBOM it lists (`sbom_uris`). This skill does not generate an SBOM; if the record lists none, record `sbom missing` and go to step 5.
2. Call `shared_artifact_check` with `{task_id, outputs: [{kind, uri, digest, producer}], agent: <producer id on the SBOM entry>}`. `ok: true` and `verified: true` confirm that the file exists, the digest matches and that agent produced it; a result status `no_digest` or `unchecked` (for example a URI without a digest) is not a confirmation. Call `shared_artifact_digest` with `{path}` on the local SBOM and on any other file you cite, and record the digest.
3. Diff the SBOM against the previous release's SBOM by `grep` or `read`; list each dependency whose version or declared licence changed, with both SPDX expressions.
4. Call `maint_license_change` with `{previous_license, current_license, allowlist}` for every changed dependency. The allowlist is the SPDX list approved in the task constraints (at least one entry); without it ask through `open_questions`, never assume one. `requires_review` or `copyleft_introduced` true makes a finding.
5. Call `maint_changelog_check` with `{entry: <markdown of the release section>, version: <release version>}`; `valid: false` becomes a finding of the release-hygiene kind with `first_problem` as evidence.
6. Write the findings list: dependency, old licence, new licence, `copyleft_introduced`, allowed or not. A licence expression the tool cannot parse (`INVALID_INPUT` on `current_license`) is a finding itself, never a pass.
7. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- Licence findings for A10: one per changed or unapproved dependency, with evidence and the digest of the SBOM used.
- A clean statement (`no licence drift`) only when every changed dependency came back allowed.

## Failure Handling

- No previous SBOM to diff: compare against the allowlist only and say the diff was not possible.
- `INVALID_INPUT` on `previous_license` (no SPDX id found): treat the dependency as unknown licence, a finding.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry once, then list the unchecked dependencies; never report a partial list as complete.

## Handover

- Fill `goal` (A10 reviews these licence findings), `completed` (dependencies checked), `remaining`, `decisions` (allowlist used and its source), `artifacts` (SBOM with digest), `open_questions` (missing allowlist or SBOM), `verification`.
- `shared_handover_write` to `A10`. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/license-recheck.md; edit the source and run scripts/build_agents.py -->
