---
name: a28-chain-infra--mev-protection-routing
description: Use when a chain task must pick the submission route (public mempool, private relay, sequencer) and slippage cap for swap, liquidation or mint transactions.
owner: a28-chain-infra
tools: [chain_mempool_privacy, chain_confirmation_depth, chain_network_guard, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# MEV protection routing

## When to Use

- A `chain.rpc` task asks whether a transaction class should leave through the public mempool, a private relay or a rollup's sequencer, and what slippage limit bounds a sandwich loss.
- The analysis is L2. Moving production traffic to another route or relay is L3 for A11; any transaction on mainnet is signed and sent by a human or the owning service, never by this agent.
- Fee levels are `skill://a28-chain-infra--gas-fee-strategy`; transactions already stuck are `skill://a28-chain-infra--stuck-transaction-recovery`; the relay is chosen only from what the task lists.

## Procedure

1. `read` the task and list one row per transaction class: `network_kind` (`l1_public_mempool` or `l2_sequencer`), `tx_kind` (`swap`, `liquidation`, `nft_mint`, `arbitrage`, `transfer`, `governance`), typical `value_usd`, `slippage_bps`, `has_deadline`, the route used today (`submitted_route`: `public` or `private`), whether a private route exists on this chain (`private_route_available`) and whether it drops reverting transactions (`revert_protection`). The owner states `max_acceptable_loss_usd`; without it BLOCKED needs=input.
2. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`. Use the result's `network_class` and `chain_name` to check that `network_kind` is right: an OP Stack or Arbitrum chain is `l2_sequencer`, Ethereum L1 is `l1_public_mempool`. A wrong kind is E-CONTRACT.
3. Call `chain_mempool_privacy` once per class with the row values. Read `route`: `public_mempool`, `private_relay` or `sequencer_native`; `exposure`: `none`, `low`, `medium` or `high`; `max_sandwich_loss_usd` (swaps only: value times slippage tolerance, the most a sandwich can take); `slippage_bps_cap` (the slippage that keeps that loss within `max_acceptable_loss_usd`, -1 for non-swaps); `route_change_required` is true only when the computed route is `private_relay` and `submitted_route` is `public`; a private submission never needs a change, and `public_mempool` or `sequencer_native` routes never set it.
4. Decision rules on the `chain_mempool_privacy` output. `high` exposure (a swap whose `max_sandwich_loss_usd` is above the owner's loss limit) needs a private relay, or the slippage lowered to `slippage_bps_cap`. Liquidations, mints and arbitrage are front-run targets (`medium`): private route too. `no-private-route` (major) means the chain offers none: lower slippage or accept the exposure in writing. `no-deadline` (major) on a swap: set a deadline in blocks or seconds; a builder may otherwise hold the transaction. `no-revert-protection` (minor): the relay may still charge gas for a revert.
5. For `l2_sequencer` (`chain_mempool_privacy` route `sequencer_native`) there is no public mempool, but ordering is the sequencer's policy, which is a trust assumption and a single point of censorship. State the forced-inclusion path through the L1 inbox and defer its delay to the rollup's docs.
6. Cross-check the figures: `max_sandwich_loss_usd` equals `value_usd * slippage_bps / 10000` for a swap, and `slippage_bps_cap` multiplied by `value_usd / 10000` is at most `max_acceptable_loss_usd` (the tool truncates the cap to whole basis points) unless the cap is 10000. Where a private relay delays inclusion, call `chain_confirmation_depth` for the class (`finality_model`, `value_at_risk_usd`, `loss_tolerance_usd`, `block_time_s`, `observed_max_reorg_blocks`) and state the extra wait in the policy.
7. Using `read` on the relay terms in the task: a private route is not a guarantee: it hides the transaction from the public pool but trusts the relay operator and builder; say which party gains visibility. Note when the transaction is not private by construction (an `eth_sendRawTransaction` to a public endpoint is public).
8. `write` `chain/plans/<task_id>.mev.md` with the table (class, route, exposure, `slippage_bps_cap`, deadline rule, trust assumptions) and call `shared_artifact_digest` with it.

## Outputs

- `chain.infra.plan` section on submission routing (route per class, slippage caps, deadline rule, trust assumptions), citing tool output.

## Failure Handling

- INVALID_INPUT (`slippage_bps` or `value_usd` out of schema range, `max_acceptable_loss_usd` of 0): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the classes not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Relay names, bundle rules and builder policies change: take them from the task; for anything else write `unverified` and name no vendor.
- Never retry a submission; this skill sends none.

## Handover

- `to_agent`: `A11` (routing configuration) and `A29` (wallet or dapp default slippage); write to A11, list A29 in `remaining`. `goal`: apply the routing on testnet first; `decisions`: route and cap per class; `artifacts`: plan with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.
