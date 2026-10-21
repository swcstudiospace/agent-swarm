---
name: "a29-dapp-wallet--simulation-before-sign"
description: "Use when a dapp.tx_lifecycle task adds the pre-sign gate (eth_call simulation, gas estimate, balance cover, decoded confirmation) before a wallet prompt."
disable-model-invocation: true
---

# Simulation before sign

## When to Use

- A `dapp.tx_lifecycle` task sends transactions and must prove none reaches a signature prompt without a simulation and an explicit user confirmation (this agent never ships a flow without both).
- Tracking after submission is `skill://a29-dapp-wallet--tx-lifecycle-ux`. Code is L2; testnet or fork only. Broadcasting on mainnet is an L4 human step.

## Procedure

1. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. Each `DAPP-NO-SIMULATION` (minor) names a file that sends but never simulates or estimates (`simulateContract`, `eth_call`, `estimateGas`); `DAPP-NO-CHAIN-CHECK` (major) names a file with no chain guard. Treat the minor as a blocker for this task.
2. Call `shared_toolchain` with `scan: true` to learn which JS toolchain exists; if none is present, record `skipped:tool-missing` for local tests and keep the review static.
3. Implement (`edit`) the gate in order: re-read `eth_chainId` and compare; simulate with `eth_call` or `simulateContract` from the connected account against `latest`; on revert go to `simulation_failed` with the reason decoded through `dapp_provider_error_map` (`code: 3`, `revert_data`) and no confirm button; else `eth_estimateGas`, then show the confirmation, then ask for the signature.
4. For each flow call `dapp_gas_buffer_check` with `estimated_gas`, `gas_limit`, `block_gas_limit` (from the latest block), `contract_call`, `max_fee_per_gas_wei`, `value_wei`, `balance_wei`, `estimate_age_seconds`, and `tx_gas_cap` when the chain sets one. Rules from its findings: blockers `GAS-BELOW-INTRINSIC` (under 21000), `GAS-BELOW-ESTIMATE`, `GAS-ABOVE-BLOCK-LIMIT`, `GAS-ABOVE-TX-CAP`, `GAS-INSUFFICIENT-BALANCE` (balance under value plus `gas_limit` x `max_fee_per_gas_wei`, see `max_cost_wei`); majors `GAS-NO-HEADROOM` (contract call under 10% headroom) and `GAS-NEAR-BLOCK-LIMIT` (over 90% of the block limit); minors `GAS-BUFFER-EXCESSIVE` (over 100%), `GAS-ESTIMATE-STALE` (over 60 s: re-estimate) and `GAS-RECOMMEND-OVER-CEILING` (the estimate is itself above the 90% ceiling, so `recommended_gas_limit` equals the estimate and the transaction may never be included). Use `recommended_gas_limit` (20% headroom) as the default.
5. Cross-check by hand with `bash`: `buffer_percent` equals (`gas_limit` - `estimated_gas`) / `estimated_gas` x 100 and `max_cost_wei` equals `value_wei` + `gas_limit` x `max_fee_per_gas_wei`. A mismatch is E-CONTRACT.
6. Call `dapp_signing_request_scan` with the pending `requests` (`method`, `to`, `data`, `origin`), `known_contracts` and `expected_domains`. Show `decoded_calls` in the confirmation: an approval, a transfer (`SIGN-ASSET-MOVEMENT`, info) or `setApprovalForAll` must be named in words. `SIGN-TARGET-UNKNOWN` or a blocker stops the flow before the prompt.
7. Build the fixture history draft, simulating, simulated, awaiting_confirmation, user_confirmed, awaiting_signature and call `dapp_tx_state_machine` with `events` and `now`: `verdict: pass`. Then remove `simulated` and re-run: `TX-POLICY-NO-SIMULATION-CONFIRMATION` (blocker) must appear, proving the gate is enforced.
8. Call `shared_artifact_digest` on the patch and the fixtures, then hand over.

## Outputs

- `code.patch` (gate, decoded confirmation) and `wallet.flow.spec` (gate order, gas rules), citing script `counts`, `verdict`, `buffer_percent`, `recommended_gas_limit`.

## Failure Handling

- INVALID_INPUT (`estimate_age_seconds` over 86400, counts out of range): fix the field or BLOCKED needs=input. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- An estimate that fails or a simulation that cannot run (no RPC): the flow stays blocked, never "skip simulation to proceed".
- Gas caps change with protocol upgrades: pass `tx_gas_cap` only from the task or chain docs and defer to the tool.
- Never send a real transaction to test; signing on a funded mainnet account is L4.

## Handover

- `to_agent`: `A08` (QA exercises the gate on testnet). `goal`: test the gate with failing and passing simulations; `artifacts`: patch and fixtures with digests (producer `A29`); `decisions`: headroom 20%; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/simulation-before-sign.md; edit the source and run scripts/build_agents.py -->
