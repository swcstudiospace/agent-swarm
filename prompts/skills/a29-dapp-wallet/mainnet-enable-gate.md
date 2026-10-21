---
name: a29-dapp-wallet--mainnet-enable-gate
description: Use when a dapp.wallet task asks to make a mainnet selectable in a client; assemble the evidence pack and stop at the L3 approval, never flip the switch alone.
owner: a29-dapp-wallet
tools: [dapp_chain_config_check, dapp_rpc_fallback_check, dapp_tx_state_machine, shared_git_head, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Mainnet enable gate

## When to Use

- A `dapp.wallet` task asks to add or enable a production chain (network class mainnet) in the wallet UI, the chain table or the feature flags of a client that already works on a test network.
- Enabling a mainnet in a client is L3 (autonomy ceiling `mainnet_enable`): this skill prepares the evidence and ends BLOCKED needs=human-approval. Deployment is A11/A12; the contracts are A26/A27. Sending a transaction on mainnet is L4 and never done here.

## Procedure

1. `read` the task and `grep` the chain table and flags (`chainId`, `enableMainnet`, `NEXT_PUBLIC_`, `VITE_`). The mainnet must stay behind a flag whose default is off in every committed config; a default of on is a blocker you fix first (`edit`).
2. Call `dapp_chain_config_check` with the production chain's `chain_id` (hex), `chain_name`, `native_name`, `native_symbol`, `native_decimals`, `rpc_urls`, `explorer_urls` and `expected_chain_id`. Read `network_class` and `mainnet_enable_requires_approval` (expected `true`: info `CHAIN-MAINNET` for a listed mainnet, or `CHAIN-UNKNOWN` for an id outside the reference table, which is treated as a mainnet until listed); any blocker (`CHAIN-NAME-SPOOF`, `CHAIN-NAME-IMPERSONATION`, `CHAIN-ID-MISMATCH`, `*-CREDENTIALS`, `CHAIN-EXPLORER-n-DANGEROUS-SCHEME`) or major stops the gate.
3. Call `dapp_rpc_fallback_check` with the production `providers`, `quorum`, `backoff_ms`, `ui_budget_ms`. Required: `verdict: pass` (so no `RPC-SAME-HOST` or `RPC-SAME-DOMAIN`), `distinct_hosts` at least 2, `majority_quorum: true`, `worst_case_ms` at most `ui_budget_ms`. The URLs must be key-free; a key means a server-side proxy first.
4. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. Required: `status` `ok` and zero `blocker` or `major` in `counts` (no `DAPP-ETH-SIGN`, `DAPP-RPC-KEY`, `DAPP-NO-CHAIN-CHECK`, `DAPP-UNLIMITED-APPROVAL`). Re-check the `DAPP-NO-SIMULATION` minors: none may remain.
5. Call `dapp_tx_state_machine` with the testnet evidence history (`events`: draft to finalized with real hashes from the test network, `required_confirmations`, `now`): `verdict: pass`, `final_state: finalized`, no `TX-POLICY-NO-SIMULATION-CONFIRMATION`. Without testnet evidence the gate is BLOCKED needs=input; never fabricate hashes.
6. Cross-check the three sources: the chain id in `dapp_chain_config_check`, the `eth_chainId` the RPC providers answered (from the task or the A28 policy) and the id compared in the code guard must be one value; two values is E-CONTRACT.
7. Call `shared_git_head` to record `sha` and `branch` in the pack. Write `dapp/mainnet-enable/<task_id>.md`: flag name and default, the verdicts above, rollback (flag off) and the human who approves, then call `shared_artifact_digest` on it.
8. Call `shared_escalate` with `task_id`, `reason_code: "policy"`, `summary: "mainnet enable awaiting L3 approval"`, `needs: ["human-approval"]` and return its `result_patch` as the BLOCKED `task.result`. Do not retry and do not flip the flag.

## Outputs

- `dapp.integration.spec` (the evidence pack with digest, flag default, rollback) and BLOCKED needs=human-approval; any `code.patch` only keeps the flag off.

## Failure Handling

- INVALID_INPUT: fix the named field or BLOCKED needs=input. PARTIAL_SUCCESS: use `completed_fraction`; a partial pack is not submitted for approval.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; a skipped check counts as not passed.
- A flag default on, a key in a client URL, or missing testnet evidence: BLOCKED, no approval request yet.
- Mainnet parameters (names, symbols, ids) come from the registry the task names; `CHAIN-UNKNOWN` means ask, not guess.

## Handover

- `to_agent`: `A01` (routes the approval). `goal`: obtain L3 approval to enable the chain behind the flag; `remaining`: human decision, then A11 deploy; `artifacts`: the pack with digest (producer `A29`); `decisions`: gate results; `blockers`: `human-approval`; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.
