---
name: a26-smart-contract--natspec-and-interfaces
description: Use when a contract's public surface needs a documented interface (IFoo with NatSpec, ERC-165 id) so docs, wallets and auditors read one contract.abi.
owner: a26-smart-contract
tools: [sol_natspec_coverage, sol_selector_check, sol_erc_conformance, sol_abi_diff, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A contract gains or changes public functions, events or errors, a new standalone interface file is needed, or `review.verdict` / A15 asks for NatSpec. Source edits are L2 and change no behaviour.
- Not for prose documentation, runbooks or API references (A15 writes them from your output), for ABI break decisions (`skill://a26-smart-contract--abi-stability-gate`), or for implementing standard behaviour (`skill://a26-smart-contract--erc20-implementation`).

## Procedure

1. `glob` the Solidity files under `src/` and `read` the public surface. Plan one interface per role (`IVault`, `IVaultAdmin`) holding the external functions, events and custom errors; the contract inherits it and uses `/// @inheritdoc IVault` on each implementation so the text lives once. Interface files consumed by others use a range pragma (`>=0.8.0`), implementations keep the repo's pinned compiler.
2. Write the NatSpec in the interface with `edit`: `@notice` (what it does for a user, one sentence), `@dev` (constraints, units, rounding, reentrancy and trust assumptions), one `@param <name>` per argument with the exact parameter name, one `@return` per return value (`@return name text` when named), `@custom:security` only for facts a reader must know. Events and errors get `@notice` and their `@param`. Never document a parameter that does not exist: that is wrong documentation, and the tool rates it higher than a missing tag.
3. Call `sol_natspec_coverage` with `source` (one file or contract per call, up to 100000 characters) and `min_coverage_pct` 100 for interface files and 90 for implementation files. Read `items_total`, `items_documented`, `coverage_pct`, `verdict`. `NATSPEC-COVERAGE-BELOW-MIN` is a major; `NATSPEC-PARAM-UNKNOWN` and `NATSPEC-RETURN-EXTRA` are majors (the docs claim something the code does not have); `NATSPEC-NOTICE-MISSING`, `NATSPEC-PARAM-MISSING`, `NATSPEC-RETURN-MISSING` are minors that still count against the percentage. Cross-check: `items_total` must equal the public function, event and error count you counted with `grep -c` in step 1 (an `@inheritdoc` tag counts as documented without being resolved and its `@param`/`@return` are not checked: confirm in the interface that the text exists).
4. ERC-165: write `supportsInterface(bytes4)` returning true for `type(IVault).interfaceId` and the inherited standard ids. The interface id is the XOR of the 4-byte selectors of the unique signatures declared in that interface only (inherited functions excluded). Call `sol_selector_check` with `functions` = the interface's canonical signatures: read `interface_id` and compare with the value `forge` reports for `type(IVault).interfaceId` (`bash` `cast call` on a local test, or a `console.log` in a test); any difference is E-CONTRACT. Known references: ERC-165 itself `0x01ffc9a7`, ERC-721 `0x80ac58cd`, ERC-1155 `0xd9b67a26`.
5. When the interface implements a standard, call `sol_erc_conformance` with `standard` (`erc721`, `erc1155`, `erc4626`, `erc20`, `erc2612`), `abi`, and `erc165_ids` = the ids returned by `supportsInterface` in a test. `verdict` `conformant`; `ERC-EVENT-INDEXED` majors mean the interface changes the topic layout of a standard event: fix the interface, never the standard.
6. Protect the surface: call `sol_abi_diff` with the ABI before and after the documentation edit. Adding an interface must give `added` 0, `removed` 0, `changed` 0 and `verdict` `compatible`: a documentation task that changes the ABI is rejected and split.
7. `bash` `forge doc` (when the repository uses it) to confirm the comments render, `forge build` for no warnings, and `python3 scripts/sol_contract_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path src --json` for the unchanged lint counts (`counts.major` and `counts.blocker` not higher than before).
8. Call `shared_artifact_digest` for the interface files, the extracted ABI and the coverage output.

## Outputs

- `code.patch` (interfaces, NatSpec, `supportsInterface`) and `contract.abi` (the documented ABI, `userdoc`/`devdoc` from `forge inspect <C> methodIdentifiers` and `forge inspect <C> userdoc` if A15 wants them).

## Failure Handling

- INVALID_INPUT: `min_coverage_pct` outside 0 to 100, a non-canonical signature, `source` empty: fix or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing`.
- The coverage tool reads comments as text, does not resolve imports and inherited docs may not be followed; where it and `forge doc` disagree, report both and keep the stricter result.
- Never invent behaviour in a `@dev` note to satisfy a count; leave an `open_question` instead.

## Handover

- `to_agent`: `A15` (docs from `code.patch`), with `A27`, `A28`, `A29` for `contract.abi`. `artifacts`: interfaces, ABI with digests; `verification`: coverage percentage, ERC-165 equality, ABI `compatible`.
- End with `shared_handover_validate`, then `shared_handover_write`.
