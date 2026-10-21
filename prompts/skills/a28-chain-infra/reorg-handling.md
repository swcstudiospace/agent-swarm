---
name: a28-chain-infra--reorg-handling
description: Use when an indexer or service needs a confirmation-depth and block-tag policy plus a rollback rule for reorgs, by finality model and value at risk.
owner: a28-chain-infra
tools: [chain_confirmation_depth, chain_indexer_lag, chain_network_guard, shared_artifact_digest, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Reorg handling

## When to Use

- A `chain.indexer` or `chain.rpc` task asks how many confirmations, or which block tag (`latest`, `safe`, `finalized`), a consumer may trust, and how an indexer undoes data after a reorg.
- Policy work is L2. Changing a production consumer's tag or depth is L3 for A11.
- Backfill sizing is `skill://a28-chain-infra--indexer-backfill`; a rollup withdrawal wait is `skill://a28-chain-infra--l2-bridge-monitoring`.

## Procedure

1. `read` the task for the consumer actions and, per action, `value_at_risk_usd` and `loss_tolerance_usd` (loss accepted if the transaction is reversed, at least 0.01), `block_time_s`, and `observed_max_reorg_blocks` from the node or indexer logs. No value at risk is BLOCKED needs=input; a guessed value gives a guessed policy.
2. Choose `finality_model` for `chain_confirmation_depth`: `ethereum_pos` for Ethereum L1 and chains with a beacon-style finality gadget, `probabilistic` for proof-of-work or longest-chain chains (add `attacker_share`, 0.001 to 0.49, a stated assumption), `instant_bft` for BFT chains with one-block finality.
3. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `mainnet_approved`, `approved_level: "L2"`; the finality model must match `chain_name` (a rollup is not `ethereum_pos`: its `latest` is the sequencer's word, see `skill://a28-chain-infra--l2-bridge-monitoring`).
4. Call `chain_confirmation_depth` once per action class. For `ethereum_pos` the tool returns `latest` when `risk_ratio` (value over tolerance) is at most 1, `safe` with 64 confirmations (2 epochs of 32 slots) up to 100, and `finalized` with 96 above that; an observed reorg of r blocks raises the depth to at least r + 1 (`reorg-history-dominates`, info, when r + 1 exceeds the 64 or 96 policy depth); `wait_seconds` is slots times `block_time_s` (12 s on Ethereum, so about 12.8 minutes for 64 slots). For `probabilistic`, `basis: nakamoto` means `required_confirmations` is z + 1: z is the smallest number of blocks after the transaction's block (Nakamoto, section 11) that keeps the attacker's catch-up probability under tolerance over value, and the block that holds the transaction is the first confirmation. `reorg-history-dominates` means an observed reorg of r blocks, which needs r + 1 confirmations, asks for more than the model.
5. Treat `bft-reorg-observed` (blocker) as an incident: a chain assumed final reorganised, so no depth is trusted until the cause is found. `shared_escalate` with `reason_code: "other"`, `needs: ["human-approval"]`.
6. Define the indexer rollback rule in the spec (`write` it with the table in step 8): store `block_number`, `block_hash` and `parent_hash` per block; when a new block's `parent_hash` differs from the stored hash at `block_number - 1`, walk back to the common ancestor, delete or mark rows above it (logs with `removed: true` on subscriptions are the same signal), reapply from the new branch in one transaction. A fork point at or below the `finalized` block is not a reorg: stop and escalate.
7. Check the rollback visibly with `chain_indexer_lag`: split samples at the rewind, because `head_block` decreasing is INVALID_INPUT; a window where `indexed_block` moves backwards yields the `indexer-rewound` (info) finding. After the rollback the next window must show `trend: closing` or `caught-up` and `within_slo`.
8. `write` `chain/plans/<task_id>.md` with the table (action class, `block_tag`, `required_confirmations`, `wait_seconds`, `basis`) and the rollback rule, and call `shared_artifact_digest` with it.

## Outputs

- `chain.infra.plan` section on confirmation depth, and the reorg rules inside `indexer.spec`, citing tool output and `observed_max_reorg_blocks`.

## Failure Handling

- INVALID_INPUT (`attacker_share` too high for any depth within 2000 blocks, `loss_tolerance_usd` of 0): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the action classes left. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The 64 and 96 block figures and the 100x ratio rule are the tool's policy, not protocol constants: when a consumer needs another rule, say it is the consumer's, not Ethereum's.
- Never retry a rollback: it is non-idempotent unless the transaction is atomic.

## Handover

- `to_agent`: `A13` (alerts on reorg depth, `indexer.spec` consumer) and `A11` (consumer config); write to A13, list A11 in `remaining`. `goal`: adopt this policy; `decisions`: finality model, tolerance values, assumptions; `artifacts`: plan with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.
