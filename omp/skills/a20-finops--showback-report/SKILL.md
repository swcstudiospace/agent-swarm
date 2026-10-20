---
name: "a20-finops--showback-report"
description: "Use when a finops.model task needs a periodic showback of shared platform cost to teams, exact to the cent, with unallocated spend reported rather than hidden."
disable-model-invocation: true
---

# Showback report

## When to Use

- A period closed and the task needs each team's direct plus shared cost (clusters, networking, observability, support), for information, not for charging.
- Not for finding why a team's number rose (`skill://a20-finops--anomaly-triage`) or for fixing missing tags (`skill://a20-finops--tagging-remediation`, run it first when coverage is poor).
- Autonomy: the report is L2. A chargeback that moves money between budgets or changes a team's budget is `budget_change`, L3, with human approval.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A11 `environment.record` and the cost export of the period: `shared_cost` (the pool to spread, in whole cents) and per team `direct_cost`. Take from the task the usage metric per team (CPU-hours requested, requests, GB stored) if the usage method is wanted, and its source. No export is BLOCKED `needs=input`.
2. Check allocatability first: call `finops_tag_coverage` with `resources` (the export lines as `id`, `monthly_cost`, `tags`; at most 500 per call, so batch larger exports and add the `untagged_cost` and costs up) and `required_keys` `["team"]` (or the key the task names). Read `cost_coverage_pct` and `untagged_cost`. Below 95% by cost, show the unallocated amount as its own row, `unallocated`, and do not spread it silently; below 80% say the showback is indicative only (both thresholds are conventions of this skill; the tool's own verdict uses count coverage with a default of 95).
3. Choose the method with this skill's rule and write it in the report: `usage` when every team has a measured metric of the same unit (a team with usage 0 receives nothing: the tool reports `SB-<team>-idle`); `proportional` (by direct cost) when no metric exists and the pool follows spend; `even` only for a fixed per-team platform fee. Never pick the method that flatters a team.
4. Call `finops_showback` with `shared_cost` (at most 2 decimals), `method`, `consumers` (`team`, `direct_cost`, `usage`; team names unique; `proportional` needs a positive total `direct_cost`, `usage` a positive total usage). Floors per consumer are in cents and leftover cents go to the largest fractional remainders, ties by team name.
5. Cross-check the `finops_showback` result: `allocated_total` equals `shared_cost` exactly, `shared_allocation` summed over `allocations` equals it, and each `total_cost` equals `direct_cost` plus `shared_allocation`. Any difference of even one cent is E-CONTRACT: stop. `share_pct` (each team's share of the shared pool, not of its total cost) sums to 100 within 0.01.
6. Read `largest_consumer` and the information findings `SB-<team>` (a team at 50% or more of the pool when there is more than one). Run the method comparison once: call `finops_showback` again with another admissible method and list the teams whose `total_cost` changes by more than 10% (a convention of this skill); those teams are told which method and why.
7. For one product-level view, call `finops_unit_cost` per team with `costs` (`direct`, `shared`), the team's `volume` and `unit`, so the report shows cost per unit and not only totals. Skip it for teams with no volume figure and say so.
8. Write the `cost.report` with `write` at the path the task names, else `docs/finops/showback-<period>.md`: method and reasoning, the allocations table, the `unallocated` row, the comparison, and a note that it is a showback (informational). A request to charge back is `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "chargeback needs budget approval", "needs": ["human-approval"]}` (L3).
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report` (showback section): allocations to the cent, unallocated spend, method and reason, unit costs; evidence is the `finops_showback` outputs and the tag coverage.

## Failure Handling

- INVALID_INPUT: fix the named field (`shared_cost` with more than 2 decimals, duplicate `team`, method needing a positive total) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name teams without usage.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never allocate by hand.
- Credits, refunds and amortised commitments change the pool: take them from the export as the task defines them; if undefined, ask.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A17` (consumes `cost.report`, publishes it to the teams). `goal`: distribute the showback and collect disputes.
- `decisions`: method and the unallocated treatment; `artifacts`: report digest, producer `A20`; `open_questions`: teams without usage or tags; `verification`: verified only when the cent checks agreed.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a20-finops/showback-report.md; edit the source and run scripts/build_agents.py -->
