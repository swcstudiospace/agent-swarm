---
name: a20-finops--commitment-proposal
description: Use when a finops.model task asks whether and how much to commit to a savings plan or reservation; recommend a level with break-even evidence, never buy.
owner: a20-finops
tools: [finops_commitment_coverage, finops_budget_burn, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Commitment proposal

## When to Use

- A `finops.model` task has a usage history for an eligible service family (compute, database) and asks for a savings-plan or reservation level, or for a check of an existing one.
- Not for deciding which workloads move to spot (`skill://a20-finops--spot-adoption`) or for resizing (`skill://a20-finops--rightsizing-sweep`); those change the baseline you commit to and run first.
- Autonomy: the proposal is L2. Any purchase, exchange or term change is `commitment_purchase`, L4: a human buys. You never call a billing or purchase interface.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the `cloud.architecture` (A16) and any `rightsizing.plan` you own. Gather the usage series: on-demand-equivalent eligible spend per period (daily or hourly, one unit throughout; the tool takes 1 to 2160 periods), with spot-covered and already-reserved usage removed and the pending downsizes of the plan subtracted. This skill requires at least 90 periods (a convention, not a tool limit); fewer, or a series with a launch or migration ramp inside it, is BLOCKED `needs=input`.
2. Take `discount_pct` (commitment discount against on-demand for the term and payment option the task names; the tool accepts values above 0 and below 100) from the task; never from memory. Term and payment option are the human's choice: offer the shortest term the task allows first, because a longer term raises lock-in risk (the break-even utilization is 100 - `discount_pct` whatever the term).
3. Call `finops_commitment_coverage` with `usage`, `discount_pct`, and a first `commitment` equal to the tool's own baseline: run once with a placeholder `commitment` above 0 and take `suggested_commitment` (the nearest-rank 10th percentile of usage x (1 - discount), independent of the placeholder), then call again with that value. A `suggested_commitment` of 0 means no baseline exists: recommend no commitment. Leave `min_utilization_pct` (default 90) and `min_coverage_pct` (default 70) unless the task sets others.
4. Read the result: `utilization_pct` below `breakeven_utilization_pct` (equal to 100 - `discount_pct`) raises `CC-BREAKEVEN` (blocker) and always gives `under_utilized`, even when the caller's `min_utilization_pct` is lower: the commitment costs more than on-demand, so reject that level. `under_utilized` with `CC-UTIL` (major) means lower it; `under_covered` (`CC-COVERAGE`, minor) means room to add; `within_band` is acceptable. Prefer the level with the highest `net_savings` among `within_band` runs.
5. Run `finops_commitment_coverage` at two more levels, 0.8 and 1.2 times the chosen `commitment` (levels of this skill), and tabulate `net_savings`, `unused_commitment`, `uncovered_on_demand`, `coverage_pct`, `utilization_pct`. Then rerun the chosen level on the most recent 30 days of periods only: if `utilization_pct` drops by more than 5 points against the full run (a convention of this skill), usage is trending down and the proposal moves to the 0.8 level.
6. Cross-check with `finops_budget_burn` (`budget` = the period's budget from the task, `spend_to_date`, `period_start`, `period_end`, `as_of`, ISO-8601 with offset): the chosen `commitment` x periods in the budget period must fit inside the budget, and a `verdict` of `over_budget` or `will_overrun` is stated beside the proposal. A commitment never solves an overrun that is already underway.
7. Write the report with `write` at the path the task names, else `docs/finops/commitment-<family>.md`: usage window, `discount_pct` and source, the level table, the recommendation (or "no commitment"), risks (workload retirement, architecture change by A16, region move) and the L4 wording: "purchase by a human; not executed".
8. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "commitment purchase awaiting L4 approval", "needs": ["human-approval"]}` and return its `result_patch` as the BLOCKED `task.result`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report` (commitment section): chosen level, alternatives, `net_savings`, break-even, risks; evidence is the three `finops_commitment_coverage` runs.

## Failure Handling

- INVALID_INPUT: fix the named field (`usage` needs numbers, `discount_pct` the tool's accepted range) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name missing periods.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never compute coverage by hand.
- `net_savings` at or below 0 for every level: recommend no commitment; do not search for a level that "works".
- Provider-specific rules (plan families, exchangeability, term refunds) are not in the tool: defer to the provider's current terms and name that as an open question.
- Never retry a purchase: there is none; L4 actions end BLOCKED `needs=human-approval`.

## Handover

- `to_agent`: `A16` (consumes `cost.report`). `goal`: architecture review of the commitment risks; the human buyer is named in `open_questions`.
- `decisions`: level, `discount_pct`, term as proposed; `artifacts`: report digest, producer `A20`; `blockers`: `human-approval`; `constraints`: A20 never purchases; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.
