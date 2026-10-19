---
name: a03-architect--data-conflict-split
description: Use when the A07 data model conflicts with a published or draft contract and the contract must be split into versioned increments.
owner: a03-architect
tools: [shared_handover_read, arch_field_breaks, arch_semver_bump, arch_contract_ops, shared_artifact_digest, shared_escalate, shared_handover_validate, shared_handover_write]
---

## When to Use
- A07 reports that a schema or migration cannot satisfy a contract field, or the contract exposes a shape the model cannot hold.
- Rule (03-agents/A03 section 5, prompts/A03 ESCALATION): arbitration through A01 first; if unresolved in one cycle, split the contract into versioned increments.
- Driven by the data model, not by a consumer. Consumer-driven breaks are `skill://a03-architect--breaking-change`.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take the A07 model fields (name, type, nullability), the contract fields in conflict and any arbitration outcome A01 recorded in `decisions`.
2. Convert both sides into `{name, type, required}` lists (a nullable column is `required` false). Per side (`request`, `response`) call `arch_field_breaks` with `{side, base_fields: <current contract>, head_fields: <what the model can serve>}`; `breaking` is the conflict set.
3. No arbitration outcome yet: call `shared_escalate` with `{task_id, reason_code: conflict, summary, needs}`, `needs` naming the A07 question, and return its `result_patch` as the BLOCKED result (`needs` arbitration via A01). Stop; a task cannot wait inside a run. A01 re-assigns it after the session.
4. The outcome says unresolved (second cycle): use `write` to split. Increment 1 keeps the fields both sides support (additive only), increment 2 carries the remaining change.
5. For each increment use `write` to draft the revision and call `arch_contract_ops` with `{document}`; operations must not drop below the base count unless the increment says so.
6. Call `arch_semver_bump` per increment with `{base_version, head_version, breaking_change, additive_change}`: additive increment minor; removal or tightening is breaking, major, then continue in `skill://a03-architect--breaking-change`.
7. Call `shared_artifact_digest` for each increment file.
8. Call `shared_handover_validate` (`draft` true) and `shared_handover_write` once to A07 and once per contract consumer.

## Outputs
- Increment plan: order, fields per increment, version per increment, the A07 migration each depends on.

## Failure Handling
- A07 data missing: `shared_escalate` `reason_code` `ambiguous-input`; do not guess column types.
- A second conflict after the split: `shared_escalate` `reason_code` `conflict` to A01; never override A07's model.
- `INVALID_INPUT` names the field (duplicate field name); fix once.

## Handover
Fill `goal`, `completed`, `decisions` (what each increment holds, why), `artifacts` (increment contracts, digests, `producer` A03), `constraints` (increment order; migrations that must land first), `open_questions` for A07 and `verification` (`status`, `confidence`, `checks`). Write to A07 first, then consumers.
