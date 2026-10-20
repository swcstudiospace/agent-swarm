---
name: "a18-supply-chain--reproducible-build-check"
description: "Use when two builds of one commit must yield the same artifact digest and any difference must be traced to its cause in SBOM, pins or timestamps."
disable-model-invocation: true
---

# Reproducible build check

## When to Use

- Two builds (A and B) of one source commit exist, by A11's pipeline or two builders, and the question is whether the artifact digests are identical (bit-for-bit reproducible) or why not. Output: an `image.policy.report` with the reproducibility result.
- Not for checking that one artifact matches its provenance: `skill://a18-supply-chain--provenance-verify-on-consume`. Not for rebuilding after a compromise: `skill://a18-supply-chain--builder-compromise-response`.
- Reading and comparing is L2 (`image_policy`). Building is A11's job; you only run a local rebuild with `bash` when the task authorizes it and the build is hermetic (no registry push, no secrets). Fixing the pipeline is a `ci.pipeline` request, not your edit.

## Procedure

1. Call `shared_handover_read` with the build `task_id` of each build. Take for A and B: commit sha, builder id, artifact digest (`sha256:<hex>`), SBOM path, provenance path, Dockerfile path. Different commits or different Dockerfile bytes are not a reproducibility test: compare their `shared_artifact_digest` and stop with BLOCKED needs=input when they differ.
2. Compare the two digests with `bash` (plain string equality). Equal: go to step 7 (reproducible). Different: continue; the two artifacts are never both "the" release until explained.
3. Call `supply_provenance_match` twice with each build's `provenance`, `artifact_digest` and `artifact_name`; both `matched` must be true. A false result is a provenance problem, not non-reproducibility: report it and do not analyse further.
4. Call `supply_sbom_diff` with `before` the SBOM of A and `after` the SBOM of B. A non-zero `version_changed_count`, `added_count` or `removed_count` is dependency drift: a floating version or fresh resolution made the builds differ. Name each component from `version_changed` (with its `direction`). `verdict` `unchanged` means same components, so the difference lies in bytes, not content.
5. Find the drift sources. Run `python3 scripts/supply_image_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <Dockerfile dir> --json` and call `supply_pin_audit` with `files` `[{path, content}]` for the Dockerfile, workflows and lock or requirements files. Cross-check: the same unpinned bases and actions in both; every `pin.image-latest`, `docker.latest-tag`, `pin.requirement-not-exact` or `pin.npm-not-exact` is a candidate cause; a tag-only base can move between builds.
6. With identical SBOMs and clean pins the remaining causes are timestamps, file ordering, build paths, and archive metadata. Use `bash` read-only: `diffoscope` on the two OCI layouts if installed, otherwise compare layer digests (`skopeo inspect --raw`, `crane manifest`) to find the first differing layer. Recommended controls to state, not apply: `SOURCE_DATE_EPOCH` set to the commit time, BuildKit timestamp rewriting for the exporter (`rewrite-timestamp=true`, which needs a recent BuildKit: confirm with `docker buildx version`), pinned toolchain versions, a fixed build path. Report the first differing layer as evidence; if `diffoscope` is missing, say the cause is unproven.
7. Conclude: `reproducible` (digests equal, both provenance matched), `explained` (differences traced to named components or pins, with evidence), or `unexplained` (no cause found). Call `shared_artifact_digest` for the report file and the two SBOM diff outputs saved with `write`.

## Outputs

- `image.policy.report`: result (`reproducible`, `explained`, `unexplained`), both digests, diff counts, drift sources with finding ids, controls to adopt, evidence files with digests.

## Failure Handling

- INVALID_INPUT naming `before`, `after` or `provenance`: fix once (valid SBOM JSON, digest as `algorithm:hex`), else BLOCKED needs=input.
- PARTIAL_SUCCESS: cite `completed_fraction`; a partial diff cannot prove equality.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; `diffoscope` or `skopeo` missing is `skipped:tool-missing`, result `unexplained`.
- Do not claim reproducibility from equal SBOMs alone: only equal digests prove it.
- Differences in an artifact already released: stop and report to A12 and A10; never re-tag or overwrite a digest.

## Handover

- `to_agent`: A11 (owns the build). `goal`: adopt the controls and rebuild; `completed`: comparison; `decisions`: result and causes; `artifacts`: report and diff files with digests (producer A18); `open_questions`: unproven causes; `verification`: verified for `reproducible`, partial otherwise.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/reproducible-build-check.md; edit the source and run scripts/build_agents.py -->
