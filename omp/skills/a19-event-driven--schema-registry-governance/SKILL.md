---
name: "a19-event-driven--schema-registry-governance"
description: "Use when a schema registry or schema folder needs a compatibility policy per subject and an audit that every version satisfies it, including transitive checks."
disable-model-invocation: true
---

# Schema registry governance

## When to Use

- A repo or registry holds many versions of event schemas and nobody can say which compatibility level protects which consumers; or a registry setting is to be relaxed. Policy and audit are L2 (`event_design`).
- Relaxing a subject to no compatibility checking, or approving a version that fails the policy, is `breaking_event_schema`: L3. Deleting a subject or version is L4: never.
- Not for one schema change (`skill://a19-event-driven--schema-evolution`).

## Procedure

1. Call `shared_git_head` with `{"scan": true}` and record `sha` and `branch`: the audit is valid for that revision only. List the schema files with `glob` (for example `**/*.avsc`, `**/schemas/**/*.json`) and group them into subjects by the naming strategy in use: topic name (the common registry default, one record type per topic), record name (several types per topic) or topic plus record name. Order each subject's versions with `bash` `git log --follow --format=%H -- <file>` or by the version in the path; an order you cannot establish is BLOCKED `needs=input`.
2. Set the policy per subject and record it with `write`, from who reads it. Registry levels are `BACKWARD` (default: the new schema reads data of the previous version), `FORWARD`, `FULL`, and their `_TRANSITIVE` variants (against all previous versions), plus `NONE`. Rule of thumb: topics consumers may replay from the start need `BACKWARD_TRANSITIVE`; independently deployed producers and consumers need `FULL` or `FULL_TRANSITIVE`; `NONE` only for a topic with a dead consumer set, with a written reason.
3. Audit adjacent versions. For each subject and each pair `v(n-1)` to `v(n)` call `event_schema_compat` with `format` (`avro` or `json-schema`), `mode` (`backward`, `forward` or `full` as the policy says), `base_schema`, `head_schema` (JSON text). A `breaking_changes` above 0 in the policy direction is a violation; record the pair, mode and finding ids.
4. For a `_TRANSITIVE` policy repeat the `event_schema_compat` call of step 3 for the newest version against every earlier one, not only the previous one (a field removed in v3 and re-added with another type in v4 passes pairwise and fails against v2). More than 25 versions per subject: audit the oldest version still retained in any topic, the latest, and the versions where findings occurred, and say so.
5. Cross-check the tool with the script: run `python3 scripts/event_schema_check.py --task-id $TASK --correlation-id $CORR --root <repo> --json --base <old> --head <new> --mode <mode> --path <schema-dir>` on at least the pairs that violated and two that passed; its `comparison.compatible` must equal the tool's `compatible`, and `findings` of rule `avro-invalid` or `schema-invalid` are separate hard errors. Disagreement is E-CONTRACT.
6. Decide per violation: fix forward (new compatible version), or a deliberate break: that needs L3. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "schema policy relaxation or breaking version awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as the BLOCKED `task.result`.
7. Write the governance document: subjects, naming strategy, level per subject with reason, audit table (pair, mode, `compatible`, `breaking_changes`), exceptions with owner and expiry, and the CI rule (the pipeline runs `scripts/event_schema_check.py` with `--base`/`--head` against the registry or main). Registry configuration itself is applied by the owners, never by you. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `event.schema.set`: policy table and audit report bound to the `sha` of step 1.

## Failure Handling

- INVALID_INPUT: fix the named schema field (JSON text, at most 200000 characters) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; list the pairs not audited.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no pair is "compatible" unchecked.
- Registry semantics (level names, subject strategy) are from the registry docs, not from the tools: defer to the registry in use and flag a mismatch.
- L3 or L4 actions: BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

`to_agent`: `A15` (documents the policy). Fill `goal`, `completed`, `decisions` (level per subject), `artifacts` (policy and audit with digests), `open_questions` (violations awaiting approval), `constraints` (valid for `sha`), `verification` (`partial` if pairs were sampled). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a19-event-driven/schema-registry-governance.md; edit the source and run scripts/build_agents.py -->
