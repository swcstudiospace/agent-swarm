---
name: "a27-contract-audit--oracle-review"
description: "Use when a contract reads a price from a spot AMM, TWAP, Chainlink feed or admin setter and manipulation cost versus profit and feed hygiene must be checked."
disable-model-invocation: true
---

## When to Use

- Any in-scope code that prices collateral, shares or swaps from an oracle: `getReserves`, `slot0`, `observe`, `consult`, `latestRoundData`, or an owner-set price.
- Generic flash-loan or vault economics without an oracle is `skill://a27-contract-audit--economic-attack-modeling`. Who may change the oracle address is `skill://a27-contract-audit--access-control-review`.
- Modeling and review are L2, simulation only. A severity different from the computed one is L3.

## Procedure

1. `grep -n` the oracle reads and `read` each consumer: which `oracle_type` it is (`spot_amm`, `twap`, `chainlink`, `admin_set`), what value is protected by the price (extractable profit), and whether the price is read in the same transaction that moves value.
2. Spot AMM price (`spot_amm`): call `audit_oracle_risk` with `oracle_type`, `pool_liquidity_usd` (both sides), `target_deviation_pct` (the price move the exploit needs), `extractable_profit_usd` (from the contract path), `fee_bps` (pool fee; the tool assumes 30 if omitted, so pass it), `flashloan_liquidity_usd`, `attacker_capital_usd`, `flashloan_fee_bps`. A spot price is always at least `ORACLE-SPOT` high; `profitable` and `feasible` both true is `ORACLE-ATTACK` critical; profitable but `required_capital_usd` above the capital is medium.
3. TWAP (`twap`): add `twap_window_s`, `hold_blocks` (blocks the attacker can control, for example a validator or builder), `block_time_s` (12 on Ethereum mainnet, read it for other chains). A window under 1800 s is flagged `ORACLE-TWAP-WINDOW` medium (the tool's heuristic floor, not a standard). The cost is blocks x capital lost per block: a longer window or a deeper pool raises it.
4. Chainlink (`chainlink`): read the consumer for `updatedAt` against the feed's heartbeat (read it from the feed's page, never assume 3600 s), `answer > 0`, and its own sanity bounds on the answer: Chainlink's API reference says `minAnswer`/`maxAnswer` are no longer used on most Data Feeds, so a contract that relies on them has no circuit breaker unless it checks the feed's aggregator itself (some feeds still enforce them: read the aggregator, do not assume). Pass `staleness_check` and `answer_bounds_check` (true only for a bound the contract itself enforces); for an L2 also `l2` true and `sequencer_uptime_check` (the L2 Sequencer Uptime Feed: answer 0 is up, 1 is down, plus a grace period after it comes back; Chainlink's example uses 3600 s). The tool only takes the boolean, so read the grace period in the code. Missing staleness or sequencer check is high; missing bounds check is medium.
5. Cross-check spot results with the second tool: call `audit_flashloan_profit` with `reserve_in` = `reserve_out` = `pool_liquidity_usd` / 2, `in_token_price_usd` 1, `borrow_amount` = `required_capital_usd` (with `attacker_capital_usd` 0), the same `fee_bps` and `flashloan_fee_bps`, `fixed_cost_usd` 0, `exploit_gain_usd` = `extractable_profit_usd`. Valid only when step 2 used `attacker_capital_usd` 0 (rerun it so if not). `total_cost_usd` must equal `attack_cost_usd` within 0.02 USD; a larger gap is E-CONTRACT (inputs differ), fix the inputs before citing either.
6. Score each failing path with `audit_score_severity` (`loss_usd` from the path, `tvl_usd`, `attacker_privilege` `permissionless` for AMM pushes, `preconditions`, `irrecoverable`). The `risk` from `audit_oracle_risk` is a model result (`none` when the run has no findings); the finding severity is the scored `severity`. Keep `impact`, `likelihood` in the finding.
7. Recommend, never apply: TWAP window long enough for the pool depth, a pool deep enough that `attack_cost_usd` exceeds `extractable_profit_usd` with margin, a Chainlink wrapper with the checks above, a fallback oracle with a deviation bound. Re-run `audit_oracle_risk` with the proposed parameters and cite the new `net_gain_usd`.
8. `write` the review to `audit/oracle-review.md` (inputs table, outputs, decision per oracle) and call `shared_artifact_digest` with its `path`.

## Outputs

- Oracle part of `audit.findings`: finding per failing path with `oracle_type`, `attack_cost_usd`, `net_gain_usd`, scored severity; passes recorded with their inputs.

## Failure Handling

- INVALID_INPUT (a field required for the type missing, for example `twap_window_s` for `twap`): supply it from the code or BLOCKED `needs=input`; never use a default.
- Constant-product xyk is the only AMM modeled: concentrated-liquidity (Uniswap V3) ranges, stable pools and multi-pool routes are not; say so and mark the result partial. A TWAP is modeled as an arithmetic mean (Uniswap V2 cumulative price); Uniswap V3 averages ticks geometrically, which needs a larger push for the same upward move, so the TWAP cost is a lower bound there.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Feed addresses, heartbeats and bounds are chain facts the tools cannot see; mark them "from code" or "unverified", never from memory.

## Handover

- `to_agent`: `A26` (owner of the fix) or `A10` when a gate waits. `decisions`: per-oracle verdicts. `artifacts`: review file with digest. `open_questions`: unverified feed parameters. `verification`: verified when the step 5 cross-check agreed.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/oracle-review.md; edit the source and run scripts/build_agents.py -->
