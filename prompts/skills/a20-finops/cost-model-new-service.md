---
name: a20-finops--cost-model-new-service
description: Use when a finops.model task needs a pre-launch cost and unit-economics model for a new service from its cloud.architecture, ending in a priced cost.report.
owner: a20-finops
tools: [finops_unit_cost, finops_egress_cost, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Cost model for a new service

## When to Use

- A `finops.model` task asks what a new or re-architected service will cost per request, user or transaction before launch, from the `cloud.architecture` A16 produced.
- Not for an already running service whose spend moved (`skill://a20-finops--anomaly-triage`), for a budget cap (`skill://a20-finops--budget-guardrail-setup`) or for changing the architecture (A16 owns it; you price it).
- Autonomy: the model is L2 analysis. A cost target the model cannot meet is reported, never negotiated away.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A16 `cloud.architecture`. List each component with its instance or service class, count, storage GB, and the flows between zones, regions and the internet. Prices, volume and the revenue per unit must come from the task or a rate card it names; none supplied is BLOCKED `needs=input`, never a remembered list price.
2. If IaC drafts exist, run `python3 scripts/finops_cost_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <iac-dir> --json`. Exit 0 is ok, exit 1 means status `fail` (a major finding, not a tool error), exit 2 is bad input. Take `counts` and `by_rule`: an `iac-oversized-instance` (review size and above; major at twice the review size), `iac-previous-generation-instance` or `iac-previous-generation-volume` finding is a prompt to review, not proof the size is wrong: price the design as drafted, and price the alternative only when the task supplies its rate; record the delta.
3. Build the `finops_unit_cost` `costs` input (1 to 100 items): one `{category, amount}` per category (for example `compute`, `storage`, `network`, `observability`, `other`; unique non-blank names, at most 60 characters; amounts of 0 or more), for one billing period, using 730 hours per month for hourly prices (the convention of public price calculators). Keep fixed (always-on) and variable (per request) amounts apart in your notes: only variable amounts scale with volume.
4. Call `finops_egress_cost` with `flows` (`name`, `kind` of `cross_az`, `cross_region` or `internet`, `monthly_gb`, `via_nat`), `rates` (`cross_az_per_gb`, `cross_region_per_gb`, `nat_processing_per_gb`) and `internet_tiers` (first tier `starts_at_gb` 0, strictly ascending). Its `total_cost` is the `network` amount of step 3; any other priced network line (load balancer, CDN fees) is added as its own category and named, because the tool excludes it.
5. Call `finops_unit_cost` with `costs`, `volume` (units served in the same period, above 0), `unit` (`request`, `user` or `transaction`) and, when supplied, `target_unit_cost` and `revenue_per_unit`. Read `verdict`: `loss_making` (finding `UC-LOSS`, unit cost above revenue per unit) means do not recommend launch as designed; `over_target` carries `UC-TARGET`, major when unit cost is above 1.25 x the target, else minor. `top_category_share_pct` of 60 or more (`UC-CONCENTRATION`, info) names the lever to study first.
6. Check the arithmetic: `total_cost` equals the sum of `costs` amounts (rounded to 4 decimals) and `unit_cost` x `volume` equals `total_cost` within `volume` x 0.000000005 (`unit_cost` is rounded to 8 decimals). Then call `finops_unit_cost` twice more with variable amounts and `volume` both scaled by 0.5 and by 2.0 (fixed amounts unchanged) to show unit cost at half and double load. A `verdict` that flips between runs is a finding for the report.
7. Write the `cost.report` with `write` at the path the task names, else `docs/finops/cost-model-<service>.md`: assumptions with their source, per-category amounts, `unit_cost`, `cost_per_1000_units`, `gross_margin_pct` when `margin_known`, the three-load table and the scan findings. State the inputs verbatim.
8. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "unit cost over target", "needs": ["human-approval"]}` only when `verdict` is `loss_making`, or `over_target` with a major finding and the task forbids launching over target.
9. Call `shared_artifact_digest` with `{path}` of the report, then see Handover.

## Outputs

- `cost.report`: assumptions, categories, `unit_cost`, margin, load sensitivity, scan counts; evidence is the cited tool outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (`costs`, `volume` or `unit`; amounts 0 or more, `volume` above 0) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction` and name the unpriced components in `open_questions`.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing`; never state a unit cost you did not compute.
- Script: exit 2 means fix `--path`; a `skipped` entry of `yaml-manifests` (PyYAML missing) is a named gap, not a clean scan.
- Unit-price drift between provider, region and contract is outside the tools: defer to the supplied rate card and say so.
- Two sources disagreeing (step 6) is E-CONTRACT: report both values and stop.

## Handover

- `to_agent`: `A16` (consumes `cost.report`; A17 reads the same report for the golden path). `goal`: confirm or change the architecture against the unit-cost verdict.
- `completed`: tools and runs; `decisions`: the verdict and the unit; `artifacts`: the report with its digest, producer `A20`; `open_questions`: unpriced components and unsupplied prices; `verification`: verified only when every cross-check agreed.
- End with `shared_handover_validate` then `shared_handover_write`.
