---
name: "a18-supply-chain--pin-and-update"
description: "Use when GitHub Actions, images or requirements and package.json dependencies are unpinned and need pinning to commits, digests or hashes with an update path."
disable-model-invocation: true
---

# Pin and update

## When to Use

- A `ci.pipeline` or `code.patch` touches workflows, Dockerfiles, `requirements*.txt` or `package.json`, or a pin audit reports unpinned references; the output is a pinning patch and an `image.policy.report` with the pin figures.
- Not for choosing a newer base image: `skill://a18-supply-chain--base-image-refresh`. Not for Dockerfile content other than pins: `skill://a18-supply-chain--dockerfile-hardening`. Dependency upgrades for CVEs are A14's patch work.
- Pin edits are L2 (`image_policy`). A pin you cannot resolve from a source is never invented: leave it and report.

## Procedure

1. Run `python3 scripts/supply_image_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path .github --json` and again with `--path <Dockerfile dir>`. The script covers workflows and Dockerfiles only (`pin.*` and `docker.*` ids); keep `files_scanned`, `blocking_count`, `skipped`. It does not see requirements or package.json: step 2 does.
2. Use `glob` for `.github/workflows/*.y*ml`, `**/Dockerfile*`, `requirements*.txt`, `constraints*.txt`, `package.json` and the lockfiles, then `read` each. Call `supply_pin_audit` with `files` `[{path, content}]` (at most 40 files, 60000 characters each; batch more, lockfiles may be empty content so the tool knows one exists). Read `pinned_pct`, `unpinned`, `references`, `by_kind` and `files_skipped`.
3. Cross-check `supply_pin_audit` against `scripts/supply_image_lint.py`: workflow and Dockerfile `pin.*` findings must be the same set (id and line). A reference only one of them reports is E-CONTRACT with both outputs.
4. Decide by `supply_pin_audit` finding id. Major: `pin.action-not-sha` for mutable branches or third-party owners, `pin.action-no-ref`, `pin.image-latest`, `pin.action-image-unpinned` on latest, `pin.requirement-url-unpinned`, `pin.npm-not-exact` on `*`, `latest`, `next`. Minor: first-party `actions/` or `github/` owners on a tag, `pin.image-tag-only`, `pin.requirement-not-exact`, `pin.no-lockfile`. Info: `pin.requirements-no-hashes`.
5. Resolve pins from sources, read-only: an action tag to its full 40-hex commit SHA with `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha` (or `git ls-remote`) through `bash`, written `uses: owner/repo@<sha> # v1.2.3`; an image to `@sha256:<digest>` from the registry; requirements to `==` plus `pip hash` or `pip-compile --generate-hashes` output (install with `--require-hashes`); npm to exact versions with a committed lockfile (`npm ci`). A tag that resolves to an annotated tag object needs the peeled commit; if unsure, report instead of guessing.
6. Apply with `edit`, then re-run steps 2 and 1. Accept only when `unpinned` is 0 for `github-actions` and `dockerfile` entries of `by_kind` (policy of this skill: those two kinds must be 100 %), `verdict` is `pass`, and no other kind regressed. Requirements and npm may keep minor items only when a lockfile or hash file exists and the report lists them.
7. Add the update path: name the bot or workflow (Dependabot `github-actions` and `docker` ecosystems, or Renovate with digest pinning) so pins are bumped by reviewed pull requests; the config is a `ci.pipeline` change for A11. When a bump changes an SBOM, compare releases with `supply_sbom_diff` (`before`, `after`): `downgrade_count` above 0 is a major finding.
8. Call `shared_verdict` with the final finding severities (evidence only) and `shared_artifact_digest` for the patch and report.

## Outputs

- `image.policy.report`: `pinned_pct` before and after, `by_kind` table, findings left with reasons, the patch with digest.

## Failure Handling

- INVALID_INPUT on `files` (path or content missing): fix the entry once; PARTIAL_SUCCESS: cite `completed_fraction` and the unaudited files.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- `gh`, network or registry unreachable: leave that reference unpinned and report it; never write a placeholder SHA or digest.
- Script `skipped:tool-missing:pyyaml`: container options were not checked; say so.
- A pin that breaks the build (yanked version, moved tag): revert that pin and BLOCKED needs=input; do not retry the build blindly.

## Handover

- `to_agent`: A11 (owns workflows and builds). `goal`: merge the pinning patch and add the update bot; `completed`: pins applied; `decisions`: kinds allowed minor; `artifacts`: patch and report with digests (producer A18); `open_questions`: unresolved references; `verification`: verified only when step 6 passed.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/pin-and-update.md; edit the source and run scripts/build_agents.py -->
