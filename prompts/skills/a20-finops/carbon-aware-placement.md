---
name: a20-finops--carbon-aware-placement
description: Use when a finops.carbon task needs workload operational carbon and the value of moving to an eligible lower-intensity region, as a carbon.report.
owner: a20-finops
tools: [finops_carbon_estimate, finops_egress_cost, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Carbon-aware placement

## When to Use

- A `finops.carbon` task wants the location-based operational emissions of a service or fleet and a regional comparison, from the `cloud.architecture` A16 owns.
- Not for deciding which regions are legally or technically allowed (A16 owns residency and architecture: you take its eligible list) or for cost-only region moves (`skill://a20-finops--egress-reduction` prices transfer).
- Autonomy: the estimate and the comparison are L2. Relocating a workload is an architecture change (A16) and an apply (A11), L3 in production; you recommend.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A16 `cloud.architecture`. Take per workload `name`, `region`, the data-residency, latency and service-availability constraints, and the regions the architecture allows. A region is `eligible` only if A16's constraints allow it; a region you cannot justify is `eligible: false`, never "probably fine".
2. Collect power and time per workload: `avg_power_w` (average draw of the allocated hardware; the task supplies the source: provider or hardware figures, a measured value or a published instance power model) and `hours` for the period (730 per month for always-on). A power figure with no named source is BLOCKED `needs=input`.
3. Collect `pue` (facility energy divided by IT energy, ISO/IEC 30134-2; between 1 and 3, the tool refuses values outside) from the provider's published figure per region, or one fleet-wide value named in the task. Collect `grid_intensity`: per region `g_co2e_per_kwh` from one named dataset and year (a grid operator or a provider's published location-based factor), and `eligible`. Mixing datasets between regions makes the comparison meaningless: use one source.
4. Call `finops_carbon_estimate` with `workloads` (`name` unique, `region`, `avg_power_w`, `hours`), `pue`, `grid_intensity` (`region` unique, `g_co2e_per_kwh`, `eligible`) and `min_saving_pct` (default 10; pass it only when the task sets another). Every workload region needs a `grid_intensity` row.
5. Read `total_energy_kwh` (power x hours / 1000 x PUE), `total_kg_co2e`, `region_breakdown`, `best_region` (the lowest-intensity `eligible` region; empty means none is eligible), `potential_saving_kg`, `potential_saving_pct` and `verdict`: `relocation_candidates` only when the saving is above 0 and at least `min_saving_pct` of the total; else `no_action`. Per workload, `potential_saving_kg` is energy times the intensity difference to `best_region`, never negative.
6. Cross-check: `total_kg_co2e` equals the sum of `workloads[].kg_co2e` and of `region_breakdown[].kg_co2e`, within rounding (each value is rounded to 4 decimals). Then verify the move is not a net loss on cost: for each candidate call `finops_egress_cost` with the `flows` (`name`, `kind` `cross_region` or `internet`, `monthly_gb`, `via_nat`) that the move would create, plus `rates` (`cross_az_per_gb`, `cross_region_per_gb`, `nat_processing_per_gb`) and `internet_tiers` (first tier `starts_at_gb` 0) from the task's rate card. The added transfer cost is reported next to the avoided kg; a move whose added cost exceeds the carbon price or the budget the task allows is marked "not recommended on cost".
7. State the method limits in the report: location-based (grid average where it runs), operational only (embodied hardware emissions and network energy are excluded), one average intensity per region from one dataset and year (hourly or marginal intensity can differ, so time-shifting batch jobs is a separate study), and that provider-reported and your figures may differ by method. Write the `carbon.report` with `write` at the path the task names, else `docs/finops/carbon-<scope>.md`.
8. Relocation of production workloads needs approval: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "relocation candidates need architecture review", "needs": ["human-approval"]}`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `carbon.report`: kWh and kgCO2e per workload and region, best eligible region, candidate moves with the transfer cost, method limits; evidence is the `finops_carbon_estimate` and `finops_egress_cost` outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (`pue` between 1 and 3, a workload region missing from `grid_intensity`, duplicate regions) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name workloads without a power source.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no estimate by hand.
- Grid factors change every year and differ by method (location-based versus market-based under the GHG Protocol Scope 2 guidance): this tool is location-based only; if the task asks for market-based or Scope 3 reporting, say it is outside the tools.
- L3 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A16` (consumes `carbon.report`). `goal`: decide whether the candidate regions enter the architecture.
- `decisions`: data sources and `eligible` regions with who set them; `artifacts`: report digest, producer `A20`; `open_questions`: unsourced power or intensity figures; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.
