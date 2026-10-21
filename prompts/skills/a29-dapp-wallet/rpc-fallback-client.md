---
name: a29-dapp-wallet--rpc-fallback-client
description: Use when a dapp.wallet task builds the client RPC transport from an rpc.policy and it must be key-free, meet quorum and fit the UI time budget.
owner: a29-dapp-wallet
tools: [dapp_rpc_fallback_check, dapp_chain_config_check, shared_artifact_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# RPC fallback in the client

## When to Use

- A `dapp.wallet` task turns the `rpc.policy` (A28) into the app's transport: provider list, quorum, per-provider timeout and retry, hedge delay.
- Choosing providers and rate limits is A28 (`rpc.policy`); wallet-side chain parameters are `skill://a29-dapp-wallet--chain-switching`. Code is L2; a mainnet endpoint change is L3.

## Procedure

1. `read` the `rpc.policy` and call `shared_artifact_check` on it (`task_id`, `outputs`: `[{kind, uri, digest, producer}]` for the policy, producer `A28`) so you build from the verified artifact. Take order, `hedge_after_ms` and eject triggers from it; no policy is BLOCKED needs=input.
2. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`. `DAPP-RPC-CREDENTIALS` (blocker), `DAPP-RPC-KEY` (major), `DAPP-RPC-INSECURE` and `DAPP-PUBLIC-SECRET-ENV` (blocker: a secret in a client-exposed env prefix) mean an endpoint with a key ships to every user: route it through a server-side proxy and keep only a key-free URL in the client.
3. Build `providers` (`url`, `timeout_ms`, `retries`, `weight`) in policy order and call `dapp_rpc_fallback_check` with `providers`, `quorum`, `backoff_ms`, `ui_budget_ms` (the longest a user waits for a read) and `stall_timeout_ms` (the policy hedge delay). Blockers: `RPC-QUORUM-UNREACHABLE` (quorum above `total_weight`) and any `RPC-n-CREDENTIALS`. Majors: `RPC-NO-FALLBACK`, `RPC-SAME-HOST` (`distinct_hosts` 1), `RPC-SAME-DOMAIN` (several hosts under one registrable domain, such as three Infura subdomains: one operator outage takes all of them), `RPC-WORST-CASE`, `RPC-TIMEOUT-LONG`, `RPC-n-KEY`.
4. Read the `dapp_rpc_fallback_check` minors: `RPC-TIMEOUT-SHORT` (under 1000 ms), `RPC-RETRIES-HIGH` (above 3: fail over instead of hammering one host), `RPC-QUORUM-NO-MAJORITY` (`majority_quorum` false: two disagreeing groups can both reach quorum), `RPC-STALL` (the stall timeout must be below the shortest provider timeout or the hedge never starts), `RPC-DUPLICATE-HOST`. Timeouts stay within 1000 to 30000 ms.
5. Cross-check `worst_case_ms` yourself with `bash`: the sum over providers of `timeout_ms` x (`retries` + 1) + `backoff_ms` x `retries`; it must equal the tool value and stay at or below `ui_budget_ms`. If it does not fit, lower retries or add hedging; never raise `ui_budget_ms` to pass.
6. Call `dapp_chain_config_check` with the chain's fields and the same `rpc_urls` so the wallet-facing list passes the same URL rules (`CHAIN-RPC-NONE`, `*-CREDENTIALS`); the two URL verdicts must agree for identical URLs, else E-CONTRACT.
7. Implement (`edit`) with the library the lockfile pins (for example viem `fallback` transport with `retryCount` and `rank`, or ethers `FallbackProvider` with `quorum`, `stallTimeout` and `weight`; confirm option names against the pinned version). Verify each provider answers `eth_chainId` with the expected id before trusting it.
8. Call `shared_artifact_digest` on the patch and the transport config, then hand over.

## Outputs

- `code.patch` (transport config) and a `dapp.integration.spec` section with `verdict`, `worst_case_ms`, `distinct_hosts`, `majority_quorum`, citing the policy digest.

## Failure Handling

- INVALID_INPUT (a `url` that is not absolute, `retries` over 20): fix the field or BLOCKED needs=input. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- A policy URL that carries a key: BLOCKED needs=input for A28/A11 to provide a proxy URL; never copy it into the client.
- Library option names change by version: defer to the lockfile and say so.

## Handover

- `to_agent`: `A09`. `goal`: review the transport; `decisions`: quorum, timeouts, budget; `artifacts`: patch with digest (producer `A29`); `open_questions`: proxy URL owed by A28; `verification`: partial until a testnet failover drill by A08.
- End with `shared_handover_validate`, then `shared_handover_write`.
