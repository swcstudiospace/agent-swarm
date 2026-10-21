---
name: "a07-data--pii-classification"
description: "Use when a new or changed column or contract field may hold personal data and needs a tag, masking rule and retention entry before the migration merges."
disable-model-invocation: true
---

## When to Use
- A migration or contract adds or retypes a field that could store personal data (email, name, address, identifiers).
- Rules: a PII field needs classification, masking rule and retention entry before merge; with the classifier down, migrations touching unknown columns are blocked fail-closed with E-POLICY (prompts/A07-data.md decision_logic 4, error_handling; 03-agents section 7).
- This tags schema fields; data-subject requests are `a07-data--privacy-request-plan`.

## Procedure
1. Call `shared_handover_read` with `task_id` to get the new columns and the classifier output or A10 `security.policy` that decides `pii`.
2. If neither a classifier result nor an A10 policy covers a column, call `shared_escalate` with `task_id`, `reason_code` policy, `summary` "classification unavailable for <columns>" and `needs` naming the classifier or A10; do not guess the flag.
3. With `write`, for each PII column assign a tag from the allowed set (named by the A10 policy or the task inputs; `PII-direct` is only the example in 03-agents/A07-data.md), a masking rule for every non-prod environment, and a retention period.
4. Call `data_pii_tag_coverage` with `columns` (`name` as unique `table.column`, `pii`, `tag`) for every column of the touched tables and `allowed_tags`; the tool checks tags only on columns whose `pii` is true, so a wrong `pii: false` is invisible to it. `coverage` must be 1 and `untagged` empty.
5. Write the classification catalog entry per field: tag, masking rule, retention, access note (grants are generated, never hand-written).
6. With `edit` on the draft JSON, fill `pii_touched` in the schema.migration (`table.column` form) and `fields_classified` in any data.contract.
7. Call `shared_artifact_digest` for the catalog file, then `shared_dod_check` with `criteria` ["every PII field has tag, masking rule, retention"] and `result` whose `completed` and `checks` (`{name, status}`) lines state, per field, tag, masking rule and retention.

## Outputs
- privacy.classification catalog entries: field, tag, masking rule, retention.
- `pii_touched` and `fields_classified` values, plus `coverage`.

## Failure Handling
- `untagged` not empty: block publication of those fields until tagged; the migration must not merge.
- Duplicate column names (INVALID_INPUT, field `columns`): fix the list once, else BLOCKED `needs=input`.
- `shared_escalate` itself failing: restate the E-POLICY block in the final message.

## Handover
`to_agent` is the agent id in the task routing (default A01; A10 when its policy was missing). Fill `goal`, `completed`, `decisions` (tag and masking choices with rationale), `blockers` (untagged fields, classifier outage), `artifacts` (catalog file with digest), `constraints` (masking in non-prod). Run `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a07-data/pii-classification.md; edit the source and run scripts/build_agents.py -->
