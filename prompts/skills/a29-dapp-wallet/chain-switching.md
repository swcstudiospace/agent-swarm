---
name: a29-dapp-wallet--chain-switching
description: Use when a dapp.wallet task adds network switching or wallet_addEthereumChain and the chain parameters and the 4902 add-then-switch path must be verified.
owner: a29-dapp-wallet
tools: [dapp_chain_config_check, dapp_provider_error_map, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Chain switching

## When to Use

- A `dapp.wallet` task makes the app request `wallet_switchEthereumChain` or `wallet_addEthereumChain` (EIP-3326, EIP-3085), or guards signing on the connected chain.
- Test networks and the code change are L2. Adding or enabling a mainnet is L3: `skill://a29-dapp-wallet--mainnet-enable-gate`.
- RPC list resilience is `skill://a29-dapp-wallet--rpc-fallback-client`; the generic app shell is A06.

## Procedure

1. `read` the task, `rpc.policy` and the chain table in the sources (`grep` for `chainId`, `wallet_addEthereumChain`). Collect per chain: hex `chain_id`, `chain_name`, native name, symbol and decimals, `rpc_urls`, `explorer_urls`.
2. For each chain call `dapp_chain_config_check` with `chain_id` (0x-prefixed lowercase hex, no leading zeros), `chain_name`, `native_name`, `native_symbol`, `native_decimals`, `rpc_urls`, `explorer_urls` and `expected_chain_id` (the decimal id from the task). `verdict: block` stops: `CHAIN-ID-NOT-HEX`, `CHAIN-ID-MISMATCH`, `CHAIN-RPC-NONE`, `CHAIN-NAME-SPOOF` (a mainnet named like a testnet), `CHAIN-NAME-IMPERSONATION` (a name that belongs to another well-known network) and any `*-CREDENTIALS` blocker.
3. Handle `major`: `CHAIN-SYMBOL-MISMATCH`, `CHAIN-SYMBOL-LENGTH` (MetaMask takes 1-6 characters), `CHAIN-DECIMALS` (MetaMask rejects anything but 18), `CHAIN-ID-LEADING-ZERO`, `CHAIN-ID-TOO-LARGE`, and `*-KEY` (an API key in a client URL: replace with a server-side proxy). `CHAIN-UNKNOWN` is info: the reference table does not know the id, so confirm it against the chain registry the task names; never fill it from memory. `CHAIN-RPC-n-WEBSOCKET` is minor (wallet_addEthereumChain wants http(s)).
4. Read `network_class` and `mainnet_enable_requires_approval`. When it is `true`, do not wire the chain as selectable: continue only with the mainnet gate skill and call `shared_escalate` there. Test networks proceed.
5. Call `dapp_provider_error_map` with `code: 4902` and again with `4001` and `4901`. Expect `chain_not_added` (`add_chain_then_switch`; `standard` is MetaMask, because EIP-3326 defines no error code), `user_rejected` (`message_class: info`, no error toast) and `chain_disconnected` (`reconnect_or_switch_chain`). Build the switch flow from `suggested_action`: try switch, on 4902 add then switch, on 4001 stay on the current chain without an error toast.
6. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. `DAPP-NO-CHAIN-CHECK` (major) on a file that sends or signs means that file never reads `eth_chainId`/`chainId`: add a guard that compares the wallet chain with the expected one before every signing prompt, and re-read it after `chainChanged`.
7. Cross-check: `chain_id_hex` from the tool must equal the literal the code passes to `wallet_switchEthereumChain`; a different spelling is E-CONTRACT. Implement with `edit`; never switch silently, always show the target chain name.
8. Call `shared_artifact_digest` on the patch and the chain table, then hand over.

## Outputs

- `code.patch` (switch/add flow, pre-sign chain guard) with `verdict`, `network_class`, finding ids and the script `counts` cited.

## Failure Handling

- INVALID_INPUT on `chain_id`: fix to a hex string or BLOCKED needs=input. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; do not describe an unchecked chain as valid.
- `mainnet_enable_requires_approval` true or any L3 action: BLOCKED needs=human-approval, never retried.
- Reference tables go stale: when `CHAIN-UNKNOWN` fires, defer to the registry the task names and say so.

## Handover

- `to_agent`: `A09` (reviews `code.patch`). `goal`: review the switch flow; `completed`: per-chain verdicts; `decisions`: chains deferred to the mainnet gate with the finding id; `artifacts`: patch with digest (producer `A29`); `open_questions`: chains still `CHAIN-UNKNOWN`; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.
