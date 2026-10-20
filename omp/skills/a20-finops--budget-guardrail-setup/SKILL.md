---
name: "a20-finops--budget-guardrail-setup"
description: "Use when a finops.budget task needs a budget and alert-threshold policy for an environment, calibrated on real burn, handed to A11 as a budget.policy."
disable-model-invocation: true
---

# Budget guardrail setup

## When to Use

- A `finops.budget` task asks for a budget amount, alert thresholds and a response for one environment or product, using the `environment.record` A11 holds and recent spend.
- Not for explaining a spike that already happened (`skill://a20-finops--anomaly-triage`) or for dividing a shared cost across teams (`skill://a20-finops--showback-report`).
- Autonomy: designing the policy is L2. Creating or changing the budget in the provider, or any enforcement (blocking deploys, stopping resources) is `budget_change`, L3: A11 applies it after human approval. Stopping production spend is never automatic.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A11 `environment.record` and any A12 `release.record` that names a launch date. Take environment name, tier (production or not), owner and the period (month is the default). A missing owner or tier is BLOCKED `needs=input`.
2. Get the spend history from the task: one daily series of consecutive days, oldest first, at least 28 days (a convention of this skill; the tool itself needs 7 to 366). Call `finops_spend_anomaly` with `daily_spend`, `start_date` (YYYY-MM-DD) and the default `threshold` 3.5. `anomaly_count` above 0 means the history contains a spike: calibrate on `median` and say so; do not size the budget on spike days.
3. Pick the candidate budget: `median` times days in the period, plus the planned growth the task states (a launch in `release.record` counts only when A12 gave a date). No growth stated means 0, never a guess. Keep the arithmetic in the policy.
4. Call `finops_budget_burn` with `budget` (candidate), `spend_to_date`, `period_start`, `period_end`, `as_of` (all ISO-8601 with an explicit offset, for example `2026-10-01T00:00:00Z`; `as_of` after `period_start` and not after `period_end`) and `recent_daily_spend` (last 7 days; the tool allows 1 to 31). Read `burn_ratio` (share of budget used over share of period elapsed), `projected_total`, `verdict` (`over_budget` when spend has reached the budget, `will_overrun` when projected above it, `at_risk` when projected is at least 90% of it, else `on_track`) and `overrun_at` (empty unless `will_overrun`).
5. Cross-check the run rate: call `finops_budget_burn` again without `recent_daily_spend`. `run_rate_source` becomes `period_average`; a `projected_total` more than 15% apart (a convention of this skill) means spend changed inside the period (a trend): report both and plan on the higher.
6. Set thresholds from the `finops_budget_burn` verdicts, as percent of budget. The levels are a convention of this skill, not a provider rule: 50% actual (information), 80% actual (warning to the owner), 100% forecasted (page the owner: the `will_overrun` condition) and 100% actual (escalate to the budget approver). Production is notify-only; for non-production only propose "scale to zero after hours at 100% actual" as an L3 enforcement proposal that A11 applies from a plan or diff after human approval.
7. Write the `budget.policy` as JSON with `write` at the path the task names, else `docs/finops/budget-<env>.json`: `environment`, `tier`, `period`, `budget`, `currency` (from the task), `thresholds` (`percent`, `basis` actual or forecast, `action`), `owner`, `review_date`, `calibration` (the `finops_spend_anomaly` and both `finops_budget_burn` results cited verbatim) and `approval`: `L3 pending`.
8. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "budget change awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as the BLOCKED `task.result`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `budget.policy`: budget, thresholds, actions, owner, calibration evidence; status `L3 pending`.

## Failure Handling

- INVALID_INPUT: fix the named field (offsets missing on a timestamp are the common cause; `as_of` outside the period is `as_of`'s error) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name missing days.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no thresholds without a burn run.
- `finops_spend_anomaly` needs at least 7 days (BLOCKED `needs=input` with fewer); fewer than 28 days breaks this skill's calibration rule: say the budget is indicative.
- Weekly seasonality is not modelled by the burn tool: if weekends differ, say the run rate is approximate.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A11` (consumes `budget.policy`). `goal`: create the budget and alerts in the named environment once approved.
- `decisions`: budget and its basis; `artifacts`: policy digest, producer `A20`; `blockers`: `human-approval`; `constraints`: no enforcement in production; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a20-finops/budget-guardrail-setup.md; edit the source and run scripts/build_agents.py -->
