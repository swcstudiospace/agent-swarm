---
name: a08-qa--suite-lint
description: Use when an existing test suite needs a static audit for orphan tests, unexplained skips, bad names and rerun-hidden failures, with no new code written.
owner: a08-qa
tools: [qa_criteria_coverage, qa_name_convention, qa_skip_ratio, qa_retry_budget, qa_finding_shape_check, shared_handover_write]
---

## When to Use

You inherit a suite (yours from an earlier run, or a producer's `tests/` changes) and must say whether it is fit to gate on. Spec decision rule 1: every test traces to at least one criterion; orphan tests fail the suite lint. This skill audits statically from the files and the last run report; it does not author tests (`criteria-to-suite`) and does not judge flake history (`flake-quarantine`).

## Procedure

1. Collect the facts: `grep` for `AC-` references across `tests/`, list each test with its `ac_refs`, and take the criteria ids from the task's `acceptance`.
2. Call qa_criteria_coverage with `criteria` and `tests` (criteria and test names unique). `orphan_tests` are lint failures unless the test is named as an explicit regression-category test (the tool reports every test without a known AC as an orphan); `uncovered` ids are coverage findings.
3. Call qa_name_convention with `names` = all test names, `prefix` = the runner prefix and `max_length` if the repo sets one. Each `violations` line becomes a minor finding.
4. Parse the last run report into `{name, status, reason}` (status `passed`, `failed` or `skipped`) and call qa_skip_ratio with `max_skip_pct` set from the task or, if none, 5. `unexplained` skips (no reason) are findings regardless of the ratio.
5. If the report records rerun attempts, call qa_retry_budget with `runs` = `{test, attempts[]}` and `max_reruns` 2. `unresolved` tests never passed; `rescued` tests passed only after a retry and are hand-offs for `skill://a08-qa--flake-quarantine`.
6. Express each problem as a finding `{id: QF-n, severity, kind: coverage, ac_ref, evidence}`: orphans and unexplained skips are `minor` unless an AC is `uncovered`, which is `major`. Check the list with qa_finding_shape_check and fix every violation it names.

## Outputs

- Lint findings (kind `coverage`) with test or AC ids as evidence.
- Counts: criteria, covered, `coverage_pct`, orphans, `skip_pct`, unexplained skips, rescued, unresolved.
- A conforming flag from qa_finding_shape_check.

## Failure Handling

- No run report exists: skip steps 4 and 5, say so, and keep the coverage and naming audit.
- qa_skip_ratio needs `max_skip_pct`; if the task gives none use the 5 default and say it is a default.
- Never delete or rewrite a test during lint; propose the change to the owner.

## Handover

Call shared_handover_write to the suite owner (A05, A06 or A07 by the file's zone) or A01 if mixed: `completed` the checks done, `remaining` each finding id with its fix, `artifacts` none unless you saved the report, `verification` status `verified` with the tool outputs summarised in `checks`.
