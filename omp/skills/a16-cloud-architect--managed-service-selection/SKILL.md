---
name: "a16-cloud-architect--managed-service-selection"
description: "Use when a task must choose a provider-managed service (database, queue, cache) over self-hosting or the reverse, with weighted criteria and knockouts."
disable-model-invocation: true
---

# Managed service selection

## When to Use

- A `cloud.architecture` task needs a build-versus-buy answer for one stateful or infrastructure component (relational database, message broker, cache, search, Kubernetes control plane).
- Not for pricing the options (A20 owns `cost.report`; you consume it), nor for the technology stack of the application (A03 `tech-stack`), nor for security acceptance (A10).
- Autonomy: the recommendation is L2. Selecting a new provider or a service in a new region is L3. A contract, reserved-capacity or exit-plan decision is L4: you recommend, a human decides; nothing is purchased or provisioned.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the blueprint, the `slo.manifest`, the `cost.report` and the `carbon.report` if present. The cost and sustainability criteria must be scored from those artifacts; if absent, ask A20 through `shared_escalate` `reason_code: "blocked"`, `needs: ["cost.report from A20"]` rather than estimating.
2. Define 6 to 10 criteria, each `{id, weight 1-10, managed_score 1-5, self_hosted_score 1-5, critical}`: for example `availability-sla`, `ops-burden`, `cost`, `lock-in-exit`, `compliance-residency`, `feature-fit`, `performance`, `team-skill`. Mark `critical: true` for a hard requirement (residency, a required extension or version, a required recovery objective); a 1 on a critical criterion disqualifies that option. Every score cites a source (provider SLA page named in the task, the cost report line, the SLO); an unsourced score is recorded as "assumed".
3. Call `cloud_managed_vs_self_hosted` with `{criteria, tie_margin_pct}` (default 5 from the tool). Read `managed_score_pct`, `self_hosted_score_pct`, `margin_pct`, `recommendation` (`managed`, `self_hosted`, `tie`, `none`), `disqualified`, `decisive_criteria`. `no-viable-option` (blocker) means the requirements cannot be met by either option: escalate, do not relax a critical flag.
4. Sensitivity: change each `decisive_criteria` weight by 2 in both directions (kept inside 1-10) and call `cloud_managed_vs_self_hosted` again. If `recommendation` flips, the answer is fragile: report `tie` semantics and name the criterion rather than the winner.
5. Check availability: call `cloud_composite_availability` once with the managed option (tier `availability_pct` = the SLA figure stated in the task; `replicas` and `min_required` from the provider's HA mode) and once with the self-hosted one (one tier per replica group, `shared_failure_domain` as designed). The managed SLA is a credit contract, not a guarantee: compare `composite_pct` with the target and record the unmanaged remainder.
6. For self-hosted, call `cloud_replica_placement` with the planned `placements`, `quorum_mode` and `tolerate_az_failures` to prove the spread; `az-loss-breaks-quorum` disqualifies that design. For managed, `cloud_data_residency` with the service's regions and the data classes it will hold: `violation_count` above 0 disqualifies it, `unruled_count` above 0 fails closed.
7. Cross-check: a recommendation of `managed` while step 6 shows a residency violation (or `self_hosted` with a failing quorum) contradicts step 3. Rerun `cloud_managed_vs_self_hosted` with the failing criterion scored 1 and `critical: true`; never ship the pre-check result.
8. Write the decision with `write` at the task path, else `docs/cloud/service-selection.md`: criteria table, scores with sources, sensitivity, checks, exit-cost note for the L4 owner. Call `shared_artifact_digest`, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `cloud.architecture` decision record: recommendation, criteria, disqualifications, sensitivity, availability and residency evidence.

## Failure Handling

- `INVALID_INPUT`: fix the named score (outside 1-5) or BLOCKED `needs=input`. `PARTIAL_SUCCESS`: use `completed_fraction`.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`; a skipped check keeps the decision `proposed`.
- SLA percentages, feature availability and region lists change: take them from the task text; if not given, leave the criterion unscored and say so.
- `tie`: do not break it by preference; hand the open criterion to a human (L3).
- Two tools disagreeing is E-CONTRACT.

## Handover

`to_agent` A03 (records the decision as an ADR; see `skill://a16-cloud-architect--cloud-adr-handoff`); a second write to A20 for cost validation. Fill `goal`, `completed`, `decisions`, `artifacts` (kind `cloud.architecture`, digest, producer A16), `open_questions` (assumed scores), `verification`. End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a16-cloud-architect/managed-service-selection.md; edit the source and run scripts/build_agents.py -->
