---
name: a16-cloud-architect--multi-region-rollout-design
description: Use when a cloud.architecture task adds a second or further region and needs wave order, traffic split, latency and residency checks before A12 promotes.
owner: a16-cloud-architect
tools: [cloud_blast_radius, cloud_latency_budget, cloud_composite_availability, cloud_data_residency, shared_handover_read, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Multi-region rollout design

## When to Use

- A service moves from one region to two or more, or an active-active pair gains a third region, and the task needs the design plus the order in which regions come up.
- Not for the DR strategy choice alone (`skill://a16-cloud-architect--dr-strategy-selection`) and not for executing or promoting the rollout: A12 owns promotion, A11 the IaC.
- Autonomy: the design is L2. Every new region or provider is L3 (BLOCKED `needs=human-approval`). Commitments or exit plans are L4.

## Procedure

1. Call `shared_handover_read` with `{task_id}`; take the blueprint (service list, dependencies), the `slo.manifest` objective and the candidate regions. Missing dependency lists are BLOCKED via `shared_escalate` `reason_code: "ambiguous-input"`.
2. Run `python3 scripts/cloud_readiness_scan.py --task-id $TASK --correlation-id $CORR --root . --json` on the IaC path. Findings (ids start with `cloud.`) `multi-az-missing`, `multi-az-disabled` or `k8s-no-zone-spread` findings must be fixed in the design before regions are added: a region of single-AZ services only moves the weak point.
3. Call `cloud_blast_radius` with `services` (`id`, `criticality` 1-10, `instances` as `{region, az, cell}`, `depends_on` hard dependencies) for the target state and `max_az_blast_pct` from the task. Read `worst_domain`, `worst_blast_pct`, `ranking[]` (`scope` is `cell`, `az` or `region`), `spof_services`. The tool applies the limit to `az` and `cell` scopes only; for `region` scope this skill requires `blast_pct` below the share one region serves in the planned split (for example under 50 for two active regions). A service with criticality 8 or more in `spof_services` is a major finding.
4. Call `cloud_latency_budget` with `p99_target_ms` and `hops` for the critical path; each synchronous cross-region call is its own hop with measured `p50_ms`, `p99_ms` and `calls`, parallel hops share a `group`. `meets_target` false, `dominant-stage:*` or `tail-amplification` (tail_hit_pct above 10) means the call must become asynchronous or region-local. Never use a guessed inter-region latency: unmeasured is `needs=input`.
5. Call `cloud_composite_availability` with the tiers. Model a global dependency (DNS, identity, the control plane) as its own tier with `replicas: 1` or `shared_failure_domain: true`: a second region earns redundancy credit only where `shared_failure_domain` is false. Compare `composite_pct` and `weakest_tier` with the objective; if the weakest tier is a shared global, adding regions will not help.
6. Call `cloud_data_residency` with every candidate region mapped to its jurisdiction, the per-class rules and the placements of each datastore, replica, backup, cache and log in the new region. `violation_count` above 0 removes that region for that class (replicate nothing, or tokenize); `unruled_count` above 0 means a class lacks a rule: fail closed.
7. Order the waves with `write` into the design: (1) region with least traffic or internal users, (2) one region at a time, traffic shifted in steps from the task's allowed percentages, (3) rollback = restore the previous split. Each wave names its gate evidence (`worst_blast_pct`, `budget_remaining_ms`, `composite_pct`). Do not state bake times the task did not give.
8. Call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]` for the new region (L3). Call `shared_artifact_digest` on the design, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `cloud.architecture`: target topology, wave plan, per-wave gate evidence, residency matrix.
- `resilience.design` section with the blast-radius ranking.

## Failure Handling

- `INVALID_INPUT`: fix the named field or BLOCKED `needs=input`. `PARTIAL_SUCCESS`: use `completed_fraction`, name the unchecked services.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`; a wave with a skipped gate cannot be proposed.
- Region and AZ naming differs by provider: take names from the task, never from memory.
- Blast radius and composite availability disagreeing on the weakest dependency is E-CONTRACT.

## Handover

`to_agent` A17 (platform onboarding of the new region); also A20 (cost model) and A03 (blueprint impact). Fill `goal`, `completed`, `decisions`, `artifacts` (kind `cloud.architecture`, digest, producer A16), `blockers` (the L3 approval), `open_questions`, `verification`. End with `shared_handover_validate` then `shared_handover_write`.
