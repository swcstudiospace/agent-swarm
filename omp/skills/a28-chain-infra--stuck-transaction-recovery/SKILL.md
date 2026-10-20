---
name: "a28-chain-infra--stuck-transaction-recovery"
description: "Use when a sender account has pending transactions that do not confirm (nonce gap, underpriced, aged) and replacement fee bids are needed for a human to sign."
disable-model-invocation: true
---

# Stuck transaction recovery

## When to Use

- A `chain.node` or `chain.rpc` task reports transactions of one or more sender accounts that stay pending: a nonce gap, a max fee below the base fee, or an age over the task's stuck limit.
- Computing the replacement bids is L2. Signing and broadcasting a replacement uses a key: this agent never does it. On a testnet the owning service or developer broadcasts; on mainnet it is an L4 human step under A30's key policy.
- Designing a fee policy so it does not recur is `skill://a28-chain-infra--gas-fee-strategy`; hiding transactions from searchers is `skill://a28-chain-infra--mev-protection-routing`.

## Procedure

1. `read` the evidence per account: a label or address (never a key), `confirmed_nonce` (the transaction count at `latest`), and every pending transaction's `nonce`, `max_fee_gwei`, `max_priority_fee_gwei`, `first_seen_at`, plus the current `base_fee_gwei` and the stuck limit in minutes. Take the pending list from the node's own txpool and the confirmed nonce from a second source; do not mix chains.
2. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`. A mainnet-grade target is allowed for analysis (`read`), but the broadcast step is marked L4 in the result.
3. Call `chain_stuck_transactions` with `as_of`, `base_fee_gwei`, `stuck_after_minutes` and `accounts`. Duplicate nonces in one account, a pending nonce below `confirmed_nonce`, or a priority fee above the max fee are INVALID_INPUT; they mean the data is inconsistent, so re-take it.
4. Read the counts. `nonce-gap:<account>` (major): `missing_nonces` nonces are absent, so `queued_count` transactions behind the gap can never execute until each gap nonce is filled with a replacement or a 0-value self-transfer. `stuck:<account>:<nonce>` is major when the fee is below the base fee (`underpriced`) and minor when only `aged`.
5. Use `replacements` from `chain_stuck_transactions`: each row's `min_max_fee_gwei` and `min_priority_fee_gwei` are at least 110% of the old values and strictly above them (go-ethereum's txpool rejects a replacement unless both fee fields are higher and at least `pricebump` percent higher, default 10; a zero tip therefore needs at least 1 wei; other clients may demand more) and never below `base_fee_gwei` plus the bumped tip. A bid that does not raise both is rejected as `replacement transaction underpriced`.
6. Cross-check with `chain_fee_policy`: with the latest block data (`latest_base_fee_gwei`, `latest_gas_used`, `gas_limit`, `elasticity_multiplier`, `base_fee_change_denominator`, `horizon_blocks`, `assumed_fullness`, `priority_fee_gwei`, `priority_cap_gwei`, `max_fee_cap_gwei`) its `max_fee_gwei` must not be below the replacement's `min_max_fee_gwei` unless a cap binds; when `capped` is true or `cap-below-next-base-fee` appears the replacement cannot be afforded under the owner's caps: that is a decision for the owner, never raise the caps here.
7. Write the plan in nonce order: fill gaps first (lowest nonce first, since later ones cannot move before it), then reprice the stuck ones; for a transaction that must not run, the cancel is a 0-value self-transfer at the same nonce with the bumped fees. State that a cancel is a race: the original may still confirm.
8. `write` `chain/plans/<task_id>.recovery.md` (table: account label, nonce, action, `min_max_fee_gwei`, `min_priority_fee_gwei`) and call `shared_artifact_digest` with it. On mainnet call `shared_escalate` with `reason_code: "policy"`, `summary: "replacement broadcast needs L4 human signing"`, `needs: ["human-approval"]`.

## Outputs

- `chain.infra.plan` section for recovery: replacement table, order, race warning, `network_guard` block.

## Failure Handling

- INVALID_INPUT (labels duplicated ignoring case, a pending nonce before `confirmed_nonce`, `first_seen_at` after `as_of`): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the accounts not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The 10% rule is the default of go-ethereum, Erigon, Besu and Nethermind for ordinary and EIP-1559 transactions; blob (type 3) transactions need a 100% bump, and a different client or an L2 sequencer may differ: where the task names another rule, use it and say so.
- Never retry a signing or broadcast; never print or request a key.

## Handover

- `to_agent`: `A30` (signing authority for the key) when keys sign; otherwise `A11` (the sender service owner). `goal`: sign and broadcast in this order; `blockers`: `human-approval`; `decisions`: bids and order; `artifacts`: plan with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/stuck-transaction-recovery.md; edit the source and run scripts/build_agents.py -->
