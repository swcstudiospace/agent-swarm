---
name: a29-dapp-wallet--wallet-connect-integration
description: Use when a dapp.wallet task adds or changes wallet connection (EIP-6963, EIP-1193, WalletConnect v2) and needs a checked capability matrix and connect flow.
owner: a29-dapp-wallet
tools: [dapp_wallet_matrix, dapp_signing_request_scan, shared_git_head, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Wallet connection integration

## When to Use

- A `dapp.wallet` task adds a connect button, a wallet list or a WalletConnect session, or changes how providers are discovered.
- Code work is L2. Changing what a user is asked to sign during connect is a signing-flow change (L3).
- Switching networks is `skill://a29-dapp-wallet--chain-switching`; the page layout and generic components belong to A06.

## Procedure

1. `read` the task and `ux.spec`, then `grep` the sources for `request({` / `.request(` calls to list every JSON-RPC method the app uses (`required_methods`) and take the target chains from the task (`required_chain_ids`). No method or chain list is BLOCKED needs=input.
2. Build one `wallets` row per candidate wallet: `name`, `eip1193`, `walletconnect` (`none`, `v1` or `v2`), `announced_6963`, `methods`, `chain_ids`, plus `rdns`, `uuid`, `icon` for announced ones. Take values from the task or captured `eip6963:announceProvider` events; a capability nobody observed is never guessed (BLOCKED needs=input).
3. Call `dapp_wallet_matrix` with `wallets`, `required_methods`, `required_chain_ids`. `blocker` findings stop the work: `WALLET-UNREACHABLE` (no EIP-1193 provider and no WalletConnect). `major`: `WALLET-WALLETCONNECT-V1` for a wallet that offers only v1 (the v1 relay is shut down, use v2; with an injected EIP-1193 provider next to it the finding is minor and the wallet stays compatible), `WALLET-6963-UUID`/`-RDNS`/`-ICON` (malformed announcement), `WALLET-METHODS-MISSING`, `WALLET-CHAINS-MISSING`, `WALLET-NONE-COMPATIBLE` (no wallet satisfies every method and chain: stop, report `compatible_count` 0).
4. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. Exit 1 means a major or blocker in `counts`. Each `DAPP-WINDOW-ETHEREUM` (info) must either be replaced by EIP-6963 discovery (`eip6963:requestProvider`, listen for `eip6963:announceProvider`) or be the documented last-resort fallback; `DAPP-CLIENT-SIGNER`, `DAPP-KEY-MATERIAL` and `DAPP-KEY-STORAGE` must be zero (the app never holds keys).
5. Cross-check `dapp_wallet_matrix` against the script: a wallet with `announced_6963: false` is reachable only through `window.ethereum`, so a `compatible_wallets` entry of that kind needs a `DAPP-WINDOW-ETHEREUM` site in the code; a compatible wallet with no code path that reaches it (or code that reaches one the matrix lacks) is E-CONTRACT, report both.
6. If connect issues a signature (for example a login), call `dapp_signing_request_scan` with `requests` (`method`, `origin`), `known_contracts` and `expected_domains` (the app's own domains). `ORIGIN-SPOOF`, `ORIGIN-LOOKALIKE` and `ORIGIN-USERINFO-SPOOF` are blockers; `SIGN-METHOD` on `eth_sign` is a blocker.
7. Implement (`edit`): connect only after a user click, never `eth_requestAccounts` on page load; subscribe to `accountsChanged` and `chainChanged`; treat an empty accounts array as disconnected; one WalletConnect v2 project id read from a public env variable (an identifier, not a secret) and never a credentialed URL.
8. Call `shared_git_head` for the `sha` and `branch`, write the `wallet.flow.spec` under `dapp/wallet-flow/<task_id>.md` (matrix, states, events) and call `shared_artifact_digest` on it and on the changed files.

## Outputs

- `wallet.flow.spec` (matrix, connect states, events handled) and `code.patch`, citing `verdict`, `compatible_count` and the script `counts`.

## Failure Handling

- INVALID_INPUT: fix the named field (for example a `uuid` over 64 characters) or BLOCKED needs=input. PARTIAL_SUCCESS: use `completed_fraction` and name the unassessed wallets.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` and state the gap; never present a matrix you did not get.
- The script rules are line heuristics: a finding is a prompt to look, not proof. Library names and versions come from the repo lockfile, not memory.
- Any new signature during connect is L3: BLOCKED needs=human-approval.

## Handover

- `to_agent`: `A06` (builds the surrounding UI). `goal`: wire the connect UI to this flow; `completed`: checks run; `decisions`: wallets kept or dropped with the finding id; `artifacts`: spec and patch with digests (producer `A29`); `open_questions`: unobserved wallet capabilities; `verification`: partial until A08 tests it.
- End with `shared_handover_validate`, then `shared_handover_write`.
