---
name: a05-backend--test-first-criteria
description: Use when implementing acceptance criteria; write a failing test per criterion first, prove every criterion is covered, keep changed-code coverage at 80%.
owner: a05-backend
tools: [shared_handover_bundle, be_acceptance_coverage, be_coverage_delta, shared_dod_check]
---

## When to Use
- Before implementing each acceptance criterion of a feature task (A05 spec section 4 rule 2: a task without tests cannot enter IN_REVIEW; coverage on changed code at least 80 %).
- Feature work only. A defect fix starts from a defect id (`a05-backend--hotfix-minimal-patch`); the final readiness check is `a05-backend--pre-review-self-gate`.

## Procedure
1. Call `shared_handover_bundle` with `{task_id}` and take `acceptance` (strings) as the criteria list. Use each criterion's own id when its text starts with one (`AC-1: ...`); otherwise assign `AC-1`, `AC-2`, ... in order and keep that label in every later step.
2. For each criterion write one failing unit test whose name or docstring carries the id. Run only that test with `bash`; it must fail for the missing behaviour, not for a syntax or import error. Quote the failure line in your notes.
3. Call `be_acceptance_coverage` with `{criteria: [<all ids>], covered: [<ids that have a test>]}`. `uncovered` and `unmapped` must both be empty before you write production code.
4. Implement the smallest code that makes those tests pass, then rerun them with `bash`.
5. Run the repo coverage reporter with `bash` on the changed files, once on the base ref and once now. Call `be_coverage_delta` with `{before, after}`; `regressed` must be false, and `after` itself must be at least 80 (the tool reports the delta, so compare the number yourself).
6. Call `shared_dod_check` with `{result: {completed: ["AC-1: tests/..::test_x passes", ...], verification: {checks: [...]}}, criteria, task_id}`; evidence is matched by `AC-n` reference or by keywords in `summary_md`, `completed` and `checks`. `all_met` must be true; every `unknown` needs evidence or an explicit unmet note.

## Outputs
- Tests, one per criterion, each seen failing then passing; `covered_count` out of `total_count`; coverage numbers `before` and `after`.

## Failure Handling
- A criterion that cannot be tested as written: do not weaken it. End BLOCKED needs=input naming the criterion (A01 routes it to A02, owner of `acceptance.criteria`) and leave it in `uncovered`.
- `unmapped` non-empty: a test points at an id that is not in the task; fix the id.
- INVALID_INPUT from `be_acceptance_coverage` (empty criteria): BLOCKED needs=input.
- No coverage reporter in the repo: say `skipped:tool-missing`; never estimate a number.

## Handover
Fills `completed` (criteria with passing tests) and `verification.checks` (`AC-n: tests/...::test_x`), `remaining` (uncovered ids). The final `shared_handover_write` is sent by `a05-backend--pre-review-self-gate`; this skill only calls `shared_dod_check`.
