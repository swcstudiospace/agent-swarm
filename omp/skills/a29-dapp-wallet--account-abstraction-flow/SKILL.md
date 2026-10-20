---
name: "a29-dapp-wallet--account-abstraction-flow"
description: "Use when a dapp.account_abstraction task builds an ERC-4337 UserOperation flow (bundler, paymaster) and field coherence and prefund must be proven."
disable-model-invocation: true
---

# Account abstraction flow (ERC-4337)

## When to Use

- A `dapp.account_abstraction` task sends UserOperations through a bundler, sponsors gas with a paymaster or deploys the account on first use.
- Ordinary EOA transactions are `skill://a29-dapp-wallet--tx-lifecycle-ux`. Who may sponsor, signer and session-key policy belong to A30; this skill only checks the operation the client builds. Code is L2; testnet bundlers only.

## Procedure

1. `read` the task and `contract.abi` (A26) and fix the EntryPoint version: `0.6` (separate `initCode`, `paymasterAndData`) or `0.7` (packed gas fields, paymaster gas limits inside `paymasterAndData`, at least 52 bytes). Never mix field layouts; a version nobody named is BLOCKED needs=input.
2. Use `bash` or the project's bundler client to obtain a UserOperation for a testnet account from the bundler's estimate (`eth_estimateUserOperationGas`) with a dummy signature, and the final one after the wallet signs. Fields stay hex quantities as the bundler returns them.
3. Call `dapp_userop_check` with `entry_point_version`, `sender`, `nonce`, `call_gas_limit`, `verification_gas_limit`, `pre_verification_gas`, `max_fee_per_gas`, `max_priority_fee_per_gas`, `init_code`, `call_data`, `paymaster_and_data`, `signature` and `prefund_available_wei` (the deposit in the EntryPoint plus balance, or the paymaster deposit). Run it twice: the estimate (a `UOP-SIGNATURE-EMPTY` major is acceptable only there) and the final one (it must be absent).
4. Blockers: `UOP-VERIFICATION-GAS-ZERO`, `UOP-PRE-VERIFICATION-GAS` (below the calldata floor of 4 gas per zero byte and 16 per non-zero byte, read `calldata_gas_floor`), `UOP-PREFUND-SHORT`, `UOP-INIT-CODE-SHORT`, `UOP-PAYMASTER-ZERO`, `UOP-GAS-OVERFLOW` (a gas or fee value at or above 2^120, v0.6 and v0.7: the EntryPoint reverts with AA94). Majors: `UOP-FEE-ORDER`, `UOP-FEE-ZERO`, `UOP-CALL-GAS-ZERO`, `UOP-INIT-NONCE` (initCode with a non-zero nonce sequence: the EntryPoint reverts with AA25).
5. Cross-check `required_prefund_wei` by hand with `bash`: v0.6 is (`call_gas_limit` + `verification_gas_limit` x 3 with a paymaster, x 1 without, + `pre_verification_gas`) x `max_fee_per_gas`; v0.7 is (verification + call + paymaster verification + paymaster post-op + pre-verification) x `max_fee_per_gas`. A different number is E-CONTRACT: report both and do not ship.
6. Call `dapp_gas_buffer_check` with `estimated_gas` = the bundler's `callGasLimit` estimate, `gas_limit` = the value sent, `block_gas_limit`, `contract_call: true`, `max_fee_per_gas_wei`, `value_wei`, `balance_wei`, `estimate_age_seconds`. Under 10% headroom is `GAS-NO-HEADROOM` (major); recommend `recommended_gas_limit`.
7. Model the operation lifecycle for `dapp_tx_state_machine`: `submitted` is the bundler accepting the op and `tx_hash` carries the 32-byte `userOpHash`; keep the simulate then confirm order, so `TX-POLICY-NO-SIMULATION-CONFIRMATION` must stay absent. Show the user who pays (account or paymaster) and whether `deploys_account` is true.
8. `edit` the flow, then call `shared_artifact_digest` on the patch and the `dapp.integration.spec` (versions, field table, prefund math, results).

## Outputs

- `dapp.integration.spec` and `code.patch`, citing `verdict`, `required_prefund_wei`, `calldata_gas_floor`, `paymaster_present`, `deploys_account`.

## Failure Handling

- INVALID_INPUT on a hex field (not 0x-prefixed, over uint256): fix the named field or BLOCKED needs=input. PARTIAL_SUCCESS: use `completed_fraction`.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Bundler gas rules and EntryPoint addresses vary by version and chain: defer to tool output and `contract.abi`; do not quote an address from memory.
- A mainnet bundler or a paymaster policy change is L3/L4: BLOCKED needs=human-approval.

## Handover

- `to_agent`: `A06` (builds the UI) and name A15 in `remaining` for docs. `goal`: integrate the flow; `decisions`: version and paymaster use; `artifacts`: spec and patch with digests (producer `A29`); `open_questions`: sponsor policy owed by A30; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/account-abstraction-flow.md; edit the source and run scripts/build_agents.py -->
