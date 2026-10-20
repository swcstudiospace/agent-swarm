---
name: a16-cloud-architect--cloud-nfr-translation
description: Use when requirements.spec and slo.manifest state availability, latency, recovery and throughput goals that must become checked cloud design parameters.
owner: a16-cloud-architect
tools: [cloud_composite_availability, cloud_latency_budget, cloud_dr_feasibility, cloud_cell_capacity, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Cloud NFR translation

## When to Use

- A `cloud.architecture` task starts from non-functional requirements (A02 `requirements.spec`) and SLOs (A13 `slo.manifest`) and needs one table of numbers the cloud design must meet, each proven feasible or flagged.
- Not for allocating NFRs across the application's components (A03 `nfr-allocation` does that), nor for defining new SLOs (A13 owns them).
- Autonomy: the translation is L2. An NFR that cannot be met without a new provider, region or commitment is raised as L3 or L4 and never silently relaxed.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the `requirements.spec` and the `slo.manifest`. List every NFR with value, unit, window and source. "Highly available" or "fast" without a number is `shared_escalate` with `reason_code: "ambiguous-input"` and `needs` naming the metric.
2. Reconcile the two sources: a requirement target must be at or below the SLO objective it will be measured by (target 99.95 needs an SLO of at least 99.95). Disagreement is `shared_escalate` with `reason_code: "conflict"` carrying both values; never pick the easier one.
3. Availability: call `cloud_composite_availability` with `target_pct` and the `tiers` of the request path from the blueprint (`id`, `availability_pct` of one replica as measured or given, `replicas`, `min_required`, `shared_failure_domain`). Read `composite_pct`, `nines`, `downtime_min_per_year` against `target_downtime_min_per_year` (the tool uses a 365-day year: 99.9 is 525.6 min, 99.95 is 262.8, 99.99 is 52.56), `meets_target`, `weakest_tier`. If false, the weakest tier is the first design change.
4. Latency: call `cloud_latency_budget` with `p99_target_ms` and the `hops` of the user path (`p50_ms`, `p99_ms`, `calls`, `group`). Read `critical_path_p99_ms`, `budget_remaining_ms`, `stages[]`, `tail_hit_pct`. Hop figures come from measurements or the task; mark unmeasured ones "assumed" and cap the status at `at-risk`.
5. Recovery: for each RTO/RPO pair call `cloud_dr_feasibility` with the candidate `strategy` and the timing fields; record `cheapest_sufficient_strategy`, `rto_margin_min`, `rpo_margin_min`. Leave the final strategy choice to `skill://a16-cloud-architect--dr-strategy-selection`.
6. Throughput: call `cloud_cell_capacity` with `load_rps`, `cell_max_rps`, `target_utilisation_pct`, `growth_pct`, `spare_cells`, `current_cells`. Read `cells_required`, `headroom_pct`, `current_cells_sufficient`.
7. Build the table with `write` at the task path, else `docs/cloud/nfr-translation.md`: NFR, source, design parameter (replicas, zones, cells, timeouts), tool, result, status (`met`, `at-risk` for assumed inputs, `unmet`). Every `unmet` row states the smallest change that would meet it, with its autonomy level (L2 within current providers and regions, L3 for a new region or provider, L4 for a commitment).
8. For `unmet` rows that need an A03 contract or blueprint change, call `shared_escalate` with `reason_code: "conflict"` and a change-request summary: A03 owns the contract, you only ask. Call `shared_artifact_digest`, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `cloud.architecture` NFR table with tool evidence and status per row.
- A list of change requests for A03 (not edits).

## Failure Handling

- `INVALID_INPUT` (a percentage above range, a `min_required` above `replicas`): fix the named field or BLOCKED `needs=input`.
- `PARTIAL_SUCCESS`: use `completed_fraction`; rows not evaluated are `unmet`-unknown, not `met`.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`.
- Availability from component SLAs is an upper bound: correlated failures and deploys are not modelled, so say `met` means "feasible by this model", not "guaranteed".
- Two tools giving incompatible assumptions (the same tier counted with different replicas) is E-CONTRACT.

## Handover

`to_agent` A03 (consumes `cloud.architecture`); a second write to A13 when targets changed. Fill `goal`, `completed`, `decisions`, `artifacts` (kind `cloud.architecture`, digest, producer A16), `open_questions` (assumed inputs), `blockers`, `verification`. End with `shared_handover_validate` then `shared_handover_write`.
