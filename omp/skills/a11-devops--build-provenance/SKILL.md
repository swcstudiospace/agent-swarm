---
name: "a11-devops--build-provenance"
description: "Use when a code.patch is ready to build and a build.artifact record must be emitted and self-gated on its provenance attestation before anyone consumes it."
disable-model-invocation: true
---

# Build provenance

## When to Use

- A `code.patch` is ready and the pipeline has built it; you must emit `build.artifact`.
- Pipeline law (agents/A11-devops.md section 4.2): a `build.artifact` without a provenance attestation is rejected, fail closed. This skill is the single emitter of that record.

## Procedure

1. Call `shared_git_head` with `{"scan": true}` and note `sha` and `branch`.
2. Call `devops_build_systems` with `{"scan": true}` and note `systems` (comma-separated; `build_systems` must name the same set).
3. Run `python3 scripts/devops_build_record.py --task-id $TASK --correlation-id $CORR --json --image <oci-ref-with-@sha256-digest> --version <semver>` and the `bun scripts/ts/devops_build_record.ts` twin with the same flags. The JSON holds `record` (`sha`, `branch`, `dirty`, `tree_digest`, `build_systems`, `image`, `provenance` with `builder` and `reproducible`), `path` (the file `.swarm/artifacts/<sha>.json`), `registered` and `warnings`.
4. Compare `record.sha`, `record.branch` and `record.build_systems` with the `shared_git_head` and `devops_build_systems` results. A mismatch means the tree moved: stop and rebuild from the new HEAD.
5. The script emits no `provenance.attestation`, and your guard refuses writes into `.swarm/`, so never edit that file. Build the final record object as `record` plus `provenance.attestation` set to the URI the pipeline's attestation step produced (SLSA target); never invent one. If the pipeline made none, leave it out and let `devops_provenance_gate` reject.
6. Call `devops_provenance_gate` with `{"record": <the final record object>}`. `reject` lists `missing[]`: `provenance.attestation`, `tree_digest` (not `sha256:<64 hex>`), or `image` (an image was supplied but is not `@sha256` digest-pinned). Do not hand over a build.artifact; report BLOCKED. `warnings[]` (`dirty-tree`, `dirty-unknown`, `not-reproducible`, `builder-missing`, `image-absent`) go into the result and the verification checks.
7. Call `shared_artifact_digest` with `{"path": "<path from step 3>"}`, then `shared_artifact_check` with `{task_id, outputs: [{kind: "build.artifact", uri: "<path>", digest, producer: "A11"}]}` for that local file only. Require `failed` 0 and `verified` true. The attestation URI is not a local file (it would come back `unchecked`), so keep it out of this check and list it under `artifacts` in the handover as kind `provenance.attestation` (a convention that `skill://a11-devops--deploy-gate` and A12's provenance precheck read) with the digest the pipeline reported; say it is unchecked.
8. Emit exactly one fenced `json` block in the prompt's `build.artifact` shape (the final record from step 5), then call `shared_handover_validate` and `shared_handover_write` (see Handover).

## Outputs

- `build.artifact` record with `sha`, `tree_digest`, `image`, `build_systems`, `provenance` and the gate `decision`.
- `.swarm/artifacts/<sha>.json` (the script's record, without the attestation) with its digest.

## Failure Handling

- `decision` reject: no hand-off; BLOCKED `needs=input` naming each `missing` field.
- Script missing or `git` absent: the record reports `skipped:tool-missing`; the gate decides, never claim reproducible.
- `shared_artifact_check` `failed` above zero: rebuild the record once, then BLOCKED.
- DEPENDENCY_UNAVAILABLE on any tool: retry twice, then fail closed and say which check was skipped.

## Handover

Fill `goal` (consume this build.artifact), `completed`, `artifacts` (record path, digest, producer A11; attestation URI), `decisions` (gate `decision` with warnings), `constraints` (image is digest-pinned), `verification` (`verified` only when the gate accepted and `verified` is true). Call `shared_handover_validate`, then `shared_handover_write` to A12 (release) and, when A10 asked for the SBOM check, to A10.

<!-- generated from prompts/skills/a11-devops/build-provenance.md; edit the source and run scripts/build_agents.py -->
