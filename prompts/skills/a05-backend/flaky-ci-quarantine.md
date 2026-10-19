---
name: a05-backend--flaky-ci-quarantine
description: Use when a CI or local check fails intermittently; rerun twice, quarantine only the one test that passes on retry, report it, never disable tests silently.
owner: a05-backend
tools: [shared_check_plan, shared_handover_validate, shared_handover_write]
---

## When to Use
- The same check fails once and passes on rerun without a code change (A05 spec section 5: retry x2, then quarantine; prompt `<error_handling>`).
- A deterministic failure is not a flake: fix it inside `a05-backend--pre-review-self-gate`. No registry tool measures flakiness, so the evidence here is only the run outputs.

## Procedure
1. Call `shared_check_plan` with `{scan: true}` and note the exact check name that failed and the command it maps to (`names` is one string).
2. Record the first failure from `python3 scripts/code_checks.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json`: the check name in `results`, the test id, and the last lines of its output in `executed[].tail` (`tail` is at most 1200 characters; run the single test with `bash` when you need more).
3. Rerun the same command twice with `bash` and no code change; record both `results` values. A failure on both reruns with the same message is a real failure: stop and fix it. (pytest exit 5, no tests collected, counts as pass in `code_checks`.)
4. If a rerun passes, quarantine only that one test: with `edit` add the narrowest skip marker the framework offers and the reason `quarantine: flaky, task <task_id>, see handover`. Never delete the test, loosen an assertion, or skip a file or suite.
5. Use `grep` for `quarantine:` over the changed files; the count must equal the number of tests you quarantined.
6. Rerun `scripts/code_checks.py` once more with `bash`: no new `fail`, and the quarantined test no longer runs.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` to A01.

## Outputs
- The list of quarantined tests with first-failure evidence, the three run results, and the one-line marker diff.

## Failure Handling
- Cannot isolate one test (the whole job flakes): do not quarantine; report an infra failure (`E-DEP`) with the three outputs as evidence.
- Check not in the plan: record it as `skipped:tool-missing`.
- Never mask a failure by raising a timeout or retry count in config.

## Handover
`to_agent` is A01 (A05 spec section 5 says notify A08, but the prompt routes the report through A01, which forwards it). `open_questions` lists each quarantined test for A08 to review, `decisions` holds the evidence (`{decision: "quarantined <test>", rationale: <first failure, rerun results>}`), `verification.checks` the three run results. Validate and write as in `skill://swarm-handover-emit`.
