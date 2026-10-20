---
name: "a26-smart-contract--abi-stability-gate"
description: "Use when a code.patch changes a contract with a released contract.abi and breaking ABI, selector or event changes must be caught before handover."
disable-model-invocation: true
---

## When to Use

- Before every handover of a contract that already has a released `contract.abi` (consumed by A27, A28 indexers and A29 dapps/wallets). Read-only analysis plus a decision; L2.
- Not for storage compatibility (`skill://a26-smart-contract--storage-layout-migration`), nor for choosing the version number of a release (A12). A deliberate breaking change is allowed only with the evidence in step 7.

## Procedure

1. Get the released ABI: the previous `contract.abi` artifact, else `bash` `git show <base>:<abi path>` or a rebuild at the base commit. Call `shared_git_head` with `{scan: true}` and keep `sha` and `branch`; the base commit and the head commit must both be named in the report.
2. Produce the new ABI with `bash` `forge build` then `forge inspect <Contract> abi --json`. Map every entry to `{kind, signature, mutability, returns, indexed}`: `kind` is `function`, `event` or `error`; `signature` is canonical (`transfer(address,uint256)`, no parameter names, tuples as `(uint256,address)`); `returns` is the comma-separated return types; `indexed` has one boolean per event parameter in order. Skip `constructor`, `fallback`, `receive`.
3. Call `sol_abi_diff` with `old_abi` and `new_abi`. Read `added`, `removed`, `changed`, `breaking`, `verdict` (`compatible` or `breaking`). `breaking` counts findings of major or worse. Blockers: `FN-REMOVED` and `FN-SELECTOR-CHANGED` (a retyped function is a new selector). Majors: `EVENT-REMOVED`, `EVENT-SIGNATURE-CHANGED`, `FN-RETURNS-CHANGED` (callers decode the wrong data), `EVENT-INDEXED-CHANGED` (indexers lose their topic filters), and `FN-MUTABILITY-CHANGED` when the tool rates it so (a `view` becoming `nonpayable` breaks `staticcall` callers; the severity is the tool's). A removed `error` is minor and `*-ADDED` is info, so `compatible` can still carry minors: list them. Cross-check: `breaking` is 0 exactly when `verdict` is `compatible`.
4. Call `sol_selector_check` twice, with `functions` of the old and of the new ABI (new call also `events` and `other_functions` when a proxy exists). `SEL-COLLISION` (two functions, one 4-byte selector) is a blocker. Cross-check with step 3: the old selectors that are absent from the new `selectors` list must equal `FN-REMOVED` plus `FN-SELECTOR-CHANGED` findings in number and signature; a difference means the diff and the check disagree: E-CONTRACT, show both. Selectors integrators hard-code go into `expected_selectors` of the new call.
5. When the contract claims a standard, call `sol_erc_conformance` with `standard` (`erc20`, `erc721`, `erc1155`, `erc4626` or `erc2612`), the new `abi` and `erc165_ids` for 721 and 1155. A previously `conformant` contract turning `non_conformant` is breaking even when step 3 is silent.
6. Decide. `compatible` and conformance intact: pass, mark additions in the changelog input. `breaking`: the patch is rejected unless (a) an `acceptance.criteria` item or the blueprint explicitly asks for it, (b) the change is not an upgrade of a live proxy (that is L3, human approval), and (c) a new contract address or a versioned interface name is planned. Never edit the diff output or the old ABI to make it pass.
7. For an approved breaking change record: removed/changed signatures, affected consumers (A27 audit scope, A28 event decoding schema, A29 dapp), the migration note for integrators, and the new selectors. `write` `abi/<Contract>.abi.json` (new ABI, stable key order) and `reports/<task_id>-abi-diff.md`.
8. Call `shared_artifact_digest` for the new ABI file and the diff report.

## Outputs

- `contract.abi` (the new ABI with its diff report) and a pass or reject decision on the `code.patch`.

## Failure Handling

- INVALID_INPUT: a signature that is not canonical, `indexed` length different from the parameter count, an `old_abi` entry without `kind`: fix the named entry or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction` (the tool caps `findings` at 200; use `findings_total` and say how many were not listed). DEPENDENCY_UNAVAILABLE (`forge`, `git`): retry once, then `skipped:tool-missing`; no old ABI is BLOCKED needs=input, never "compatible by default".
- Struct and tuple components are compared by the signature string only; say so when a tuple field was renamed or reordered, since names do not enter the selector but order does.
- Proxy-admin or live upgrade consequences: BLOCKED needs=human-approval (L3); mainnet L4.

## Handover

- `to_agent`: `A27` (audit scope) and `A28`/`A29` as consumers of `contract.abi`; the assigner when rejected. `decisions`: breaking or compatible and why; `artifacts`: ABI and diff with digests; `verification`: step 3 to 5 verdicts.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/abi-stability-gate.md; edit the source and run scripts/build_agents.py -->
