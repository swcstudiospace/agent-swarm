---
name: a20-finops--idle-cleanup-proposal
description: Use when a finops.rightsize task asks which VMs, databases, volumes, load balancers or IPs sit idle and their monthly cost; propose owner review, never delete.
owner: a20-finops
tools: [finops_idle_resources, finops_tag_coverage, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Idle resource cleanup proposal

## When to Use

- A cost review or a `finops.rightsize` task needs the list of resources whose utilization stayed under a threshold for a trailing window, with the monthly cost at stake.
- Not for shrinking a workload that is in use (`skill://a20-finops--rightsizing-sweep`) or for unattached IaC lifecycle gaps (`skill://a20-finops--storage-lifecycle`).
- Autonomy: finding and pricing is L2. Stopping, snapshotting or resizing is a change by the owner or A11 (L3 in production). Deleting is never done by A20; a human decides it at L4 after the owner confirms.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A11 `environment.record` (which environments exist, which are production) and take the inventory the task supplies (1 to 300 resources, unique `id`): per resource `kind` (`vm`, `database`, `volume`, `load_balancer`, `ip_address`), `monthly_cost`, its tags and one utilization sample (0 to 100) per day, oldest first, at most 90 samples (send the latest 90). Utilization must be the one metric per kind named in the task (CPU percent for a VM, attached-and-in-use for a volume as 0 or 100, request count turned into percent of peak for a load balancer); no definition is BLOCKED `needs=input`.
2. Fix the rule, both numbers conventions of this skill: `threshold_pct` (5 for VMs and databases, 1 for volumes, load balancers and IPs unless the task sets one; one call has one threshold, so run one call per group) and `min_idle_days` 14 (1 to 90 in the tool), which covers a weekly cycle but not month-end jobs: use 30 or more for resources with monthly jobs. State both in the report.
3. Call `finops_idle_resources` with `resources` (`id`, `kind`, `daily_utilization_pct`, `monthly_cost`), `threshold_pct`, `min_idle_days`. A resource is idle only when its trailing consecutive run strictly under `threshold_pct` is at least `min_idle_days` long, ending at the latest sample.
4. Read `idle` (each with `idle_days`, `monthly_cost`, `recommendation`: `schedule_or_stop` for VM, `snapshot_and_stop` for database, `snapshot_and_review` for volume, `review_targets` for load balancer, `review_attachment` for IP; these are owner-review labels, nothing is executed), `idle_count`, `idle_monthly_cost` and `insufficient_data_count`. A finding is major when every sample is idle, minor when only the trailing run is. Resources with fewer than `min_idle_days` samples are `insufficient_data_count`, never idle: list them as "cannot judge" and ask for a longer export.
5. Cross-check the cost: `idle_monthly_cost` equals the sum of `idle[].monthly_cost` within rounding (0.01 per resource). If `idle_count` is 0 skip the tag check. Else call `finops_tag_coverage` with the idle resources as `resources` (`id`, `monthly_cost`, `tags` as `{key, value}` pairs) and `required_keys` `["owner", "environment"]`: a resource with `missing` `owner` (named in its finding) has no one to ask and goes to the "owner unknown" list, not to the cleanup list; a `case_mismatch` owner key is a spelling fix, the owner is known.
6. Exclude and mark, using `read` on the environment record and the owner answers: disaster-recovery standbys, break-glass or compliance-retained resources, and anything marked as scheduled batch cannot be recommended for stop without the owner; keep them in a separate "kept idle on purpose" list only when the owner confirmed it.
7. Write the proposal with `write` at the path the task names, else `docs/finops/idle-<scope>.md`: per resource `id`, `kind`, `idle_days`, `monthly_cost`, the tool's `recommendation`, the owner, the evidence window and a proposed grace period (14 days of notice, a convention of this skill) before any change. State: nothing was stopped or deleted.
8. Production resources and any `database` or `volume` need owner approval: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "idle production resources need owner decision", "needs": ["human-approval"]}`. Deletion is a separate L4 request and is not made here.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report` (idle section): idle list with cost at stake, owners, grace period, kept-idle list; evidence is the `finops_idle_resources` and `finops_tag_coverage` outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (duplicate `id`, unknown `kind`, `min_idle_days` outside 1 to 90) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name resources without samples.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never judge idleness by eye.
- A threshold that flags most of the inventory is probably a metric error: stop and ask which metric the series holds.
- L3 and L4 actions end BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

- `to_agent`: `A17` (consumes `cost.report`, notifies owners). `goal`: collect owner decisions before the grace period ends.
- `decisions`: thresholds; `artifacts`: proposal digest, producer `A20`; `open_questions`: owners unknown and resources with insufficient data; `blockers`: `human-approval`; `verification`: `partial`.
- End with `shared_handover_validate` then `shared_handover_write`.
