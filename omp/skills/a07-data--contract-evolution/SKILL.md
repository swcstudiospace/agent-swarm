---
name: "a07-data--contract-evolution"
description: "Use when a new or changed data.contract (event or entity schema) is planned, to choose the compatibility mode and publish it with a pinned digest."
disable-model-invocation: true
---

## When to Use
- A planned change to an event or entity schema in the schema registry (03-agents/A07-data.md section 3 `data.contract`).
- Rules: BACKWARD compatibility is L2, any other mode is L3 (prompts/A07-data.md autonomy).
- This is a planned change; observed production drift is `a07-data--schema-drift-reconcile`.

## Procedure
1. Call `shared_handover_read` with `task_id` to get the current contract and the requested change.
2. List the `before` and `after` field sets (`name`, `type`, `required`; names unique) and call `data_contract_field_compat`. It judges BACKWARD only: removed fields, new required fields, type changes and optional-to-required are `breaking`; new optional fields and required-to-optional are `additive`.
3. If `compatible` from `data_contract_field_compat` is true, publish under BACKWARD (L2); `additive` entries go into the change note.
4. If `breaking` has entries, do not publish under BACKWARD: choose another mode (a decision you justify, not a tool result), set `approval_required` L3, and report BLOCKED `needs=human-approval` unless the approval is in the task.
5. For every field in the `after` set call `data_pii_tag_coverage` with `columns` (`name`, `pii`, `tag`) and `allowed_tags`; the `pii` flag comes from the classifier or the A10 policy in the handover. Any `untagged` entry blocks publication.
6. With `write`, create the contract file with `contract_id`, `version` (bump major on breaking changes), `format`, `compatibility`, `fields_classified`.
7. Call `shared_artifact_digest` for the contract file, then `shared_artifact_check` with `task_id`, `outputs` (`kind`, `uri` the local file path, `digest`, `producer` A07) and `agent` A07; `ok` must be true (a `registry://` uri is reported `unchecked`, never `ok`).
8. Call `shared_dod_check` with `task_id` and `result` whose `completed` lines and `checks` (`{name, status}`) cite the evidence per acceptance criterion.
9. Publish only through the registry command or endpoint the task inputs name (with `bash`). If none is named, the file and its digest are the deliverable and `remaining` says "publish to registry".

## Outputs
- data.contract JSON: id, version, compatibility mode, `fields_classified`.
- Breaking and additive lists, digest, and the approval level used.

## Failure Handling
- Registry unreachable during publish: stop and use `a07-data--registry-outage-defer`.
- Duplicate field names (INVALID_INPUT naming `before` or `after`): correct the field set once, else BLOCKED `needs=input`.
- Classifier unavailable: block (see `a07-data--pii-classification`), never assume fields are non-PII.

## Handover
`to_agent` is the agent id in the task routing (default A01). Fill `goal`, `completed`, `decisions` (mode chosen and why), `artifacts` (contract file, digest, producer A07), `blockers` (approval, untagged fields), `constraints` (consumers pin the digest). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/contract-evolution.md; edit the source and run scripts/build_agents.py -->
