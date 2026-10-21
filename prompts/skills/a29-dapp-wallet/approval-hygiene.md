---
name: a29-dapp-wallet--approval-hygiene
description: Use when a dapp.signing task requests ERC-20 allowances, permits or NFT approvals and amounts, spenders, nonces and the revoke path must be limited.
owner: a29-dapp-wallet
tools: [dapp_approval_risk, dapp_permit_check, dapp_signing_request_scan, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Approval hygiene

## When to Use

- A `dapp.signing` task makes the app request `approve`, `increaseAllowance`, `permit`, Permit2 or `setApprovalForAll`.
- The general typed-data structure is `skill://a29-dapp-wallet--typed-data-signing`; reviewing a contract's own allowance logic is A27. Code is L2; a new approval kind shown to users is a signing-flow change (L3).

## Procedure

1. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. Note each `DAPP-UNLIMITED-APPROVAL` (major, a max-uint amount) and `DAPP-APPROVAL-FOR-ALL` (minor) with its `location`.
2. `grep` the call sites and build `approvals` rows: `token`, `spender`, `kind` (`erc20_approve`, `erc20_increase_allowance`, `erc20_permit`, `nft_set_approval_for_all`, `erc721_approve`), `amount`, `requested_amount` (what the action really needs), `approved` (`false` for a revoke). Build `known_spenders` from `contract.abi` / the deployment list the task names, never from runtime input.
3. Call `dapp_approval_risk` with `approvals` and `known_spenders`. `APPROVAL-SPENDER-UNKNOWN` and `APPROVAL-SPENDER-ZERO` are blockers; `APPROVAL-EXCESS` (amount above `requested_amount`) is minor; `APPROVAL-REVOKE` is info. Read `unlimited_count`, `nft_blanket_count`, `unknown_spender_count`, `revoke_count`.
4. Cross-check: the number of script `DAPP-UNLIMITED-APPROVAL` hits in the call sites you listed must equal `unlimited_count` (the tool treats an allowance of at least 2^255 as unlimited, and a Permit2 amount of at least 2^159, since Permit2 amounts are uint160). A difference means a site was missed or built dynamically: list it as E-CONTRACT and read that site.
5. Implement with `edit` this default policy: approve the exact `requested_amount`; unlimited is an off-by-default opt-in with its own confirmation; `setApprovalForAll` gets a dedicated step naming operator and collection; every approval screen has a revoke action (`approve(spender, 0)` for ERC-20). Some tokens revert when a non-zero allowance changes to another non-zero value: ask `contract.abi` / A26 before assuming reset is unnecessary.
6. For each permit call `dapp_permit_check` with `kind` (`erc2612` or `permit2`), `owner`, `spender`, `token`, `connected_account`, `domain_verifying_contract`, `value`, `nonce`, `current_nonce` (read on chain, exact; a Permit2 nonce is a uint48), `deadline`, `known_spenders`, `domain_chain_id`, `expected_chain_id`, `now`, plus `requested_amount`, `max_ttl_seconds` and for Permit2 `expiration`. Blockers: `PERMIT-OWNER-MISMATCH`, `PERMIT-SPENDER-UNKNOWN`, `PERMIT-NONCE-STALE`/`-AHEAD`, `PERMIT-INSUFFICIENT`, `PERMIT-CHAIN-MISMATCH`, `PERMIT-ALLOWANCE-EXPIRED`, `PERMIT-DEADLINE-EXPIRED`; majors `PERMIT-UNLIMITED` and the other `*-DEADLINE-*` ids; `PERMIT-ALLOWANCE-LONG` over 30 days is minor (house policy).
7. Call `dapp_signing_request_scan` with `requests` for the same operations (`method`, `to`, `data` approval calldata, `origin`) and `known_contracts`. It decodes `approve`, `increaseAllowance`, `permit`, Permit2 `approve(address,address,uint160,uint48)` and DAI-style `permit(..., bool allowed, ...)`. The DAI form carries its own signature (v, r, s), and `allowed` true is an unlimited allowance. Its `decoded_calls` must show the same spender and amount as step 3; `SIGN-UNLIMITED` or `SIGN-SPENDER-UNKNOWN` (blocker) with a clean step 3 is E-CONTRACT.
8. `edit` the call sites, then `shared_artifact_digest` on the patch and the approval table.

## Outputs

- `code.patch` and an approval table in the `wallet.flow.spec`, citing `verdict`, the four counts and every `PERMIT-*` id.

## Failure Handling

- INVALID_INPUT (`value` not decimal digits, address length): fix the field. PARTIAL_SUCCESS: name unassessed approvals. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Unknown spender with no source for `known_spenders`: BLOCKED needs=input; never allowlist from the failing input.
- The script is line-based and misses dynamic amounts: say which sites you read by hand.
- `kind: permit2` is Permit2 AllowanceTransfer (PermitSingle/PermitBatch); its `current_nonce` is the `nonce` returned by `allowance(owner, token, spender)`. SignatureTransfer permits (`PermitTransferFrom`) use unordered bitmap nonces: the tool does not cover them, so say that instead of reading `PERMIT-NONCE-*` on them.

## Handover

- `to_agent`: `A10`. `goal`: review approval and permit handling; `decisions`: exact-amount default, opt-in unlimited; `artifacts`: patch and table with digests (producer `A29`); `open_questions`: nonce source; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.
