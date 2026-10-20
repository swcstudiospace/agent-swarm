---
name: "a26-smart-contract--eip712-signature-design"
description: "Use when a contract verifies off-chain signatures (permit, meta-transactions, orders) and the EIP-712 typehash, domain and replay protection must be checked."
disable-model-invocation: true
---

## When to Use

- A requirement contains "signed message", "permit", "gasless", "voucher", "order" or an ERC-2612 token. Contract code and tests are L2; no key, mnemonic or signed production payload is ever handled here: tests derive keys with `vm.createWallet` or `makeAddrAndKey` on a local chain.
- Not for plain token behaviour (`skill://a26-smart-contract--erc20-implementation`) or for off-chain signing services (A05/A29). The audit of replay and signature handling is A27's.

## Procedure

1. `read` the blueprint and write the message down: primary struct name, fields in order with Solidity types, who signs, who submits, what the signature authorises, when it expires. Every message needs: `nonce` (per signer, consumed on use) or a unique `salt`/order hash, `deadline` (checked `block.timestamp <= deadline`), and every parameter the signer must control (amount, recipient, token). A field the signer does not sign can be changed by the relayer.
2. `write` the `encode_type` string: `Primary(type name,...)` then every referenced struct appended in alphabetical order, with single spaces and no space after commas, for example `Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)`. Dynamic `string`/`bytes` are hashed with `keccak256` in `hashStruct`; arrays hash the concatenated encoding of their elements.
3. Call `sol_eip712_typehash` with `encode_type`, `declared_type_hash` (the constant in the Solidity source, copied from `grep -n "_TYPEHASH"`), `domain_name`, `domain_version`, `domain_chain_id`, `verifying_contract` (the proxy address when behind a proxy, since `address(this)` is the proxy in a delegatecall) and `declared_domain_separator` when the source hard-codes one. Read `type_hash_status` and `domain_separator_status` (`match`, `mismatch`, `not_declared`) and `verdict`. Blockers: `EIP712-TYPEHASH-MISMATCH`, `EIP712-DOMAIN-MISMATCH`, `EIP712-UNDEFINED-TYPE`, `EIP712-UNREFERENCED-TYPE`, `EIP712-TYPE-ORDER`; majors: `EIP712-DOMAIN-NO-CHAIN-ID`, `EIP712-DOMAIN-NO-CONTRACT` (replay across chains or contracts), `EIP712-DOMAIN-ZERO-CONTRACT`, `EIP712-DUPLICATE-FIELD`, `EIP712-NON-CANONICAL-WHITESPACE`.
4. Cross-check the tool against an independent computation: `bash` `cast keccak "<canonical encode_type>"` must equal `type_hash` (and `canonical_type_hash` when `encode_type` was not canonical); the well-known ERC-2612 Permit type hash is `0x6e71edae12b1b97f4d1f60370fef10105fa2faae0126114a169c64845d6126c9`, the domain type string is `EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)`. Differing values are E-CONTRACT.
5. `edit` the contract: inherit OpenZeppelin `EIP712` (or `EIP712Upgradeable`), digest with `_hashTypedDataV4(structHash)`, recover with `ECDSA.recover` or `SignatureChecker.isValidSignatureNow` (supports ERC-1271 contract wallets, magic value `0x1626ba7e`). Consume the nonce before any external call; never roll your own `ecrecover` (it returns `address(0)` on failure and accepts the high-`s` malleable twin); the domain separator must be rebuilt when `block.chainid` changes (OpenZeppelin does this); expose `eip712Domain()` (ERC-5267) so wallets can show the domain.
6. When the contract is a token with permit, call `sol_erc_conformance` with `standard: "erc2612"` and the ABI (`permit(address,address,uint256,uint256,uint8,bytes32,bytes32)`, `nonces(address)`, `DOMAIN_SEPARATOR()`), `verdict` must be `conformant`. Call `sol_selector_check` with the same `functions` and `expected_selectors` `permit` 0xd505accf, `nonces(address)` 0x7ecebe00, `DOMAIN_SEPARATOR()` 0x3644e515; verdict `clean`.
7. Write the tests first in `forge`: a valid signature succeeds once and increments the nonce; replay reverts; expired `deadline` reverts; signature for another `chainid` (`vm.chainId`) reverts; signature from a different `verifyingContract` reverts; high-`s` signature reverts; ERC-1271 wallet mock accepts. Then call `sol_test_gate` with the lcov `files`, `min_line_pct` 95, `min_branch_pct` 90, `fuzz_tests` at least 1 over the message fields, `min_fuzz_runs` 256.
8. Call `shared_artifact_digest` for the patch, the message spec (`docs/signing/<name>.md`) and the tool outputs.

## Outputs

- `code.patch` (typed-data code, constants, tests) and the signed-message spec; the ABI extract as `contract.abi` when external signers or A29 clients consume it.

## Failure Handling

- INVALID_INPUT: `declared_type_hash` not 66 characters, `verifying_contract` not `0x` plus 40 hex digits, `domain_chain_id` negative: fix or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`cast`, `forge`): retry once, then `skipped:tool-missing` and the independent hash check is stated as not done.
- The tool hashes strings you give it; it cannot see runtime values such as the deployed proxy address: say "domain computed for the planned address".
- No real signature from a user wallet or key is used or logged; BLOCKED needs=input rather than asking for one.

## Handover

- `to_agent`: `A09` (review) with `A29` (clients use the typed data) and `A27` named. `decisions`: message fields, nonce scheme, deadline rule; `verification`: tool verdict plus the `cast keccak` equality.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/eip712-signature-design.md; edit the source and run scripts/build_agents.py -->
