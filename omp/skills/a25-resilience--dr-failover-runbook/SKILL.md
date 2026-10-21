---
name: "a25-resilience--dr-failover-runbook"
description: "Use when a resilience.dr task needs a disaster-recovery failover runbook with RTO and RPO budgets per step; production failover is an L4 human step."
disable-model-invocation: true
---

# DR failover runbook

## When to Use

- A `resilience.dr` task needs a runbook for a scenario (zone, region or cluster loss, data corruption) with explicit RTO and RPO.
- Not for backup coverage alone: `skill://a25-resilience--backup-restore-drill`. Not for reporting a finished drill: `skill://a25-resilience--post-drill-report`. Declaring the incident stays with A13.
- Production failover is L4 (`dr_failover_prod`): this agent writes the runbook, a human decides and executes. A staging rehearsal is L2.

## Procedure

1. Use `read` on `resilience.design` (A16) for scenarios, tiers and dependencies, and on `slo.manifest` for the service's targets. Each scenario needs `target_rto_minutes` and `target_rpo_minutes`; none given is BLOCKED needs=input, never a guess.
2. Call `resil_backup_coverage` for the scenario's data assets (`as_of`, `min_retention_days`, `restore_test_max_age_days`, `assets`, `jobs`). Region loss needs `offsite` true for every critical asset: `no-offsite-copy`, `backup-stale` or `backup-never-succeeded` makes the RPO unprovable, so mark the runbook `not-ready` and name the asset.
3. Call `resil_restore_rto` for each data tier (inputs as in its contract: `data_size_gib`, `restore_throughput_mib_s`, `parallel_streams`, `parallel_efficiency`, `replay_size_gib`, `fixed_overhead_minutes`, `safety_margin`, `target_rto_minutes`). Add `estimated_with_margin_minutes` along the dependency order (data before application before traffic) and take the maximum across independent branches. A total above the scenario RTO is the runbook's headline gap; report `max_data_gib_for_target` instead of promising the RTO.
4. Call `resil_drill_cadence` with `requirements` for this scenario (`max_interval_days`, `require_evidence`, targets) and the drill history. `never-drilled`, `overdue`, `rto-target-missed` or `rpo-target-missed` stamps the runbook `untested`; it must say so on its first line.
5. Draft the runbook with `write` at `dr-runbooks/<service>/<scenario>.md`: trigger and who may declare disaster; preconditions checklist; numbered steps each with command or console action, expected result, time box taken from step 3, and the check that proves it; fencing of the old primary before any promotion (no two writers); traffic shift last; failback; stop and roll back conditions; communications. Mark every production step `L4: human executes`. Name credentials by secret reference only; no key material, no credentialed endpoint.
6. Rehearse in staging: call `resil_chaos_spec_check` with `environment` `staging` for the failure injection (`required_autonomy` must be `L2`, `blockers` 0). A production rehearsal is L3 and needs `resil_chaos_budget_gate` evidence from `skill://a25-resilience--chaos-experiment-design`.
7. Cross-check `resil_restore_rto` against `resil_backup_coverage`: the sum of step time boxes must not exceed the scenario RTO, and the newest `last_success` used in step 2 must be older than the RPO window only by what step 5 documents as replayed data. Disagreement is E-CONTRACT.
8. Call `shared_artifact_digest` with `{path}` for the runbook, then `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "DR failover runbook needs human review; production failover is L4", "needs": ["human-approval"]}`.

## Outputs

- `dr.runbook`: the runbook file with status `ready`, `untested` or `not-ready`, the RTO budget table and the evidence digests.

## Failure Handling

- INVALID_INPUT: fix the named `field` or BLOCKED needs=input. PARTIAL_SUCCESS: use `completed_fraction`, name the tiers not estimated.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; the affected figure is `unknown`, not zero.
- Provider specifics (replica promotion commands, DNS or global load balancer steps) differ: take them from the task or provider docs, cite the source, and mark unverified steps `unverified`.
- Any L4 step: BLOCKED needs=human-approval; never execute failover and never retry it.

## Handover

`to_agent` A15 (consumes `dr.runbook`). `goal`: publish the runbook with its readiness status. `completed`, `decisions` (RTO budget per tier), `artifacts` (runbook with digest, producer A25), `open_questions` (unverified steps, undrilled scenarios), `constraints` (L4 human execution). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a25-resilience/dr-failover-runbook.md; edit the source and run scripts/build_agents.py -->
