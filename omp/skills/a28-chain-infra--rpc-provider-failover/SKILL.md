---
name: "a28-chain-infra--rpc-provider-failover"
description: "Use when a chain.rpc task needs a provider order, hedge delay and failover triggers for several RPC providers on one chain, proven to survive the loss of one."
disable-model-invocation: true
---

# RPC provider failover

## When to Use

- A `chain.rpc` task supplies measured providers (p95 latency, error rate, rate limit, lag) for one chain and wants the `rpc.policy` artifact.
- Read-only RPC planning is L2. Changing a production client or gateway to apply the policy is L3 (A11 applies it, with human approval).
- Per-provider request budgets and 429 handling are `skill://a28-chain-infra--rpc-rate-limit-tuning`; a live outage is `skill://a28-chain-infra--node-incident-response`.

## Procedure

1. `read` the task input and build one row per provider: `name` (a label, never a URL), `p95_latency_ms`, `error_rate_pct`, `rate_limit_rps`, `lag_blocks`, `archive`. Also take `demand_rps` and the target chain id. A missing measurement is BLOCKED needs=input; never estimate one.
2. Run `python3 scripts/chain_config_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <config dir> --json`. Exit 1 (status `fail`) means a major or blocker finding in `counts`; a `rpc-url-credentials` or `rpc-url-embedded-key` blocker stops the plan until the URL is moved to a secret reference. Exit 2 is a bad `--path`.
3. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` (the `eth_chainId` value the task recorded, omit it when none was recorded and say so), `mainnet_approved` and `approved_level: "L2"`. Continue only on `decision: allow`; `rpc-chain-mismatch` is a blocker that ejects that provider.
4. Call `chain_rpc_failover_plan` with `providers`, `demand_rps`, `max_error_rate_pct` (1 unless the task sets it; a default of this skill), `max_lag_blocks` (2 for Ethereum L1, otherwise the task value) and `need_archive` (true when the app calls historical state, `debug_*` or `trace_*`). Ineligible providers come back in `ineligible` with a reason (`error-rate`, `block-lag`, `no-archive`).
5. Cross-check the `chain_rpc_failover_plan` output: the names in `order` plus `ineligible` equal the input names; `order` is ascending by `p95_latency_ms`; the sum of `rate_limit_rps` over `order` equals `capacity_rps`. A difference is E-CONTRACT, not a rounding matter.
6. Apply the decision rules to the `chain_rpc_failover_plan` output. `eligible_count` below 2 (`single-provider`), `meets_demand` false (`capacity-short`) or `survives_single_loss` false (`no-n-minus-1`) means the plan fails: state the extra rate limit needed, never lower `demand_rps`. `hedge_after_ms` is the primary's p95 (the Tail at Scale rule: a second request after the 95th percentile adds about 5% load, the tool's hedge load of 1.05; hedged calls also count against each provider's rate limit). `primary_share_pct` is the share of the needed rate (demand plus hedge load) that the primary's rate limit alone can carry, capped at 100: at 100 the secondaries only ever see hedges and failover, so exercise them on testnet before relying on them.
7. When the task knows each provider's execution client, call `chain_client_diversity` with `layer: "execution"` and `clients: [{name, nodes}]` counting providers per client. Over 1/2 on one client (`major`) means failover between providers does not remove a client bug: record it as a risk.
8. `write` `chain/rpc/<task_id>.md`: ordered providers, `hedge_after_ms`, eject triggers (error rate above `max_error_rate_pct`, lag above `max_lag_blocks`, chain id differs) and the guard result. Call `shared_artifact_digest` with that path.

## Outputs

- `rpc.policy` (order, hedge, triggers, capacity, `network_guard` block), with the tool and script values cited.

## Failure Handling

- INVALID_INPUT (duplicate provider names, a value over a schema limit): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the providers not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and no policy.
- `decision: needs_approval` or `block`: `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]`; never retry.
- Provider limits change without notice: state the measurement date; do not assert a vendor limit that no tool returned.

## Handover

- `to_agent`: `A11` (applies the gateway config) and `A29` (client libraries); write one handover to A11 and name A29 in `remaining`. `goal`: apply this order and triggers on testnet first; `artifacts`: the policy with its digest; `decisions`: thresholds used; `verification`: partial until A11 tests the failover.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/rpc-provider-failover.md; edit the source and run scripts/build_agents.py -->
