---
name: "a26-smart-contract--foundry-test-first"
description: "Use when acceptance.criteria must become failing Foundry tests (unit, fuzz, invariant) before code exists, then pass a coverage and campaign gate."
disable-model-invocation: true
---

## When to Use

- Start of any `code.patch` task with `acceptance.criteria`, and whenever `gate.verdict` (A08) or `review.verdict` (A09) says tests, coverage or fuzzing are insufficient. Writing tests and code is L2.
- Not for the contents of one specific domain test set: token behaviour is `skill://a26-smart-contract--erc20-implementation`, upgrade tests `skill://a26-smart-contract--upgradeable-proxy-design`. Independent test review is A08; adversarial fuzz review is A27 (`skill://a27-contract-audit--fuzz-campaign-review`).

## Procedure

1. Call `shared_toolchain` with `{scan: true}` and `shared_check_plan` with `{scan: true}`, and `bash` `command -v forge && forge --version`. The first reports only the Python and JS toolchains: Foundry is detected by `bash`. Compare `names` of the check plan with the commands you will run (`forge build`, `forge test`, `forge coverage`); a plan that lacks them is recorded as a gap, not repaired here. Call `shared_git_head` with `{scan: true}` and keep `sha` and `branch` for the report.
2. `read` the `acceptance.criteria` and write a table `criterion -> test name -> kind`. One criterion maps to at least one test. Kinds: `test_<Behavior>` unit; `testRevert_<Case>` using `vm.expectRevert(<Error>.selector)` (never the deprecated `testFail`); `testFuzz_<Property>(uint256 x)` with `bound`/`vm.assume` to the valid domain (assume sparingly: a filter that rejects over half the inputs aborts the run); `invariant_<Property>` with a handler contract that narrows the call space; fork tests with `vm.createSelectFork("<alias>")` where the alias is a name from `[rpc_endpoints]` that reads an environment variable. Never write an RPC URL, key or mnemonic into a test or `foundry.toml`.
3. `write` the tests under `test/` (`*.t.sol`, `contract XTest is Test`, `setUp()` builds the system) and run `bash` `forge test --match-contract XTest`. Red first: the new tests must fail for the stated reason (revert reason, missing function), not for a compile error unrelated to the criterion. Record the failing names.
4. Implement the smallest change that turns them green (`edit`), then run the full `forge test` and `python3 scripts/sol_contract_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <contracts dir> --json` (it skips `*.t.sol` and `*.s.sol`; exit 1 means a major or blocker finding in contract code; skipped or unreadable in-scope input fails it closed, and `lib/` is listed by name without failing it).
5. Configure the CI profile in `foundry.toml` (`[profile.ci.fuzz] runs = 1000`, `[profile.ci.invariant] runs = 256 depth = 100`; defaults differ by Foundry version, so read the file and the `forge --version`, do not trust memory) and run `bash` `FOUNDRY_PROFILE=ci forge test` and `forge coverage --report lcov`.
6. Map each lcov record to `files[{path, lines_found, lines_hit, branches_found, branches_hit, functions_found, functions_hit}]` (`LF`, `LH`, `BRF`, `BRH`, `FNF`, `FNH`; exclude `test/` and `script/`). Call `sol_test_gate` with `min_line_pct` 95, `min_branch_pct` 90 (the handover floor of the A26 spec), `min_file_line_pct` 85, `tests_total`, `tests_failed`, `tests_skipped`, `fuzz_tests`, `fuzz_runs` (the runs of each fuzz test, not a total: pass the lowest per-test count, since every fuzz test must reach the floor), `min_fuzz_runs` 256 (the spec floor and the tool default; the CI profile above runs more), `invariant_tests`, `require_invariants` true for any contract that holds value.
7. Read the verdict: `TEST-FAILING`, `TEST-NONE` and `TEST-NO-COVERAGE` are blockers; `TEST-LINE-COVERAGE`, `TEST-BRANCH-COVERAGE`, `TEST-NO-FUZZ`, `TEST-FUZZ-RUNS`, `TEST-NO-INVARIANT` are majors (add tests, never lower a threshold yourself); `TEST-FILE-LINES` minor lists files under `min_file_line_pct`; `TEST-SKIPPED` minor needs a reason per skipped test. `TEST-NO-BRANCHES` info means branch coverage is vacuous, so cite line coverage only.
8. Cross-check: `line_pct` from `sol_test_gate` against the `forge coverage` summary table; they must agree within 0.5 points, else E-CONTRACT (lcov filtering differs). Every criterion row of step 2 must now have a green test; list uncovered criteria as `remaining`.
9. `write` `reports/<task_id>-tests.md` (criterion table, `sha`, `forge` version, gate output) and call `shared_artifact_digest` on it.

## Outputs

- `code.patch` with tests first in history order, and the test report as evidence for A08 and A09.

## Failure Handling

- INVALID_INPUT: counts that contradict each other (`tests_failed` above `tests_total`, `lines_hit` above `lines_found`): fix from the real `forge` output or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`, tool calls): retry once, then `skipped:tool-missing`; coverage that could not be measured is never reported as passing.
- `forge coverage` runs with the optimizer off and can mis-attribute lines; treat small gaps with that caveat, large ones as real.

## Handover

- `to_agent`: `A08`. `completed`: criterion table; `verification`: gate verdict and `line_pct`/`branch_pct`; `open_questions`: uncovered criteria.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/foundry-test-first.md; edit the source and run scripts/build_agents.py -->
