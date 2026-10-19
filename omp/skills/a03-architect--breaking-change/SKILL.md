---
name: "a03-architect--breaking-change"
description: "Use when a contract change is breaking or external-facing and needs a major bump, migration plan, consumer acks and a human-approval BLOCKED packet."
disable-model-invocation: true
---

## When to Use
- `arch_field_breaks` or `scripts/arch_contract_check.py` reports a breaking change, or the contract is external-facing.
- Autonomy rule (agents.json ceiling `breaking_contract` L3; prompts/A03 GUARDRAILS): major bump, migration plan and A05/A06 acknowledgement are mandatory and the task ends BLOCKED `needs` `human-approval: breaking_contract`; nothing is published.
- Non-breaking internal revisions use `skill://a03-architect--contract-publish`.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take both contract versions and `consumers` (agent ids such as `A05`, `A06`).
2. With `bash` run `python3 scripts/arch_contract_check.py --file <new> --base <old> --task-id <task_id> --correlation-id <correlation_id> --json`. Keep `breaking` (strings) and `contracts[].info_version` (head version).
3. Call `arch_field_breaks` once per side (`request`, `response`) with `{side, base_fields, head_fields}`, fields as `{name, type, required}`; collect `breaking`.
4. Call `arch_semver_bump` with `{base_version, head_version, breaking_change: true, additive_change}` (plain MAJOR.MINOR.PATCH). Need `required_bump` `major` and `compliant` true; otherwise raise `info.version` and rerun step 2.
5. Use `write` to author the migration plan: affected operations, consumer steps, N-1 compatibility for 30 days (prompts/A03 constraints), rollback.
6. Call `shared_artifact_digest` for the new contract and the migration plan.
7. Call `shared_handover_validate` (`draft` true), then `shared_handover_write` once per consumer (`to_agent`); put "ack <contract> <head_version>" in `open_questions`.
8. Acks arrive only when A01 re-assigns this task. First call `shared_handover_read` with `{task_id}` and no `seq`: it returns the newest handover addressed to you. When `status` is `missing` or `context_only` is true, nothing is addressed to you yet: no reply, go to step 9. Otherwise `handover.handover_id` ends in `.<N>`; that is the last `seq` that can hold a reply. Read each reply with `shared_handover_read` (`{task_id, seq}`) for `seq` = 1, 2, 3, ... up to N. A `missing` status below N is a gap in the history (a writer that died), not its end: skip it and go on. An entry with `found` false and another `status` (`malformed`, `stale`) is no reply either: go on with the next `seq`. If the first read returned `malformed` (no `handover_id`), stop only after three consecutive `missing` reads. The end of the history is a SUCCESS response, never an error. Convention: a reply counts when `from_agent` is the consumer and a `decisions` entry reads `ack <contract> <head_version>`. Build `acks` as `{consumer, version}`.
9. Call `arch_breaking_readiness` with `{breaking_change, external_facing, consumers, acks, head_version, migration_plan_present}` (unique, non-blank consumers). Read `autonomy_level`, `missing_acks`, `missing`, `needs`; an ack for another version does not count.
10. Call `shared_escalate` with `{task_id, reason_code: policy, summary, needs}` where `needs` is step 9's `needs` (it always ends with `human-approval: breaking_contract`). Return its `result_patch` as the BLOCKED result. Do not publish.

## Outputs
- Migration plan, `breaking` list, `autonomy_level` L3, `missing_acks`, BLOCKED packet.

## Failure Handling
- No consumer reply: keep `missing_acks` and escalate; never assume an ack.
- `INVALID_INPUT` (`head_version` not MAJOR.MINOR.PATCH, duplicate consumer): fix the field, retry once.
- Tool outage: retry twice, then report the field as `skipped:tool-missing`; the contract check itself is required (`FAILED` `E-DEP` if it cannot run).

## Handover
Fill `goal`, `completed`, `decisions` (bump and migration rationale), `artifacts` (contract and migration plan, digests), `blockers` (missing acks, the approval), `open_questions`, `constraints` (N-1 for 30 days) and `verification` (`status` partial, `confidence`, `checks`). The write goes to each consumer; the BLOCKED packet returns through `shared_escalate`.

<!-- generated from prompts/skills/a03-architect/breaking-change.md; edit the source and run scripts/build_agents.py -->
