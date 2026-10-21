---
name: a05-backend--hotfix-minimal-patch
description: Use when A14 assigns a hotfix with a defect.report; reproduce with a failing test, fix the root cause with the smallest diff and prove no coverage drop.
owner: a05-backend
tools: [shared_handover_bundle, be_acceptance_coverage, be_coverage_delta, shared_artifact_digest, shared_handover_write]
---

## When to Use
- An A14 task carries a defect (a `defect.report` or an incident with a root-cause lead) for owned backend code (A05 prompt `<domain>`: minimal-diff hotfix patches for A14; `<constraints>`: hotfixes are the smallest change that fixes root cause).
- A feature uses `a05-backend--test-first-criteria`; review comments use `a05-backend--review-rework-loop`.

## Procedure
1. Call `shared_handover_bundle` with `{task_id}` and take the defect id(s), reproduction steps and root-cause lead from `goal`, `acceptance` and `handover.document` (empty when no handover exists; then use the `task.assign` inputs).
2. Write a failing repro test named after the defect id. Run it with `bash`; it must fail for the reported symptom, not for an import or syntax error. Keep the failure output.
3. Call `be_acceptance_coverage` with `{criteria: [<defect ids>], covered: [<ids with a repro test>]}`; `uncovered` and `unmapped` must be empty before the fix.
4. Change the smallest amount of code that removes the root cause, not just the symptom. Run `bash` `git diff --stat`; revert every hunk not needed for the defect. No refactor, no dependency bump, no contract or schema change.
5. Run the repro test (now passing) and `python3 scripts/code_checks.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json`; every value in `results` is `pass` or `skipped:tool-missing`.
6. Measure coverage of the touched files before and after with the repo's reporter (`bash`), then call `be_coverage_delta` with `{before, after}`. `regressed` must be false.
7. When the fix touches routes, run `python3 scripts/be_contract_conformance.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --contract <contract file> --json`; `status: fail` means not ready (without `--contract` it only inventories routes).
8. Save the diff with `bash` to `.swarm/patches/<task_id>.patch` (`mkdir -p .swarm/patches` first; a path convention of A05 skills, `.swarm/` is the git-ignored swarm state dir) and call `shared_artifact_digest` with `{path}`. Assemble and shape-check the `code.patch` with steps 5 and 6 of `a05-backend--pre-review-self-gate`, then call `shared_handover_write` to A14.

## Outputs
- Repro test, patch with the smallest diff, `diff --stat` line, root-cause sentence, coverage delta, digest.

## Failure Handling
- The repro cannot be made to fail: stop, report BLOCKED needs=input with what you tried; do not patch blind.
- The root cause is in the contract or schema: record it and use the deviation skill (contract) or hand to A07 (schema, `to_agent: "A07"`); never edit schema.
- Fix grows beyond the root cause: split it and send only the minimal part.
- No coverage reporter: record `coverage: skipped:tool-missing`; never estimate numbers.

## Handover
`decisions` holds the root cause and why this fix; `completed` the repro test and checks; `artifacts` the patch (`{kind: "code.patch", uri, digest, producer: "A05"}`); `remaining` any follow-up that is out of scope; `verification.checks` the step 5 to 7 results. Sent to A14 with `shared_handover_write` (validation as in `skill://swarm-handover-emit`).
