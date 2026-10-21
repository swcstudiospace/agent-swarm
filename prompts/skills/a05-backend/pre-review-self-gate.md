---
name: a05-backend--pre-review-self-gate
description: Use when implementation is done and before A09 review; run every check, shape the code.patch JSON, digest it and hand over only if it is ready.
owner: a05-backend
tools: [shared_toolchain, shared_check_plan, shared_scratchpad_get, shared_artifact_digest, be_patch_shape_check, shared_handover_validate, shared_handover_write]
---

## When to Use
- All acceptance criteria have tests and the code is complete, immediately before the task would enter IN_REVIEW (prompts/A05-backend.md `<OPERATING_PROCEDURE>` steps 2, 5 and 6).
- Whole-patch gate. Route-only checks are `a05-backend--route-surface-hygiene`; changes after a review are `a05-backend--review-rework-loop`.

## Procedure
1. Call `shared_toolchain` with `{scan: true}`, then `shared_check_plan` with `{scan: true}`; note the `python` and `js` flags and the check names in `names`.
2. Run `python3 scripts/code_checks.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json`. Any `fail` in `results` means not ready; `skipped:tool-missing` is recorded as such. Do not let the `bun scripts/ts/code_checks.ts` twin gate anything: it only reports which toolchains are present (`checks[].status`) and runs no linter or test.
3. Run `python3 scripts/be_contract_conformance.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --contract <contract file> --prefix <mount prefix> --json`. `status: fail` (`missing` routes or an unreadable contract) means not ready; the bun twin is a pass-through to the same script.
4. Call `shared_scratchpad_get` with `{correlation_id, key: "a05.contract-gaps"}`. When `found` is true, every recorded gap must be closed or have a deviation request; `found: false` means the baseline skill did not run, so rely on step 3.
5. Check signatures with `bash` `git log --format=%G? <base>..HEAD`; every line must be `G` (or `U` when the repo's trust is not configured, say so). Save the diff with `bash` (`mkdir -p .swarm/patches`, `git diff <base>...HEAD` into `.swarm/patches/<task_id>.patch`: a path convention of A05 skills, `.swarm/` is git-ignored) and call `shared_artifact_digest` with `{path}`; its `digest` becomes the patch `digest`.
6. Assemble the `code.patch` object of the A05 `<output_format>`: `task_id`, `branch`, `commits_signed`, `contracts_bound` (`Name@MAJOR.MINOR.PATCH`), `tests_added` (above 0), `digest`, `checks` (check name to `pass`, `fail` or `skipped[:reason]`, built from steps 2 and 3), plus `pr_url`, `coverage_delta_pct`, `sbom_uri`, `dependency_requests` when known. Call `be_patch_shape_check` with `{patch}`. Both `valid` and `ready_for_review` must be true; fix each `problems[].field`. The tool accepts `branch` as `swarm/<task_id>` or, in a Programming Desk repo, the seat-prefixed `bot-0N-<seat>/<task_id>` (N 0-6, seat in lowercase a-z, 0-9, `-`); any other branch is a `branch` problem to fix, not to edit away.
7. Call `shared_handover_validate` with `{document, draft: true}` and fix every `errors` entry, then `shared_handover_write` to A09 (`skill://swarm-handover-emit`).

## Outputs
- The `code.patch` object, its digest, a handover to A09, and the final `task.result` (yielded under omp; `state: IN_REVIEW`, `outputs: [{kind: "code.patch", uri, digest}]`); or a state that is not IN_REVIEW with the failing check named.

## Failure Handling
- Any `fail` in steps 2-3 or `ready_for_review: false`: do not emit IN_REVIEW; fix, rerun the failed step and everything after it.
- `commits_signed: false`: if you cannot sign the commits, BLOCKED needs=tool; never edit the field and never touch signing material.
- Tool outages: retry twice, then report `skipped:tool-missing`; a check required for review and missing is FAILED.

## Handover
Fills `goal`, `completed`, `artifacts` (`{kind: "code.patch", uri, digest, producer: "A05"}`), `verification` (status from the check results, `checks` listing each), `constraints` (pins), `remaining` (empty when ready). Validated with `shared_handover_validate`, sent with `shared_handover_write` to A09.
