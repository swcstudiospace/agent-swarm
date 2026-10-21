---
name: "a05-backend--dependency-bump-l2"
description: "Use when A10 or A14 asks to update a package already in the manifest; apply it only if be_dependency_class says L2, then prove license, range and tests."
disable-model-invocation: true
---

## When to Use
- An A10 or A14 task names an existing dependency and a target version (A05 prompt `<domain>`: bumps requested by A10/A14; spec section 4 rule 3: allowlisted semver-compatible update is L2).
- Not for a package the manifest lacks (`a05-backend--new-dependency-request`) and not for breaking upgrades, which stop at step 2.

## Procedure
1. Call `shared_handover_read` with `{task_id}` for the requested `package`, target version, allowlist and reason in `handover.goal` and `handover.constraints`. A `status` other than `ok`, or no handover, means BLOCKED needs=input (use the `task.assign` inputs only if they carry the same four facts).
2. Call `be_dependency_class` with `{manifest, proposed: [{package, version}], allowlist}`. `manifest` maps every package name to its current `MAJOR.MINOR.PATCH` (read the resolved version from the lockfile; a range such as `>=1.2` is INVALID_INPUT) and `version` is the target in the same form. Proceed only on `level: L2` with `change` of `patch` or `minor`. `unchanged` means nothing to bump. `major`, `downgrade`, `new` or the reason `package is not on the allowlist` (all L3) stop here: report the `reason` to the requester, no edits.
3. Call `be_semver_range_class` with `{range}` for the range you will write into the manifest; `upper_bounded` must be true.
4. Call `be_license_allowlist_check` with `{packages: [{name, license}], allowlist: [<license ids>]}` using the license the target version declares (from its registry metadata or its package files); a license change can fail even an L2 bump. An expression with parentheses fails closed.
5. Change only that package's manifest and lockfile entries with the repo's package manager through `bash`. Do not touch unrelated entries or source.
6. Run `python3 scripts/code_checks.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json`; every value in `results` must be `pass` or `skipped:tool-missing`.
7. Measure coverage on the changed code before and after with the repo's reporter (`bash`), then call `be_coverage_delta` with `{before, after}` (both 0 to 100). `regressed` must be false.

## Outputs
- A diff limited to the manifest and lockfile, plus a bump table: `package old -> new`, `change`, `level`, license verdict, `delta`.

## Failure Handling
- Tests fail after the bump: revert the bump, report the failing check; do not edit tests or source to compensate.
- `be_dependency_class` INVALID_INPUT: fix the named field once, otherwise BLOCKED needs=input.
- Lockfile cannot be regenerated offline: BLOCKED needs=tool.
- No coverage reporter: record `coverage: skipped:tool-missing`; never estimate numbers.

## Handover
`completed` holds the bump table; `verification.checks` the `code_checks` results and the coverage delta; `decisions` any version held back and why; `artifacts` the changed manifest and lockfile (digests as in `skill://swarm-handover-emit`). Sent with `shared_handover_write` to the requester (A10 or A14); the readiness of the patch itself is `a05-backend--pre-review-self-gate`.

<!-- generated from prompts/skills/a05-backend/dependency-bump-l2.md; edit the source and run scripts/build_agents.py -->
