---
name: "a26-smart-contract--gas-optimization-pass"
description: "Use when a gas.report is requested or a function exceeds its gas budget, and a measured optimization must keep behaviour and the ABI unchanged."
disable-model-invocation: true
---

## When to Use

- A task asks for a `gas.report`, a budget is exceeded, or `review.verdict` / `gate.verdict` names a gas regression. Source changes are L2.
- Not for unmeasured micro-tuning (no baseline, no pass), for DoS and loop-bound analysis (A27 `skill://a27-contract-audit--gas-dos-review`), or for fee-market strategy (A28). A change that alters the ABI or storage layout is not an optimization: stop and use `skill://a26-smart-contract--abi-stability-gate` or `skill://a26-smart-contract--storage-layout-migration`.

## Procedure

1. Fix the measurement: `bash` `forge test --gas-report` (and `forge snapshot` for test-level numbers) with the profile, `optimizer_runs`, `via_ir` and `evm_version` of `foundry.toml`; write the numbers as `baseline[{name, gas}]` with name `Contract.function`, using one metric consistently (median per function). Call `shared_artifact_digest` for the saved baseline file so later runs compare to the same bytes.
2. Call `sol_pragma_policy` with the `pragmas`, `min_version`, `require_pinned`, `via_ir` and `optimizer_enabled` of the build. A changed optimizer or `via_ir` setting changes every number; only combine it with a code change on purpose and record it. `PRAGMA-KNOWN-BUG` majors for the declared build must be resolved first. Transient storage (`tstore`/`tload`, EIP-1153) needs solc 0.8.24 or later and a Cancun-or-later `evm_version`; check the target chain supports it before using it.
3. Rank the hot spots from the report (highest gas times call frequency from the blueprint). Candidate changes, one per commit: cache a storage read in a local variable; pack variables into one 32-byte slot (storage layout of an upgradeable contract is off limits); `immutable`/`constant`; `calldata` instead of `memory` for external arrays; custom errors instead of revert strings; `unchecked` only where the bound is proven in a comment and a fuzz test. A cold storage write from zero costs about 22100 gas and a cold `SLOAD` 2100 (EIP-2929), so removing one storage access outweighs most arithmetic tweaks.
4. After each change `edit`, run `forge test`, `forge test --gas-report`, and call `sol_gas_regression` with `baseline`, `current`, `max_regression_pct` 5 (the spec budget per function), `min_delta_gas` 100 (ignores noise) and `max_function_gas` set from the blueprint ceiling when one exists. A regressing function (`GAS-REGRESSION`) means revert that change; `GAS-OVER-CEILING` is a major; `GAS-NEW-FUNCTION` is info; `GAS-MISSING-FUNCTION` (minor) means the run lost coverage of a function: re-run, do not ignore.
5. Keep a change only when `sol_gas_regression` reports `improved` above 0 and `worst_increase_pct` at most the budget for every other function. Cross-check `total_change_pct` against the sum of per-function deltas you computed yourself; a mismatch above 0.01 points is E-CONTRACT.
6. Prove behaviour is unchanged: export the ABI before and after (`forge inspect <C> abi --json`), map entries to `{kind, signature, mutability, returns, indexed}` and call `sol_abi_diff` with `old_abi` and `new_abi`. `verdict` must be `compatible` and `added`, `removed`, `changed` all 0; anything else is no longer an optimization pass.
7. Call `sol_test_gate` with the lcov `files`, `min_line_pct` 95, `min_branch_pct` 90, `tests_failed` 0, `tests_skipped` 0, `fuzz_tests`, `fuzz_runs` (the lowest per-test run count, not a total), `min_fuzz_runs` 256, `invariant_tests`, `require_invariants` true. Gas wins that lower coverage or skip a test are rejected.
8. `write` the `gas.report` as a table `function, before, after, delta %` with the build settings and the `forge` version (`bash` `forge --version`), and the list of rejected changes with their `GAS-REGRESSION` lines.
9. Call `shared_artifact_digest` for the report and the patch.

## Outputs

- `gas.report` (baseline, final, per-function deltas, settings) and `code.patch` containing only measured, behaviour-preserving changes.

## Failure Handling

- INVALID_INPUT: duplicate `name` in `baseline` or `current`, `gas` below 1, fewer than 1 item: fix the table or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing`; never estimate gas by hand.
- Numbers from different compiler or optimizer settings are not comparable: E-CONTRACT, re-measure the baseline.
- Opcode costs above are fork-dependent; where a number matters for a decision, the `forge` measurement wins over the figures quoted here.

## Handover

- `to_agent`: `A08` (QA reads the budget gate), with `A27` as consumer of `gas.report`. `decisions`: kept and rejected changes; `artifacts`: report, patch with digests; `verification`: regression, ABI and test-gate verdicts.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/gas-optimization-pass.md; edit the source and run scripts/build_agents.py -->
