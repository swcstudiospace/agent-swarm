---
name: "a17-platform-engineer--tenant-quota-allocation"
description: "Use when team quotas must be sized or changed against cluster or account capacity and the answer is a simulated before and after fit sent for L3 approval."
disable-model-invocation: true
---

# Tenant quota allocation

## When to Use

- A team asks for more quota, a new tenant appears, or the periodic review finds teams near their limit; capacity comes from the `landing.zone.spec` (A16) and the `cloud.architecture`, spend from the `cost.report` (A20).
- A single self-service request is `skill://a17-platform-engineer--environment-request-intake`. Applying a ResourceQuota or cloud quota is A21 or A11.
- Any quota change is L3 (`tenant_quota_change`). Analysis and a proposal are yours; the change is BLOCKED `needs=human-approval`.

## Procedure

1. `read` the `landing.zone.spec`, the `cloud.architecture` and the current quota table. Build `capacity` as `[{resource, total}]` (for example `cpu` in cores, `memory` in GiB, `storage` in GiB; one entry per resource, all teams in the same unit) and `allocations` as `[{team, resource, quota, used}]`. Every allocation resource needs a capacity entry.
2. Fix the policy inputs: `reserve_pct` (share held back for the platform, 0 when none is stated) and `overcommit_ratio` (1 when none is stated; allocatable is `total x (1 - reserve_pct/100) x overcommit_ratio`). Ask for them when the task is silent; do not choose them.
3. Call `plat_quota_fit` on the current state first. Read `resources[]` (`allocation_ratio`, `usage_ratio`, `fits`, `shortfall`), `oversubscribed_count`, `over_quota_count`. This is the baseline; a state that is already `oversubscribed` is reported before any new request is considered.
4. Classify per team from the `plat_quota_fit` findings: `over-quota` (major: used above quota), `quota-hot` (minor: used/quota at or above 0.9), `quota-idle` (info: below 0.2, the unused part is a reclaim candidate). Reclaim proposals are listed, never taken.
5. Build the proposal: raise hot teams, shrink idle teams only with the team owner named in the catalog; then call `plat_quota_fit` again on the proposed `allocations`. The proposal is acceptable only when every resource has `fits` true and `oversubscribed_count` is 0; otherwise the `shortfall` is the answer and the request is partly refused.
6. Cross-check `plat_quota_fit`: the sum of the proposal's `quota` per resource must equal `allocated` in the result, and `shortfall` must equal `max(0, allocated - allocatable)`; a mismatch is E-INTERNAL.
7. For preview environments eating quota, call `plat_ephemeral_env_policy` with `checked_at`, `max_ttl_hours`, `idle_after_hours`, `max_cost_usd`, `environments`: its `reclaim` list names environments that are expired or idle, a cheaper first fix than raising quota. The tool names them; A11 reclaims.
8. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "tenant quota change awaiting L3 approval", needs: ["human-approval"]}` and return its `result_patch`. `write` the before and after tables and call `shared_artifact_digest` with `{path}`.

## Outputs

- `platform.claim` (the proposed quota claim per team and resource) with the baseline and simulated fit.

## Failure Handling

- INVALID_INPUT (duplicate `team`/`resource` pair, resource without capacity, negative number): fix the named list once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never present an unsimulated proposal as fitting.
- PARTIAL_SUCCESS: use `completed_fraction`, name resources not checked.
- The 0.9 and 0.2 thresholds are house policy in the tool, not a standard; defer to the reported findings.

## Handover

`to_agent`: `A21` (cluster ResourceQuota) or `A11` for cloud-account quotas. Fill `goal`, `decisions` (`reserve_pct`, `overcommit_ratio`, which teams change), `blockers` (`human-approval`), `artifacts` (claims and tables with digests, producer `A17`), `verification` (`partial`). End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a17-platform-engineer/tenant-quota-allocation.md; edit the source and run scripts/build_agents.py -->
