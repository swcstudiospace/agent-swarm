---
name: a16-cloud-architect--dr-strategy-selection
description: Use when a cloud.resilience task must pick backup-restore, pilot light, warm standby or active-active so declared RTO and RPO are provably met.
owner: a16-cloud-architect
tools: [cloud_dr_feasibility, cloud_composite_availability, cloud_replica_placement, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# DR strategy selection

## When to Use

- A `cloud.resilience` task has RTO and RPO targets (from `requirements.spec` or `slo.manifest`) and needs one DR strategy with evidence.
- Not for splitting a service into cells (`skill://a16-cloud-architect--cell-architecture-split`) or for a new region rollout order (`skill://a16-cloud-architect--multi-region-rollout-design`).
- Autonomy: the recommendation is L2. Adding a recovery region or changing provider is L3. Any spend commitment or exit plan is L4: recommend, never buy or apply.

## Procedure

1. Call `shared_handover_read` with `{task_id}`; take `rto_target_min`, `rpo_target_min` and the availability target. A target not stated by A02 or A13 is BLOCKED `needs=input` via `shared_escalate` (`reason_code: "ambiguous-input"`); never assume one.
2. Use `read` on the task, runbooks and monitoring exports to collect the measured figures: `detect_decide_min`, `traffic_cutover_min` (at least the DNS TTL), `validation_min`, `backup_interval_min`, `infra_provision_min`, `data_restore_min`, `compute_start_min`, `compute_scale_min`, `replication_lag_s` and `synchronous_replication`. A figure that is only a guess is marked as such in the design; a missing one makes the tool reject that strategy.
3. Call `cloud_dr_feasibility` once per candidate `strategy` (`backup_restore`, `pilot_light`, `warm_standby`, `active_active`) with every figure from step 2, so `evaluated_strategies` lists all four. Read `achievable_rto_min`, `achievable_rpo_min`, `rto_margin_min`, `rpo_margin_min`, `meets_rto`, `meets_rpo`, `cheapest_sufficient_strategy`. The tool is inclusive (`<=`) and orders strategies from least to most costly; it gives no money figure.
4. Decide from the `cloud_dr_feasibility` output: take `cheapest_sufficient_strategy`, but this skill requires at least 20% of each target as margin (`rto_margin_min` >= 0.2 x `rto_target_min`, same for RPO); otherwise take the next stronger strategy and say so. `rpo-missed` or `rto-missed` (blocker) on the chosen strategy means the design fails. An RPO of 0 needs `synchronous_replication: true`.
5. Call `cloud_replica_placement` for the datastore across the recovery pair: `placements` with the region as the `az` field and its replica count, `quorum_mode: "majority"`, `tolerate_az_failures: 1`. `survives_az_failures` must be true; `az-loss-breaks-quorum` (blocker) means a two-site majority cannot fail over automatically: add a third witness site or choose another strategy.
6. Call `cloud_composite_availability` with the tiers of the primary path, once with the standby as a second replica (`shared_failure_domain: false`, `min_required: 1`) and once without. Compare `composite_pct` with the target: the DR design must not be credited for routine availability unless `meets_target` flips. Quote `downtime_min_per_year`.
7. Write the design with `write` at the task path, else `docs/cloud/dr-strategy.md`: chosen strategy, the step-3 table, margins, the failover runbook trigger, and a drill requirement for A25 (a DR claim without a drill is unverified).
8. If a new region or provider is needed, call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]` (L3). Call `shared_artifact_digest` for the doc, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `resilience.design`: strategy, RTO/RPO table with margins, quorum result, availability comparison.
- `cloud.architecture` addendum when the recovery site changes the topology.

## Failure Handling

- `INVALID_INPUT` naming a field (for example `compute_start_min` for pilot light): supply the measurement or BLOCKED `needs=input`.
- `PARTIAL_SUCCESS`: use `completed_fraction`; name every strategy not evaluated.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`.
- Two tools disagreeing (for example quorum survives but composite availability fails) is E-CONTRACT: report both.
- The 20% margin is this skill's rule, not an industry standard; the tool verdict alone decides pass or fail. The tool's RTO model (detect + cutover + validation + the strategy's own step; active_active has no step and no validation) is a house model: only a drill proves the figures. `weaker-strategy-sufficient` (a lower strategy meets both targets while the declared one misses, for example replication lag above the backup interval) is reported, never picked silently.
- L3 and L4 actions are BLOCKED `needs=human-approval`; never retried.

## Handover

`to_agent` A25 (resilience verification and drills); a second write to A13 (alert and SLO inputs). Fill `goal`, `completed`, `decisions` (strategy, margins), `artifacts` (kind `resilience.design`, digest, producer A16), `open_questions` (guessed figures), `verification`. End with `shared_handover_validate` then `shared_handover_write`.
