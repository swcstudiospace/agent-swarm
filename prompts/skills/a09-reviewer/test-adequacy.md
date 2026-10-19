---
name: a09-reviewer--test-adequacy
description: Use when source files changed and the review must judge whether tests accompany them and whether the changed behaviour is actually exercised.
owner: a09-reviewer
tools: [shared_handover_bundle, rev_test_pairing]
---

## When to Use
- The changed paths include source code files, not only docs or configuration.
- The author's tests need to be judged for adequacy (decision_logic: auto-approve needs "tests present").
- Not for maintainability or style; this lens is only about tests.

## Procedure
1. Call `shared_handover_bundle` with the gated target's `task_id` to see `upstream[]` (an A08 `test.results` summary, when A08 ran: context, never A08's verdict) and the `verdicts[]` row of gate `quality`.
2. Call `rev_test_pairing` with `changed_paths` (`git diff --name-only <base>` through `bash`; `<base>` is the one the gate uses, `HEAD~1` else `origin/main`). Read `source_files`, `test_files`, `test_to_source_ratio`, `untested_sources` and `all_sources_paired`.
3. Use `read` on the tests paired with each changed source and `grep` for the changed function names. A test that never calls or asserts on the changed behaviour does not count as coverage. The gate script raises one blanket minor `coverage` finding at `tests/` when source changed and no test file did; your findings name specific untested files, so the same gap is not counted twice.
4. Rate each gap from `untested_sources` (still from the `rev_test_pairing` result): by convention of this skill an unpaired source file is `minor`, matching the script. Raise it to `major` only when the file is on a restricted path (`flagged_paths` from `a09-reviewer--risk-routing`) or the paired test cannot fail for the change. This rating rule belongs to this skill, not to the spec. Record each gap with `severity`, `summary` (including the missing test), `kind` (for example `TEST.unpaired`), `location` (`path:line` of the changed code) and `evidence`, in your notes for `a09-reviewer--verdict-assembly`.

## Outputs
- Adequacy findings (`minor` or `major`) naming each untested source, with `location` and the missing behaviour.
- `tests_present` (true when the diff includes a test file) for the auto-approve decision.

## Failure Handling
- `rev_test_pairing` INVALID_INPUT: pass only path strings; correct the field named.
- A08 results absent: say so under UNVERIFIED; do not infer that tests passed.
- Never write or edit tests (A09 has no write or edit tool); suggest them in the finding.

## Handover
- No handover of its own: the findings feed `a09-reviewer--verdict-assembly`.
- On cancel or pause, hand over per `swarm-cooperative-cancel`: `completed` (sources checked), `decisions` (each rating with its reason), `open_questions` (behaviour with no test), `verification` (`partial` when A08 results were missing).
