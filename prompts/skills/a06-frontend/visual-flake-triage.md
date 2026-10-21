---
name: a06-frontend--visual-flake-triage
description: Use when a visual or component test fails intermittently; rerun it twice, quarantine (never delete) a flake, and send a baseline change to A04 for re-approval.
owner: a06-frontend
tools: [shared_handover_read, shared_attempt_history, shared_open_findings, shared_open_questions, shared_artifact_digest, shared_handover_write]
---

## When to Use
- One component or visual test fails on some runs and passes on others, or a screenshot diff appears with no code change to that component (A06 spec section 5: "component-level retry x2 then quarantine; baseline re-approval requires A04"; prompt error_handling: "never delete").
- Flakiness only. A test that fails every time is a real failure: fix it, or take it through `a06-frontend--rework-intake`.

## Procedure
1. Call `shared_handover_read` with `{task_id}` and list the tests and snapshot baselines you delivered (`artifacts` of kind `component.tests`). With `status` other than `ok`, list them from `git status` and the story and test files you wrote.
2. Call `shared_attempt_history` with `{task_id}` and read `last_error` and the failing transitions, so you know whether the task failed before. It is task-level: the failing test itself comes from the run output.
3. Use `bash` to rerun only that test twice with the repo's component-test command (read it from `package.json` scripts; `shared_check_plan` gives only check names). Record each run as pass or fail with its output line. Two failures out of two reruns: a real failure, stop this procedure. A pass on any rerun: flaky.
4. For a flake use `edit` to quarantine it, never delete it: mark it with the framework's skip or fixme form, keep its name and file, and add a comment with the date, the run counts and the suspected cause (timing, animation, font load, network stub). List it in the quarantine list.
5. For a screenshot difference that is intended, do not update the baseline yourself. Call `shared_artifact_digest` with `{path}` of the diff image, then `shared_open_questions` with `{task_id}`; when no baseline question to A04 is open, call `shared_handover_write` with `{task_id, to_agent: "A04", goal, open_questions: ["approve new baseline for <test>?"], artifacts: [{kind: "visual.diff", uri: <path>, digest, producer: "A06"}], remaining: ["rebaseline after approval"]}` (`visual.diff` is this skill's artifact kind).
6. Call `shared_open_findings` with `{task_id}` and note any finding that already names the test, so the quarantine entry cites its id. An empty list proves nothing during a rework (see `a06-frontend--rework-intake`).

## Outputs
- A quarantine list (test, file, run counts, suspected cause), the real failures routed away, and the baseline questions sent.

## Failure Handling
- The test cannot run (toolchain missing): record `skipped:tool-missing`, do not quarantine an unrun test.
- A quarantine would hide the only test for a spec state: say so in `decisions` and leave the task not ready for review.

## Handover
Fields filled: `decisions` (each quarantine with its evidence), `open_questions` (at most one baseline approval to A04), `artifacts` (diff images), `verification.checks` (rerun counts). Written with `shared_handover_write` to A04 for a baseline question, otherwise to A09 in the closing handover (`skill://swarm-handover-emit`).
