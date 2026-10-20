---
name: a20-finops--egress-reduction
description: Use when a finops.model task finds data-transfer spend dominant and needs flows priced (cross-AZ, cross-region, NAT, internet) with a before/after plan.
owner: a20-finops
tools: [finops_egress_cost, finops_unit_cost, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Egress reduction

## When to Use

- A bill or a cost model shows network and data-transfer spend as the largest or fastest-growing category, or a new design moves data between zones, regions or to the internet.
- Not for storage tiering (`skill://a20-finops--storage-lifecycle`), for choosing where to run workloads for carbon (`skill://a20-finops--carbon-aware-placement`) or for changing the network design (A16 owns topology; you price the flows).
- Autonomy: the study is L2. Re-routing traffic, adding VPC endpoints or a CDN is an IaC change by A11 (L3 in production).

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A16 `cloud.architecture` and the flow data in the task (1 to 200 flows): per flow `name`, `kind` (`cross_az`, `cross_region`, `internet`), `monthly_gb` (0 or more) from flow logs or the bill, and whether it goes `via_nat`. Flows taken from a diagram without measured volume are marked "estimated"; none at all is BLOCKED `needs=input`.
2. Take the rate card from the task: `rates` with `cross_az_per_gb`, `cross_region_per_gb`, `nat_processing_per_gb`, and `internet_tiers` (`starts_at_gb` cumulative monthly GB, the first at 0, strictly ascending, `per_gb`; a free allowance is a leading tier with `per_gb` 0). Where the provider bills transfer in both directions, pass the summed effective per-GB rate for `cross_az_per_gb`; say so in the report.
3. Call `finops_egress_cost` with `flows`, `rates`, `internet_tiers`. Read `total_cost`, `breakdown` (`cross_az`, `cross_region`, `internet`, `nat_processing`, each with `gb` and `cost`; a `via_nat` flow is charged its own rate plus the NAT processing rate), `top_flows` (ten at most, by cost) and `dominant_category` (empty when none reaches 50%). A category at 50% or more of `total_cost` is the finding (`EG-<category>`, minor) and carries the tool's advice.
4. Pick levers from the `finops_egress_cost` `breakdown` by category: `cross_az` is topology-aware routing and zonal replicas (a trade against availability: the AZ count stays A16's decision); `cross_region` is replication or caching near consumers; `internet` is a CDN, compression or a private path; `nat_processing` is private-endpoint style paths for provider-service traffic instead of the NAT gateway (whether such an endpoint carries an hourly or per-GB charge is a provider fact: take it from the supplied rate card and run the numbers).
5. Model each lever as changed flows: copy the `flows` array, move or reduce the `monthly_gb` the lever affects (for example set `via_nat` false for the flows that a private endpoint would carry, or cut `internet` GB by the measured CDN hit ratio) and call `finops_egress_cost` again. The saving is the difference of `total_cost`; keep one run per lever, so the effects are not mixed. A lever that adds a priced component (an endpoint, CDN requests) subtracts that cost explicitly from the saving; it is not in the tool. These are what-if runs: nothing is applied.
6. Cross-check: the `breakdown` costs sum to `total_cost` within rounding (each is rounded to 2 decimals), and no `top_flows[].cost` exceeds it. Then call `finops_unit_cost` with `costs` (the transfer total as category `network` plus the other categories the task gives), `volume` (above 0) and `unit`: the new `top_category` and `cost_per_1000_units` show whether the change moves unit economics (see `skill://a20-finops--cost-model-new-service`).
7. Write the `cost.report` with `write` at the path the task names, else `docs/finops/egress-<scope>.md`: baseline, per-lever saving, risks (availability for `cross_az`, staleness for caches, latency for relocation), measured versus estimated flows, rates and their source.
8. A lever touching production routing needs approval: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "production network cost change", "needs": ["human-approval"]}`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report` (transfer section): baseline, dominant category, per-lever saving and risk; evidence is the `finops_egress_cost` runs.

## Failure Handling

- INVALID_INPUT: fix the named field (the first tier must start at 0 GB, tiers strictly ascending, `rates` keys complete) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name flows without volume.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Rates differ by provider, region pair and contract: use only the supplied card; a missing rate is a question, not a default.
- The tool excludes inter-service charges that are not transfer (load balancer capacity units, CDN request fees): name them as outside the figure.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A16` (consumes `cost.report`; the architecture owner decides topology levers). `goal`: choose levers, then A11 implements.
- `decisions`: levers with saving; `artifacts`: report digest, producer `A20`; `open_questions`: estimated flows; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.
