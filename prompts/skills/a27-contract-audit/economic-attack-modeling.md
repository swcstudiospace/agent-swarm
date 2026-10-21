---
name: a27-contract-audit--economic-attack-modeling
description: Use when an audit.economic task must test whether flash-loan price manipulation or an ERC-4626 first-depositor attack is profitable, with a scored severity.
owner: a27-contract-audit
tools: [audit_flashloan_profit, audit_vault_inflation, audit_score_severity, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- An `audit.economic` task, a vault or AMM-dependent pricing path, or a hypothesis such as "borrow, swap, exploit, repay in one transaction".
- Oracle-specific cost (spot vs TWAP vs Chainlink) is `skill://a27-contract-audit--oracle-review`. Proving a hypothesis in code is `skill://a27-contract-audit--finding-writeup-poc`.
- Modeling is L2 and simulation-only: no mainnet transactions, no keys. Changing the severity the model yields is L3.

## Procedure

1. `read` the blueprint and the in-scope code. Write each hypothesis in `audit/economic-model.md`: victim, attacker capital, what price or share is manipulated, the source of profit, number of independent preconditions. A hypothesis without a profit source is dropped, not modeled.
2. Price manipulation: call `audit_flashloan_profit` with `reserve_in`, `reserve_out` (pool reserves in token units, read from the pool state in the test environment), `borrow_amount`, `fee_bps` (the pool's swap fee, 30 for a Uniswap V2 pair), `flashloan_fee_bps` (read the lender's current premium; the tool example is 5 for Aave V3, confirm it, it can be changed by governance), `in_token_price_usd`, `exploit_gain_usd` (value the code path really yields at the shifted price, computed from the contract, never guessed), `fixed_cost_usd` (gas).
3. Sweep `borrow_amount` at 1, 5, 10, 25 and 50 percent of `reserve_in` (read `borrow_pct_of_reserve` back). Decide on `profitable` and `net_profit_usd`: any profitable point means finding `FLASH-PROFIT` (high or above); no point profitable means info only, and `break_even_gain_usd` is reported so the team sees the margin.
4. Vault share math: call `audit_vault_inflation` with `mode` (`legacy` for assets x supply / total, `openzeppelin_virtual` plus `decimals_offset` for OpenZeppelin 4.9+/5.x `_decimalsOffset()`), `initial_shares`, `initial_assets` (0 for a fresh vault), `attacker_deposit` (1 for the classic attack), `donation`, `victim_deposit`. First run `donation` 0 and read `donation_to_zero_shares`; rerun with that donation and expect `victim_shares` 0 (`VAULT-ZERO-SHARES`, critical) when unprotected.
5. Read the thresholds from the output: `victim_loss_pct` of 1 or more is high (`VAULT-INFLATION`); one basis point or more is medium (`VAULT-ROUNDING`). `attacker_cost_to_zero_shares` is the attacker's capital: compare with `attacker_profit`. Rerun with the proposed mitigation (virtual offset, dead shares, minimum first deposit) and cite the new `victim_loss_pct`; a mitigation not re-run is not verified.
6. Score each confirmed attack with `audit_score_severity`: `loss_usd` from the model (not above `tvl_usd`), `tvl_usd`, `attacker_privilege` (`permissionless` for flash loans), `preconditions`, `irrecoverable`. Cross-check: the attacker's `net_profit_usd` or `attacker_profit` can not exceed the victims' loss; if it does the model is inconsistent (E-CONTRACT).
7. Call `shared_verdict` with `severities` = the step 6 severities mapped to the gate scale (this skill's convention: info to `info`, low and medium to `minor`, high to `major`, critical to `critical`); `fail` must coincide with a high or critical `severity` from step 6, else E-CONTRACT. Digest `audit/economic-model.md` with `shared_artifact_digest`.

## Outputs

- Economic analysis inside `audit.findings`: one entry per profitable attack with inputs, `net_profit_usd` or `victim_loss_pct`, `impact`, `likelihood`, severity; models that failed stay as "not profitable at these inputs".

## Failure Handling

- INVALID_INPUT (reserve zero, `loss_usd` above `tvl_usd`, legacy vault with shares but no assets): fix the named field from real pool state; never use round placeholders.
- A result depends on a lender fee, pool fee or price I could not read: say which input is assumed and mark `verification` partial.
- Constant-product models ignore concentrated liquidity, multi-hop routes and MEV competition: state that limit; a "not profitable" result is not a safety proof.
- Disagreement between two tools (profit vs loss): E-CONTRACT, report both values.

## Handover

- `to_agent`: `A26` for fixes (or `A10` when a gate waits). `artifacts`: model file with digest. `decisions`: which hypotheses were dropped and why. `open_questions`: unverified inputs. Next stage: `skill://a27-contract-audit--finding-writeup-poc`.
- End with `shared_handover_validate`, then `shared_handover_write`.
