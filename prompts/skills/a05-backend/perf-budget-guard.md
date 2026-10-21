---
name: a05-backend--perf-budget-guard
description: Use when A03 annotated a hot-path performance budget; benchmark before and after, fail any slowdown over budget and optimise before submitting.
owner: a05-backend
tools: [shared_handover_bundle, be_perf_regression]
---

## When to Use
- The contract or blueprint carries a budget annotation for a hot path, or your change touches code on one (A05 spec section 4 rule 4: local benchmark regression above 10 % is fixed before submit).
- Non-functional only. Correctness of acceptance criteria is `a05-backend--test-first-criteria`.

## Procedure
1. Call `shared_handover_bundle` with `{task_id}` and find the annotation in `acceptance`, `handover.document.constraints` or `goal` (the contract and blueprint themselves come with the `task.assign` inputs). Its percentage becomes `max_regression_pct`; with none stated use 10. No annotation and no hot-path change means this skill does not apply.
2. Find the repo's benchmark command with `grep` (package scripts, `bench` or `benchmark` files). None found means BLOCKED needs=tool; do not invent timings.
3. Run it on the base ref in a temporary `git worktree` (`bash`, outside the repo tree) and record `before_ms` per named benchmark. Run it again on your branch for `after_ms`. Use the same machine, same input size, same iteration count.
4. Call `be_perf_regression` with `{benchmarks: [{name, before_ms, after_ms}], max_regression_pct}`. Read `regressions` (`name`, `pct`), `worst_pct` and `regressed`. A value exactly at the budget is a pass; any `pct` above it is a regression.
5. If `be_perf_regression` reports `regressed: true`, optimise only the named benchmarks' code paths, then repeat steps 3-4 until it is false. A fix that needs a contract change goes to `a05-backend--contract-deviation-request`.
6. Remove the temporary worktree with `bash` (`git worktree remove`); record the final table.

## Outputs
- A table `name | before_ms | after_ms | pct`, the budget used, and the `regressed` verdict.

## Failure Handling
- INVALID_INPUT (baseline of 0 or less, duplicate benchmark name): re-measure; a zero baseline is a broken benchmark, not a pass.
- Noisy runs: repeat each measurement and use the median of the same number of runs on both sides; say how many.
- Benchmark does not run on the base ref (new benchmark): list it as `new` in the notes and compare against the budget stated in the annotation instead.

## Handover
Numbers go into `decisions` as `{decision: "<name> <before> -> <after> ms (<pct> %)", rationale: "budget <n> %"}` of the handover that `a05-backend--pre-review-self-gate` writes. This skill sends nothing.
