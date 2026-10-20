---
name: "a28-chain-infra--l2-bridge-monitoring"
description: "Use when a chain.l2 task monitors a lock-and-mint bridge or a rollup withdrawal timeline: message lag, collateral invariant, challenge window, pause advice."
disable-model-invocation: true
---

# L2 and bridge monitoring

## When to Use

- A `chain.l2` task evaluates a bridge's in-flight messages and collateral, or how long a rollup withdrawal takes and where one is now.
- Monitoring and recommending is L2 (read-only). Pausing a bridge or changing its monitor in production is L3; a pause by a guardian key or multisig is L4 and the key policy is A30's. The agent recommends and never pauses.
- Alert wiring is A13's. A bridge exploit already under way is `skill://a28-chain-infra--node-incident-response` with `incident.alert` input; reorg depth for the L1 side is `skill://a28-chain-infra--reorg-handling`.

## Procedure

1. `read` the task data for both chains: bridge `locked_amount` (source side) and `minted_amount` (destination side) as decimal text taken at one agreed instant, the in-flight `messages` (`id`, `sent_at`, `delivered_at` omitted while in flight), `max_lag_minutes` (the delivery SLO) and whether the bridge is `paused`. Sources are the task's evidence; never query with credentialed URLs.
2. Call `chain_network_guard` once per chain (source and destination) with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`. Both must say `allow`; a source chain id that differs from the endpoint's is a blocker.
3. Call `chain_bridge_monitor` with `as_of` (ISO-8601 with offset, from `date -u +%Y-%m-%dT%H:%M:%SZ` through `bash`), `max_lag_minutes`, `pause_overdue_ratio` (0.1 unless the task sets it: a default of this skill), `paused`, `locked_amount`, `minted_amount`, `messages`.
4. Decision rules on the `chain_bridge_monitor` output. `undercollateralized` true (`minted_amount` above `locked_amount`) is the `undercollateralized` blocker: the bridge is insolvent, the signature of a forged-message exploit. `pause_recommended` true and `paused` false gives the `pause-recommended` blocker: BLOCKED needs=human-approval via step 8. `messages-overdue` (major): `overdue_count` messages in flight longer than `max_lag_minutes`. `late-deliveries` is minor. `paused-without-trigger` (info) asks for a review of resuming.
5. Cross-check the `chain_bridge_monitor` counts: `delivered_count + pending_count` equals `message_count`; `overdue_count` is at most `pending_count`; `late_delivered_count` is at most `delivered_count`; `p95_delivery_lag_min` is the p95 of the delivered lags only. A snapshot taken at different instants makes `collateral_surplus` meaningless: re-take it, do not average.
6. For a withdrawal question, call `chain_l2_withdrawal_window` with `rollup_type` (`optimistic` or `validity`), `initiated_at`, `as_of`, `batch_post_delay_min`, `proof_delay_hours`, `challenge_window_hours`, `l1_finality_min` and `max_wait_hours`. `stage` is one of `batch-posting`, `proof-pending`, `challenge-window` (`execution-delay` for a validity rollup with a delay), `l1-finality`, `claimable`; `remaining_hours` and `withdrawable_at` answer the user (delays and the ready time are rounded up to whole seconds). OP Mainnet uses a 7-day challenge window and Arbitrum One about 6.4 days (a dispute can add more); a window below 7 days is `challenge-window-below-7-days` (info), below 24 hours `challenge-window-too-short` (major, this tool's own floor); `wait-exceeds-policy` means a liquidity-provider fast exit is an option with its own trust assumptions.
7. Check consistency between `chain_bridge_monitor` and `chain_l2_withdrawal_window` when both run: a withdrawal that is `claimable` but whose bridge message is still pending after `max_lag_minutes` is a contradiction between the two sources, so report E-CONTRACT with both values and do not advise the user to claim.
8. `write` `chain/plans/<task_id>.md` (counts, p95 lag, collateral surplus, stage, pause recommendation, which condition fired) and call `shared_artifact_digest`. On `pause_recommended` call `shared_escalate` with `reason_code: "policy"`, `summary`, `needs: ["human-approval"]`.

## Outputs

- `chain.infra.plan` section on bridge and L2 health with explicit pause advice, tool values cited.

## Failure Handling

- INVALID_INPUT (a message sent after `as_of`, duplicate ids, an amount that is not decimal text): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the messages or chains not covered. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Rollup windows, proof systems and fast-exit rules are protocol specific and change: take them from the task or the rollup's docs, and say a figure is unverified when no source backs it.
- Never retry a pause; it is non-idempotent and not the agent's action.

## Handover

- `to_agent`: `A13` (alerts) and `A30` (guardian keys); write to A13, list A30 in `remaining`. `goal`: act on this recommendation; `blockers`: `human-approval` on a pause; `verification`: verified only if the cross-checks agreed.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/l2-bridge-monitoring.md; edit the source and run scripts/build_agents.py -->
