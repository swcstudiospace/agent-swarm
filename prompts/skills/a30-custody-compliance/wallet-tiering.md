---
name: a30-custody-compliance--wallet-tiering
description: Use when a custody.policy task must set hot, warm and cold wallet caps and a minimum cold share, check each tier's signers and list sweep amounts.
owner: a30-custody-compliance
tools: [shared_handover_read, custody_wallet_tier_caps, custody_multisig_policy, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Wallet tiering

## When to Use

- A `custody.policy` task needs the split of holdings between hot (operational), warm (replenishment) and cold (reserve) wallets, with caps and a sweep rule.
- Caps and sweep lists are L2 design. Every sweep or refill is fund movement: L4, human-executed; this skill lists amounts and never moves anything.
- Limits inside one signing service are `skill://a30-custody-compliance--signer-policy-authoring`; a single transaction is `skill://a30-custody-compliance--tx-policy-evaluation`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `chain.infra.plan` (A28: which wallets and chains exist, withdrawal flow) and collect from the task the current `balance_usd` per tier and the usual daily outflow in USD. A tier without a balance figure is BLOCKED needs=input; never estimate a balance.
2. Set the caps for `custody_wallet_tier_caps`. Starting proposal of this skill (replace with the project's numbers when given): hot `max_pct_of_total` 5 and `max_balance_usd` at about two days of outflow, warm `max_pct_of_total` 15, cold `max_pct_of_total` 100, `min_cold_pct` 80. Hot is meant to be small enough that its loss is survivable.
3. Call `custody_wallet_tier_caps` with `tiers: [{tier, balance_usd, max_pct_of_total, max_balance_usd}]` (`max_balance_usd` optional, at most one entry per tier) and `min_cold_pct`. The cap of a tier is `min(max_pct_of_total x total, max_balance_usd)`; a balance equal to its cap is within it. Findings: `hot-over-cap` and `warm-over-cap` are major, `cold-over-cap` minor, `cold-below-minimum` major. `verdict: fail` means a sweep list is required (for `cold-below-minimum` alone `sweep_required_usd` is 0: the list is a top-up of cold from the tiers above it).
4. Cross-check the `custody_wallet_tier_caps` output, allowing 0.01 USD per figure because every figure is rounded independently to 2 places: `total_usd` equals the sum of the three balances; each `tiers[].pct_of_total` equals balance over total (4 places); `sweep_required_usd` equals the sum of `tiers[].excess_usd`; `hot_pct` and `cold_pct` match their rows. A larger difference is E-CONTRACT: report both values.
5. For every tier call `custody_multisig_policy` with that tier's `threshold` and `signers`. Strength must not fall as value rises: `compromise_tolerance` of cold is at least that of warm, and warm at least that of hot, and `hot_signers` must stay below the threshold for warm and cold. A weaker colder tier is redesigned, not accepted.
6. Write the sweep list for a failing result (`write` into `custody/<wallet-set>.sweep.md`): per tier the `excess_usd` to move out, the target tier (hot to warm, warm to cold) and the rule that a refill needs the destination tier's own threshold. State that the list is advisory and execution is an L4 human step with the stated signers.
7. `write` `custody/<wallet-set>.tiers.json` as `{"wallet_tiers": {<step 3 input>}}`, then run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json`; its `wallet_tiers:<id>` findings must equal step 3.
8. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "tier sweep is fund movement and needs L4 human execution", needs: ["human-approval"]}` when `sweep_required_usd` is above 0. Call `shared_artifact_digest` for the policy and sweep files, then see Handover.

## Outputs

- `custody.policy.set` (tier caps JSON, per-tier signer checks, sweep list with USD amounts).

## Failure Handling

- INVALID_INPUT (a tier listed twice, a percentage above 100): fix the named field once, then BLOCKED needs=input. Tiers beyond the three names are not supported: fold them into the nearest tier.
- PARTIAL_SUCCESS: use `completed_fraction` and name the tier not checked. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Balances are a snapshot; state the time of the figures. Stale or unsourced balances mean a `partial` result. Never retry or "re-run" a sweep: it is non-idempotent and human-only.

## Handover

- `to_agent`: `A28` (it operates the wallets and monitoring for the caps). `goal`: monitor tier balances against these caps; `decisions`: cap values and why; `artifacts`: tiers JSON and sweep list with digests; `blockers`: `human-approval` when a sweep is due; `verification`: partial unless every balance was sourced.
- End with `shared_handover_validate`, then `shared_handover_write`.
