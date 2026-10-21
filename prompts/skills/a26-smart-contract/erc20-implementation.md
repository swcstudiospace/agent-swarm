---
name: a26-smart-contract--erc20-implementation
description: Use when a code.patch task creates or changes a fungible token (ERC-20, optionally ERC-2612 permit) and the result must be standard-conformant and test-gated.
owner: a26-smart-contract
tools: [sol_erc_conformance, sol_selector_check, sol_pragma_policy, sol_test_gate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A task whose `architecture.blueprint` / `acceptance.criteria` asks for a new or changed ERC-20 (name, symbol, supply, mint/burn, permit). Writing contract source is L2.
- Not for the EIP-712 permit hashing details (`skill://a26-smart-contract--eip712-signature-design`), for ABI drift of an already deployed token (`skill://a26-smart-contract--abi-stability-gate`), or for ERC-721/1155/4626 design. Auditing is A27; deployment is `skill://a26-smart-contract--deploy-script-dry-run`.

## Procedure

1. `read` the blueprint and criteria; fix in writing: `name`, `symbol`, `decimals` (OpenZeppelin default 18; anything else needs a criterion), supply model (fixed at construction, capped mint, burn), and which of ERC-2612 permit is required. Fee-on-transfer, rebasing and blacklist behaviour are rejected unless a criterion names them (integrators assume plain balances). `grep -n "openzeppelin" remappings.txt foundry.toml` to learn the major: v5 customises `_update` and reverts with custom errors (`ERC20InsufficientBalance`, `ERC20InvalidReceiver`), v4 uses `_beforeTokenTransfer`. Build on the library (`ERC20`, `ERC20Permit`, `ERC20Capped`, `ERC20Burnable`); never hand-roll the balance arithmetic.
2. Write the failing tests first (`skill://a26-smart-contract--foundry-test-first`) for the ERC-20 specifics: a zero-value transfer still emits `Transfer`; mint emits `Transfer` from `address(0)`, burn to `address(0)`; `transferFrom` with an allowance of `type(uint256).max` leaves the allowance unchanged; transfer or approve to the zero address reverts; invariant: sum of balances equals `totalSupply`.
3. `edit` / `write` the contract until the tests pass. Add no owner powers the criteria do not list (`skill://a26-smart-contract--access-control-roles`).
4. Run `python3 scripts/sol_contract_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <contracts dir> --json`. Exit 1 means `counts.major` or `counts.blocker` is above 0 (`SOL-TX-ORIGIN`, `SOL-SELFDESTRUCT`, `SOL-UNCHECKED-LOW-LEVEL-CALL`, `SOL-PRAGMA-NOT-PINNED`): fix them; `SOL-MISSING-EVENT` minors on state-changing functions are fixed too. `tools.slither` `skipped:tool-missing` is recorded, not an error.
5. Get the ABI with `bash` `forge inspect <Token> abi --json` and map each entry to `{kind, signature, mutability, returns, indexed}`. Call `sol_erc_conformance` with `standard: "erc20"` (a second call with `"erc2612"` when permit is required) and that `abi`. Pass only when `verdict` is `conformant` and `missing` is 0. `ERC-MISSING-FN`/`ERC-MISSING-EVENT` are blockers; `ERC-RETURNS` and `ERC-MUTABILITY` are majors (`transfer`, `approve`, `transferFrom` return `bool`); Add the optional `name`, `symbol`, `decimals` (`ERC-OPTIONAL-FN-MISSING`, info): wallets read them.
6. Call `sol_selector_check` with `functions` (all public signatures), `events` (`Transfer(address,address,uint256)`, `Approval(address,address,uint256)`) and `expected_selectors` for the wire constants: `transfer(address,uint256)` 0xa9059cbb, `approve(address,uint256)` 0x095ea7b3, `transferFrom(address,address,uint256)` 0x23b872dd, `balanceOf(address)` 0x70a08231, `totalSupply()` 0x18160ddd, `allowance(address,address)` 0xdd62ed3e, `permit(address,address,uint256,uint256,uint8,bytes32,bytes32)` 0xd505accf. `verdict` must be `clean`; `SEL-CONSTANT-MISMATCH` is a blocker. Cross-check: `function_count` equals the ABI function count of step 5, and the `Transfer` `topic0` starts `0xddf252ad`.
7. Call `sol_pragma_policy` with the `pragmas` found by `grep -n "pragma solidity"`, `min_version` `0.8.20` (the OpenZeppelin 5 floor; use the repo's higher floor when set), `require_pinned` true for the token itself, and `via_ir` and `optimizer_enabled` copied from `foundry.toml`. `verdict` `pass` and `buggy` 0; a `SOL-PRAGMA-*` lint finding without the matching `PRAGMA-*` one is E-CONTRACT (one rule set).
8. Run `bash` `forge coverage --report lcov`, map each file to `files[{path, lines_found, lines_hit, branches_found, branches_hit, functions_found, functions_hit}]` and call `sol_test_gate` with `min_line_pct` 95, `min_branch_pct` 90, `min_file_line_pct` 85, the `forge test` counts as `tests_total`/`tests_failed`/`tests_skipped`, `fuzz_tests`, `fuzz_runs` (the lowest per-test run count, not a total), `min_fuzz_runs` 256, `invariant_tests` and `require_invariants` true. Any blocker (`TEST-FAILING`, `TEST-NONE`, `TEST-NO-COVERAGE`) stops the handover.
9. Call `shared_artifact_digest` for the patch, the ABI JSON and the test report.

## Outputs

- `code.patch` (token, tests, lint and conformance evidence) and `contract.abi` (the ABI file of step 5).

## Failure Handling

- INVALID_INPUT: fix the named field (a non-canonical signature such as `transfer(address to,uint256)`, `expected_selectors.selector` not `0x` plus 8 hex digits) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked part; DEPENDENCY_UNAVAILABLE (`forge`, `slither`): retry once, then record `skipped:tool-missing`; never claim coverage that was not measured.
- Tool and lint disagree on a pragma or selector: E-CONTRACT, report both outputs. The lint is heuristic text analysis, not a compiler.
- OpenZeppelin names above are v5; when the checked-out version differs, the repo source wins.

## Handover

- `to_agent`: `A09` (review), with `A08`, `A10` and `A27` named in `open_questions`; `contract.abi` consumers are A27, A28 and A29. `completed`: criteria met; `decisions`: supply model, decimals, permit yes/no; `artifacts`: patch, ABI, test report with digests; `verification`: conformance, selector, pragma and test-gate verdicts.
- End with `shared_handover_validate`, then `shared_handover_write`.
