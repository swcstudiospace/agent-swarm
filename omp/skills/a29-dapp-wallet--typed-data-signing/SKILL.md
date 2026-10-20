---
name: "a29-dapp-wallet--typed-data-signing"
description: "Use when a dapp.signing task builds an EIP-712 signTypedData_v4 request and its domain, types, chain, contract binding, deadline and nonce must be checked."
disable-model-invocation: true
---

# Typed-data signing (EIP-712)

## When to Use

- A `dapp.signing` task creates or edits a typed-data payload (orders, votes, meta-transactions, gasless approvals).
- Permit amounts, spenders and on-chain nonces are `skill://a29-dapp-wallet--approval-hygiene`; plain-text login is `skill://a29-dapp-wallet--siwe-login`.
- Code is L2; adding or changing a signed message type is a signing-flow change (L3).

## Procedure

1. `grep` the sources for `signTypedData` and `eth_signTypedData`; list each payload builder. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. `DAPP-ETH-SIGN` is a blocker (blind hash signing), `DAPP-TYPED-DATA-LEGACY` is minor (v1 format): move every call to `eth_signTypedData_v4`.
2. For each builder produce one realistic payload with `types` (including `EIP712Domain`), `primaryType`, `domain`, `message`. Take the verifying contract addresses from `contract.abi` or the deployment registry the task names, never from user input.
3. Call `dapp_typed_data_check` with `typed_data_json` (the document as a string), `expected_chain_id`, `now`, `deadline_field` (the message member that holds the expiry, for example `deadline` or `validBefore`), `max_ttl_seconds` (tool default 3600) and `expected_verifying_contracts`. Blockers: `TD-CHAIN-MISMATCH`, `TD-TYPE-UNDEFINED`, `TD-PRIMARY-UNDEFINED`, `TD-VALUE-RANGE`, `TD-REPLAY-UNBOUNDED`. Majors: `TD-CHAIN-MISSING` (replayable on every chain), `TD-VERIFYING-ZERO`, `TD-VERIFYING-UNEXPECTED`, `TD-DEADLINE-MISSING`, `TD-NONCE-MISSING` (a nonce inside an array of structs, as in Permit2 PermitBatch, counts), `TD-DOMAIN-TYPE-MISMATCH`, `TD-DOMAIN-SALT`, `TD-FIELD-MISSING`.
4. Minors to fix before approval: `TD-TYPE-ALIAS` (write `uint256`, not `uint`), `TD-FIELD-EXTRA` (a field not in the type is not signed), `TD-VERIFYING-MISSING`, `TD-DOMAIN-NAME-VERSION`. Read `deadline_remaining_seconds`: a runway under 60 s, over `max_ttl_seconds` (house policy), in milliseconds, or never expiring is a `*-DEADLINE-*` finding.
5. Call `dapp_signing_request_scan` with `requests` (`method: "eth_signTypedData_v4"`, `typed_domain_verifying_contract`, `origin`), `known_contracts` and `expected_domains`. `SIGN-VERIFYING-UNKNOWN` must agree with `TD-VERIFYING-UNEXPECTED` for the same payload; a disagreement is E-CONTRACT, report both.
6. Using `dapp_typed_data_check`, mutate each payload once (wrong chain id, zero verifying contract, drop the nonce) and re-run step 3: the mutation must raise the matching finding, otherwise the check input was wrong.
7. Implement (`edit`): build the payload server-side or from constants, show the user the decoded fields (primary type, spender, amount, deadline), never only a hash, and rebuild the payload after every `chainChanged`.
8. Call `shared_artifact_digest` on the patch and payload fixtures, then hand over.

## Outputs

- `code.patch` and `wallet.flow.spec` (payload shapes, shown fields), citing `verdict`, `primary_type`, `domain_chain_id`, `deadline_remaining_seconds` and the script `counts`.

## Failure Handling

- INVALID_INPUT on `typed_data_json` (not JSON, over 65536 chars) or `now` without an offset: fix the field or BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and no verdict.
- The tool checks structure, not business meaning; whether the message matches the contract's `TYPEHASH` comes from `contract.abi` and A26, not from this check.
- A changed message type is L3: BLOCKED needs=human-approval; never sign anything to test it.

## Handover

- `to_agent`: `A10` (security reviews signing paths). `goal`: review the signed message types; `completed`: verdicts per payload; `decisions`: TTL and nonce policy; `artifacts`: patch and fixtures with digests (producer `A29`); `open_questions`: TYPEHASH match owed by A26; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/typed-data-signing.md; edit the source and run scripts/build_agents.py -->
