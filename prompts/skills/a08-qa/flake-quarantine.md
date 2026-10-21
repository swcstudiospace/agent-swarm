---
name: a08-qa--flake-quarantine
description: Use when run history shows tests that pass and fail intermittently and each needs a quarantine, watch or consistent-fail decision.
owner: a08-qa
tools: [qa_flaky_classify, qa_retry_budget, shared_scratchpad_put, shared_handover_write]
---

## When to Use

You hold per-test run histories (several clean runs) and some tests flip between pass and fail. The flake policy (A08 spec decision rule 4): a test failing intermittently in at least 3 of the last 10 clean runs is quarantined, never deleted, with a fix task to A05, A06 or yourself. This skill decides per test and issues no verdict; a flaky test does not by itself turn the gate green or red.

## Procedure

1. Build `histories` as `{test, outcomes[]}` (names unique) with outcomes `pass`, `fail` or `skip`, oldest first, from the run reports or CI logs the task names and you can read with the read tool; there is no fixed history path.
2. Call qa_flaky_classify with `histories`. Buckets over the last 10 non-skipped runs: `quarantine` (3 or more fails with passes present), `watch` (fewer fails with passes present), `consistent_fail` (no pass in the window: a real defect, not a flake), `unobserved` (only skips); all-pass tests are counted in `stable_count`.
3. Call qa_retry_budget with `runs` = `{test, attempts[]}` (`pass`/`fail`, names unique) from the latest run's retry log and `max_reruns` 2 (convention; the tool accepts 0 to 10). `over_budget` tests burn reruns even when they finally pass; `rescued` ones passed only on retry; `unresolved` never passed.
4. From the qa_flaky_classify buckets decide each test: `quarantine` tests go on the list `flake_quarantined` (keep the test file, mark it with the runner's own skip or xfail marker plus a comment naming the fix owner, never delete); `watch` tests get no action beyond a note; `consistent_fail` tests go to `skill://a08-qa--defect-triage`.
5. Name the owner for each quarantined test from the path: product tests in `src/` zones go to A05 (backend) or A06 (frontend), test-only fixtures to A08 itself; state the suspected cause only if evidence shows it (ordering, time, network), else write `cause unknown`.
6. Call shared_scratchpad_put with the task's `correlation_id`, `key` `flake_quarantined` (convention) and `value` `{tests: [...], fix_owner: {...}, as_of: <date>}` so the gate-run skill and later runs read one list.

## Outputs

- `flake_quarantined[]` (test ids), `watch[]`, `consistent_fail[]` with counts evaluated and `stable_count`.
- Per quarantined test: evidence (fail/pass counts), owner, fix-task note.

## Failure Handling

- Fewer than 10 runs: the classifier still evaluates what exists; say the window is short and avoid quarantining on 3 fails of 4 runs without a second look.
- Histories you cannot read: no quarantine; report `unobserved` and BLOCKED `needs=input` for the run reports.
- Never hide a flaky failure by rerunning until green and reporting `pass`.

## Handover

Call shared_handover_write to A01 (it opens the fix tasks; there is no task-creation tool for A08) with `goal` "fix the quarantined tests", `remaining` one line per test with its fail count and fix owner (A05, A06 or A08), `constraints` "tests stay in the suite, marked, until fixed", `verification` status `verified`.
