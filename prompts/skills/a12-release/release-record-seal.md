---
name: a12-release--release-record-seal
description: Use when a promotion or rollback has completed and you must assemble, check and append the release.record with every evidence field present.
owner: a12-release
tools: [rel_release_record_check, shared_handover_bundle, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Seal the release record

## When to Use

- A promote reached 100 percent, or a rollback finished: the audit backbone must show what shipped, when, to where, with which evidence (spec section 7). Completeness target is 100 percent (section 6).
- One record per release. Window statistics over many releases are `skill://a12-release--delivery-metrics`.

## Procedure

1. Call `shared_handover_bundle` with `{task_id}` and take `artifacts` and `verdicts` (gate, verdict, signature, expired) for the release.
2. Assemble the record in memory from the spec fields: `release_id`, `promoted_at` (UTC, `bash` `date -u +%Y-%m-%dT%H:%M:%SZ`), `artifacts` (uri with `@sha256`), `verdicts` (per gate `<verdict>@<issue time>`, as the spec's `pass@2026-08-29T09:41Z`; the issue time is the `payload.issued_at` of the gate's verdict file, found as in step 3 of `skill://a12-release--gate-conjunction`, converted with `date -u -d @<epoch>`), `sbom_uris`, `provenance` (the attestation uri), `operator` (`A12@<instance>`, spec example `A12@leader`). For a rollback add `rolled_back_at`, `breach_at` and the `rollback.command` file path (field names are conventions of this skill, read by `skill://a12-release--delivery-metrics`).
3. Call `shared_artifact_digest` with `{path}` for each local SBOM or attestation file and put its `digest` next to the uri; registry uris stay uri only.
4. Call `rel_release_record_check` with `record` and `required_fields` = `release_id`, `promoted_at`, `artifacts`, `verdicts`, `sbom_uris`, `provenance`, `operator`. A non-empty `missing` means do not write: fix the missing evidence from its source, never fill it with a placeholder.
5. Records are append-only. Use `glob` on `release-records/<release_id>.record*.json`: when one exists, a correction is a new file `release-records/<release_id>.record.<n>.json` with `supersedes` naming the old one; never edit or delete a record. Then use `write` to create the file (a convention of this skill, not a runtime path: your guard refuses writes into `.swarm/`), and call `shared_artifact_digest` on it.
6. You hold no signing tool, so the file has no `sig`: say the record is unsigned and `verification` is `partial`; the runtime must sign and append it. Never invent a signature. A task that requires a signed record and cannot get one is BLOCKED needs=tool.
7. Tell A15 and A14 through `shared_handover_write` (both consume `release.record`: changelog, license recheck); A13 consumes it too, for suspect-change correlation.

## Outputs

- The `release.record` file (unsigned), its `complete: true` check, and its digest in the handover.

## Failure Handling

- `missing` stays non-empty after one fix: BLOCKED needs=input with the field names.
- Record file already exists: no overwrite; report the existing digest and write a superseding record only for a real correction.
- `shared_handover_bundle` `task_known` false: take verdicts from `.swarm/verdicts` by `read`, mark them unverified.

## Handover

- Two writes (a third for A13 if useful). `to_agent` A15: `goal` publish notes and changelog for `<release_id>`; A14: `goal` recheck licenses for the released set. `artifacts`: the record file with its digest, producer A12. `completed`: assembled and complete. `verification`: partial (unsigned) unless the runtime signed it.
- For each, call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.
