---
name: "a16-cloud-architect--residency-compliance-mapping"
description: "Use when data classes with residency or sovereignty rules must be mapped to regions, replicas, backups, logs and DR targets, with every violation listed."
disable-model-invocation: true
---

# Residency compliance mapping

## When to Use

- A `cloud.architecture` or `cloud.landing_zone` task has data classes (personal data, payment data, health data, audit logs) with jurisdiction rules, and the design must show where every copy lives.
- Not for deciding what the law requires: the rules come from the compliance owner in the task. Not for control testing (A10) and not for regional rollout order (`skill://a16-cloud-architect--multi-region-rollout-design`).
- Autonomy: the mapping is L2. Moving data to another region or provider is L3 (BLOCKED `needs=human-approval`). Any exit plan, data-transfer mechanism or contractual clause is L4 for a human; never state legal conclusions.

## Procedure

1. Call `shared_handover_read` with `{task_id}`; collect `requirements.spec` rules: the data class names, `allowed_jurisdictions` per class (use `*` only where the task says "no restriction") and who approved them. Rules without a named owner are BLOCKED via `shared_escalate` `reason_code: "ambiguous-input"`.
2. Use `read` on the task rules to build `regions` as `{region, jurisdiction}` for every region in any placement, using the jurisdiction labels of the rules (for example `EU`, `US`). A region whose jurisdiction is unclear is `needs=input`; never infer it from its name.
3. Build `placements` as `{resource, data_class, region, kind}` for every copy. Include the quiet copies: `replica`, `backup`, `analytics` (warehouse, exports), `cache` and `log` (application and audit logs often carry personal data). Take them from the IaC with `grep`/`read` and from the blueprint; a data store you cannot find is listed as an open question, not omitted.
4. Call `cloud_data_residency` with `{regions, rules, placements}`. Read `violation_count`, `unruled_count`, `by_data_class[]`, `findings`, `verdict`. A violation of `primary`, `replica`, `backup` or `analytics` is a blocker (persistent copy), of `cache` or `log` a major; `no-rule:*` (major) means the class has no rule and fails closed. Zero violations and zero unruled is required for `pass`.
5. Fix by moving the placement to an allowed jurisdiction, not by editing the rule, then call `cloud_data_residency` again until `violation_count` is 0. Every move is an L3 change of region; list it in the report with the affected resource.
6. Cross-check the guardrail: call `cloud_landing_zone_lint` on the accounts and check that `restrict-regions` is in `guardrails` for each non-management account in scope and that the allowed-region set it will enforce equals the regions of step 2. `guardrail-missing:*` means a human or pipeline can still create a copy in a forbidden region: record it as a finding against `landing.zone.spec`.
7. Cross-check recovery: when backups or the DR site must stay inside the jurisdiction, call `cloud_dr_feasibility` for the chosen `strategy` with the in-jurisdiction recovery figures. `meets_rto` or `meets_rpo` false means residency and recovery targets conflict: record E-CONTRACT with both outputs and escalate (`shared_escalate` `reason_code: "conflict"`); do not trade one away.
8. Write the matrix with `write` at the task path, else `docs/cloud/residency-matrix.md`: class x region x kind, the tool output verbatim, moves proposed and the approvals required. Call `shared_artifact_digest`, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `cloud.architecture` residency matrix with violations and fixes.
- `landing.zone.spec` change request for the region guardrail when step 6 finds a gap.

## Failure Handling

- `INVALID_INPUT` (a region absent from `regions`): add it or BLOCKED `needs=input`. `PARTIAL_SUCCESS`: use `completed_fraction`.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`; never report a matrix as compliant with a skipped check.
- The tool decides jurisdiction match by label equality only; sovereignty questions beyond that (operator access, provider legal entity, transfer mechanisms) are not modelled: list them as open questions for the compliance owner.
- L3 moves and L4 clauses are BLOCKED `needs=human-approval`; never retried.

## Handover

`to_agent` A03 (blueprint and ADR owner consumes `cloud.architecture`); a second write to A11 when the guardrail change affects `landing.zone.spec`. Fill `goal`, `completed`, `decisions`, `artifacts` (digest, producer A16), `blockers`, `open_questions` (unmodelled sovereignty topics), `verification`. End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a16-cloud-architect/residency-compliance-mapping.md; edit the source and run scripts/build_agents.py -->
