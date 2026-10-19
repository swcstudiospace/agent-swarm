---
name: "a08-qa--runtime-budget"
description: "Use when suite durations approach the 45 minute P95 budget or gate latency rises, to choose an impact subset or request lane capacity."
disable-model-invocation: true
---

## When to Use

Recent suite durations are near or above 45 minutes, or verdicts for routine PRs are slower than the 30 minute P95 target. Spec section 5: adaptive selection by failure history; escalate to A01 for lane capacity when P95 runtime exceeds 45 minutes. This is a capacity question, not a correctness one; it never changes a verdict by itself and never drops a tier the risk class requires.

## Procedure

1. Collect `durations_s` (seconds per full suite run, newest 20 or more) from the run reports the task names, with the read tool; there is no fixed report path.
2. Call qa_runtime_p95 with `durations_s` (and `budget_s` only if the task sets one other than 2700). Read `p95_s`, `mean_s`, `max_s` and `within_budget`.
3. If qa_runtime_p95 reports `within_budget` true and `p95_s` is under 80 percent of the budget (convention), record the figures and stop.
4. Otherwise find the cost: group tests by duration from the last report and name the slowest ten. Call qa_skip_ratio with the report's `tests` (`{name, status, reason}`, names unique) and `max_skip_pct` 5 (convention) to check that the suite is not already hiding skipped work, which would flatter the timing.
5. Pick an impact subset: tests whose files or ACs are touched by the change plus every test that failed in the last five runs, from `grep` over the diff and the failure history. Required-tier tests of the risk class stay in; only optional-tier or unrelated slow tests are deferred, and the full suite stays mandatory for `risk_class=high` and release candidates.
6. Call qa_runtime_p95 again with the durations of runs made with the chosen subset; if `p95_s` is still over 2700 (`within_budget` false), call shared_escalate with `task_id`, `reason_code` `budget`, a `summary` with the P95 and the subset size, and `needs` `["lane capacity"]`; the response yields the BLOCKED patch for A01. With no subset run yet, say the figure is unmeasured and do not escalate on an estimate.

## Outputs

- Duration stats and `within_budget`.
- Chosen subset: criteria for inclusion, count of tests kept and deferred.
- A capacity request when over budget.

## Failure Handling

- Fewer than 5 samples: report figures with `sample_count` and say P95 is not stable; do not escalate on it.
- qa_runtime_p95 INVALID_INPUT on empty `durations_s`: BLOCKED `needs=input` for run reports.
- Never use a subset to produce a verdict for a high-risk gate.

## Handover

Call shared_handover_write to A01: `goal` "decide lane capacity or accept the subset", `decisions` the subset rule with rationale, `remaining` the deferred tests, `constraints` "full suite still required for high risk and release candidates", `verification` status `verified` with the P95 figure in `checks`.

<!-- generated from prompts/skills/a08-qa/runtime-budget.md; edit the source and run scripts/build_agents.py -->
