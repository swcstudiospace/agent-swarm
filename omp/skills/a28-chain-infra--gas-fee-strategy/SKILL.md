---
name: "a28-chain-infra--gas-fee-strategy"
description: "Use when a chain task needs an EIP-1559 fee policy for a sender service: max fee and priority fee with caps, runway in full blocks, and the cap that binds."
disable-model-invocation: true
---

# Gas and fee strategy

## When to Use

- A `chain.rpc` or `chain.node` task designs how a transaction-sending service (relayer, keeper, deployer) sets `maxFeePerGas` and `maxPriorityFeePerGas` on an EIP-1559 chain.
- Writing the policy is L2. Sending transactions is not done by this agent; a human or the owning service broadcasts, and mainnet deployment transactions stay an L4 human step.
- Transactions that are already stuck are `skill://a28-chain-infra--stuck-transaction-recovery`; hiding transactions from searchers is `skill://a28-chain-infra--mev-protection-routing`.

## Procedure

1. `read` the task and the latest block data it records: `latest_base_fee_gwei`, `latest_gas_used`, `gas_limit`, the chain's `elasticity_multiplier` (2 on Ethereum L1) and `base_fee_change_denominator` (8 on Ethereum L1). L2s use their own parameters and add an L1 data fee outside this tool: take parameters from the chain config, never reuse the L1 values for an L2.
2. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`; continue on `decision: allow`.
3. Choose the inputs: `horizon_blocks` (6 by default, about 2x base-fee growth if every block is full at 12.5% per block; a stated default of this skill), `assumed_fullness` (0.5 is the target, so a base fee that stays flat; use the measured average for a busy chain), `priority_fee_gwei` (the wanted tip), and the two caps `priority_cap_gwei` and `max_fee_cap_gwei` that the task owner sets as spend limits. A missing cap is BLOCKED needs=input.
4. Call `chain_fee_policy` with those fields. Reading it: `next_base_fee_gwei` is exact next-block base fee by the EIP-1559 integer rule; `worst_case_base_fee_gwei` assumes `horizon_blocks - 1` further full blocks; `max_fee_gwei` is the lower of `worst_case + priority` and the cap; `capped` true means the cap binds.
5. Decision rules. `cap-below-next-base-fee` (blocker): nothing under this policy can be included; raise `max_fee_cap_gwei` or stop the sender. `max-fee-capped` (major): the cap covers the base fee for only `full_blocks_survived` consecutive full blocks against the requested horizon; accept it only if the task says delayed inclusion is fine. `max-fee-trims-tip` (minor): the cap covers the worst-case base fee of the whole horizon but not the full tip at its end. `priority-capped` is informational. `effective_tip_next_gwei` below the wanted tip means the base fee leaves little room.
6. Cross-check the `chain_fee_policy` output against the EIP-1559 rule: with denominator 8 and a base fee above 8 wei, `worst_case_base_fee_gwei` must not exceed `next_base_fee_gwei * 1.125^(horizon_blocks - 1)` (below that the spec's 1 wei minimum raise is more than 12.5%); `max_fee_gwei` must be at most `max_fee_cap_gwei` and `priority_fee_gwei` at most `priority_cap_gwei`. Any breach is E-CONTRACT.
7. When the sender already has pending transactions, call `chain_stuck_transactions` with `as_of`, `base_fee_gwei` equal to `next_base_fee_gwei`, `stuck_after_minutes` and the `accounts` (labels only, `confirmed_nonce`, `pending` with `nonce`, `max_fee_gwei`, `max_priority_fee_gwei`, `first_seen_at`). Any `underpriced` finding shows the old policy was too low; carry its replacement fees into the plan.
8. `write` `chain/plans/<task_id>.md` with the inputs, outputs, the binding cap, the replacement rule (a replacement must raise both fees by at least 10%, the geth txpool default) and call `shared_artifact_digest` with it.

## Outputs

- `chain.infra.plan` section with a fee policy (formula, caps, runway) and the `network_guard` block.

## Failure Handling

- INVALID_INPUT (`priority_cap_gwei` above `max_fee_cap_gwei`, `latest_gas_used` above `gas_limit`): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name what was not computed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Blob (EIP-4844) fees and L2 data fees are outside this tool: say so in the plan and defer to the chain's fee API; never invent a figure.
- A fee policy is advice; a base fee jumps after the sample, so state the block number of the input.

## Handover

- `to_agent`: `A11` (sender configuration) and `A30` (signer limits: the caps belong in custody policy); write to A11, list A30 in `remaining`. `goal`: apply caps on testnet first; `decisions`: horizon, fullness, caps; `artifacts`: plan with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/gas-fee-strategy.md; edit the source and run scripts/build_agents.py -->
