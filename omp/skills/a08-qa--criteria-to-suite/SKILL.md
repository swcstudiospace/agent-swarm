---
name: "a08-qa--criteria-to-suite"
description: "Use when linted acceptance criteria are ready to automate and you must author traceable tests in the tests/ zone."
disable-model-invocation: true
---

## When to Use

The criteria passed `skill://a08-qa--criteria-preflight` (or arrived linted) and the task asks for `test.suite`. This skill authors tests; `a08-qa--suite-lint` audits an existing suite afterwards. A08 is the single writer of the `tests/` ownership zone and never edits product code.

## Procedure

1. Call shared_handover_read with `task_id`. Take `goal`, `constraints` and the `artifacts` list; if `status` is `stale`, `partial` or `malformed`, stop and follow `skill://swarm-handover-intake`.
2. Call qa_runner_detect with `scan` true. Zero runners means you can author files but must report the tier as `skipped:infra` later; `has_pytest` true means pytest naming applies.
3. With grep over the criteria file, map each AC id to one or more test names, one test per Given/When/Then branch. Regression tests with no AC use an explicit `regression` category in the name (convention), never an empty trace.
4. Write the tests under `tests/` with the write tool. Put the AC id in the docstring or a comment as written in the criteria (`AC-12`), and in the name only in snake_case (`test_ac_12_...`), because qa_name_convention rejects uppercase and hyphens; `grep -i 'ac[-_]12' tests/` then finds the trace.
5. Call qa_name_convention with `names` = every new test name, `prefix` = `test_` (or the repo's runner prefix) and `max_length` if the repo sets one. Fix each violation once.
6. Call qa_criteria_coverage with `criteria` = the AC ids and `tests` = `{name, ac_refs}` for the whole suite (regression tests with an empty `ac_refs`). `uncovered` empty is the exit condition and `orphan_tests` may contain only the named regression tests (the tool reports every test without a known AC as an orphan); report any other leftover by id.
7. For each new test file call shared_artifact_digest with `path` to get its sha256 for the handover.
8. With the bash tool run only the new files with the runner's own command (for example `python3 -m pytest -q <file>`) to prove they execute; a red test that matches a real defect is a finding for `a08-qa--defect-triage`, not a reason to weaken the test.

## Outputs

- New or changed test files, each with digest.
- Trace table: AC id to test names; `coverage_pct`, `uncovered`, `orphan_tests` from qa_criteria_coverage.
- Naming check result from qa_name_convention.

## Failure Handling

- qa_criteria_coverage INVALID_INPUT on a duplicate test name: rename, never delete the other test.
- A criterion cannot be automated (needs a human): record it as `uncovered` with the reason; do not fake a test.
- Do not touch product code; if a test needs a seam that does not exist, open a defect for the owner.

## Handover

Call shared_handover_write with `to_agent` the requesting agent (usually A01; ids like A05, not slugs), `completed` the AC ids now covered, `remaining` the `uncovered` ids, `artifacts` `{kind: test.suite, uri, digest, producer: A08}` per file, `decisions` any AC you excluded and why, `verification` status `partial` until the gate runs.

<!-- generated from prompts/skills/a08-qa/criteria-to-suite.md; edit the source and run scripts/build_agents.py -->
