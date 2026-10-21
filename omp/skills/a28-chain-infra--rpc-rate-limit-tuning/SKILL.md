---
name: "a28-chain-infra--rpc-rate-limit-tuning"
description: "Use when a chain.rpc task must split provider rate limits between live reads, logs, backfill and writes and set retry, backoff, batching and caching rules."
disable-model-invocation: true
---

# RPC rate-limit tuning

## When to Use

- A `chain.rpc` task sees HTTP 429 or provider limit errors, or plans new traffic (a backfill, a new keeper) against existing provider limits, and wants the budget part of the `rpc.policy`.
- Policy writing is L2. Applying new limits or retry settings in production clients is L3 for A11.
- Choosing which provider is primary is `skill://a28-chain-infra--rpc-provider-failover`; sizing one backfill is `skill://a28-chain-infra--indexer-backfill`. This skill divides capacity among traffic classes.

## Procedure

1. `read` the task data per traffic class: `live` (head polling, user reads), `logs` (`eth_getLogs`), `backfill`, `writes` (`eth_sendRawTransaction`), each with measured peak rps, plus per provider `rate_limit_rps`, `p95_latency_ms`, `error_rate_pct`, `lag_blocks`, `archive`. Take the unit the provider uses (requests or compute units per second) from the task; never convert units yourself.
2. Run `python3 scripts/chain_config_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <config dir> --json`. A blocker for credentials or API keys in a URL stops the work: the limit belongs to the key, so a leaked key is also a leaked budget. Exit 1 is a finding, exit 2 a bad path.
3. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`; continue on `decision: allow`.
4. Call `chain_rpc_failover_plan` with `providers`, `demand_rps` (the sum of peak rps over classes), `max_error_rate_pct`, `max_lag_blocks` and `need_archive`. The planning budget is `capacity_after_loss_rps` (capacity with the largest eligible provider gone), not `capacity_rps`. Use at most 70% of it in steady state (a default of this skill) so a spike or a provider loss does not turn into throttling.
5. Allocate by priority from the `chain_rpc_failover_plan` budget: `writes` and `live` first, then `logs`, `backfill` last with whatever remains. Cross-check: the class budgets sum to at most 70% of `capacity_after_loss_rps`; `meets_demand` and `survives_single_loss` from the plan agree with that sum; a sum above the budget is a finding, not a rounding detail.
6. Size the backfill share with `chain_backfill_plan`: `provider_rate_limit_rps` is the backfill budget, `workers` your current setting, plus the range and latency inputs the task gives. `limited_by: rate-limit` and `workers` above `workers_needed` is wasted concurrency and 429s: set workers to `workers_needed`. `catches_up` false means the budget cannot follow the chain: say so and ask for more capacity, never starve `live`.
7. Write the client rules: on 429 or 5xx retry reads with exponential backoff and full jitter (250 ms base, 10 s cap, 5 attempts; defaults of this skill) and honor `Retry-After` when sent; reads are idempotent, so no retry storm: one shared limiter per provider. For `eth_sendRawTransaction` retry only the same signed bytes; the node answers `already known` or `nonce too low` once the transaction exists, which is success when the hash matches. Cap JSON-RPC batch size (50 by default; providers count each call in a batch), cache immutable results by block hash, and subscribe to `newHeads` over a websocket instead of polling faster than `block_time_s`.
8. `write` `chain/rpc/<task_id>.budget.md` with the class table, the retry rules and the cited tool values, and call `shared_artifact_digest` with it.

## Outputs

- `rpc.policy` budget section: per class rps, retry and batching rules, backfill workers, headroom, `network_guard` block.

## Failure Handling

- INVALID_INPUT (duplicate provider names, `to_block` before `from_block`): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the classes without a measured rps. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Vendor compute-unit prices and burst rules change: use only what the task states, and write `unverified` for the rest.
- A retry rule is advice for the client owner: this skill never sends traffic and never retries a non-idempotent call.

## Handover

- `to_agent`: `A11` (client and gateway settings) and `A29` (wallet or dapp clients); write to A11, list A29 in `remaining`. `goal`: apply budgets on testnet first; `decisions`: headroom 70%, retry defaults; `artifacts`: budget with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/rpc-rate-limit-tuning.md; edit the source and run scripts/build_agents.py -->
