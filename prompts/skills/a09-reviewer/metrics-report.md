---
name: a09-reviewer--metrics-report
description: Use when review.metrics is requested on demand or periodically and latency, false-approve rate and severity mix must be reported against the spec targets.
owner: a09-reviewer
tools: [shared_handover_read, rev_review_latency_p95, rev_false_approve_rate, rev_severity_histogram, shared_status_report, shared_handover_validate, shared_handover_write]
---

## When to Use
- A `review.metrics` assignment arrives, or the schedule asks for the periodic report.
- You report on past reviews, not on one PR; none of the per-PR skills applies.

## Procedure
1. Call `shared_handover_read` with `task_id` for the period and the supplied data: review durations, approved counts, later-found defects and past finding severities. The assignment inputs carry anything the handover does not; A09 has no tool that queries past reviews.
2. Call `rev_review_latency_p95` with `durations_min` for PRs under 400 lines only (spec section 6) and `target_min` 30. Read `p95_min`, `sample_count` and `meets_target` (strictly below the target).
3. Call `rev_false_approve_rate` with `approved_count` (at least 1), `defects_found_later` and `target_pct` 2 (spec: below 2 %). Read `rate_pct` and `within_target`. `defects_found_later` above `approved_count` is INVALID_INPUT: correct the data, do not clamp it.
4. Call `rev_severity_histogram` with `severities` of the period's findings (`info`, `minor`, `major`, `critical`, `blocker`) to report the mix and `blocking_count`.
5. Compute comment precision (target at least 80 %), approved-code escape rate (at most half of the unreviewed baseline) and auto-approve precision (at least 97 %) from the counts the assignment supplies with `bash` (`python3 -c 'print(...)'`). No registry tool computes them: mark them UNVERIFIED and show the arithmetic.
6. Call `shared_status_report` with `task_id`, `kind` "done" and a one-line `step` summarising the result; use `kind` "progress" while data is being gathered.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs
- A metrics report in the task result: one line per metric with value, target and verdict, and the sample sizes behind each.

## Failure Handling
- Empty `durations_min` or zero `approved_count`: report "no data for the period"; never print a rate from nothing.
- A metric that misses its target is reported as missed; do not adjust the data.
- A registry tool DEPENDENCY_UNAVAILABLE: retry once, then report that metric as skipped.

## Handover
- Fill `completed` (metrics computed), `decisions` (target met or missed, with figures), `open_questions` (missing inputs), `verification` (`partial` when any metric is UNVERIFIED).
- Call `shared_handover_write` with `to_agent` A01.
