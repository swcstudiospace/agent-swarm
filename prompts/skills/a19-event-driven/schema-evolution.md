---
name: a19-event-driven--schema-evolution
description: Use when an event.schema task changes an existing event schema (JSON Schema or Avro) and the change needs a compatibility verdict and a safe rollout order.
owner: a19-event-driven
tools: [event_schema_compat, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Schema evolution

## When to Use

- An existing event schema gets a new, removed, retyped or constrained field, or a changed enum, and a base version is available.
- Not for a first publication of a contract (`skill://a19-event-driven--event-contract-publish`), nor for registry-wide compatibility policy over many versions (`skill://a19-event-driven--schema-registry-governance`).
- A change that is incompatible in the requested mode is `breaking_event_schema`: autonomy L3, never merged by you alone. Compatible changes are `event_design`: L2.

## Procedure

1. Read the base and head schema files with `read`. The base is the published version (a file in the repo, or `git show <ref>:<path>` through `bash`, written with `write` to a scratch file next to the head, kept out of the patch). Both must be under the repo root.
2. Choose `mode` from the rollout, not from habit. `backward`: consumers on the head read data written with the base (consumers upgrade first; Avro and Confluent default). `forward`: consumers still on the base read data written with the head (producers upgrade first). `full`: both (independent rolling upgrades, or retained events replayed). No rollout order in the task: BLOCKED `needs=input`.
3. Run `python3 scripts/event_schema_check.py --task-id $TASK --correlation-id $CORR --root <repo> --json --base <base-file> --head <head-file> --mode <mode> --format <auto|json-schema|avro> --path <schema-dir>`. `--format auto` reads any file other than `.avsc` with Avro-only shapes (records, enums, fixed, maps, arrays or Avro-only primitives) as Avro; pass `--format` when unsure. Exit 0 is `status: ok`, exit 1 is `status: fail` (a blocker or major finding, including `scan-incomplete` for any skipped, truncated or unparsable in-scope input), exit 2 is a taxonomy error. Read `comparison.performed` first: `false` means no comparison happened (unparsable file or YAML without PyYAML) and is never "compatible".
4. Call `event_schema_compat` with `format`, `mode`, `base_schema` and `head_schema` set to the two file texts (JSON text). Read `compatible`, `backward_compatible`, `forward_compatible`, `breaking_changes` and `findings`. They must equal the script `comparison` values; any disagreement is E-CONTRACT: report both, state no verdict.
5. Decide from the `event_schema_compat` result. `breaking_changes` 0 and `compatible` true: L2, continue. Any major or blocker in the requested mode (Avro reader field without default, type not promotable, enum symbol unknown to the reader, Avro decimal precision or scale changed, JSON Schema `required` added or a closed `additionalProperties`): do not ship. In JSON Schema an open model (the default) makes an added property definition a backward break and a closed one a forward break. A keyword reported as "cannot be analysed" (`allOf`, `not`, `if`, `patternProperties`, ...) that differs between the schemas is a major: compatibility is not proven, review it by hand. Prefer a compatible redesign with `edit` (add the field optional or with a default, deprecate instead of removing, widen instead of narrowing) and rerun steps 3 and 4. A change that cannot be made compatible is a new event `type` or topic version, dual-published, which is L3.
6. When `event_schema_compat` reports `compatible` true for the requested mode but the other direction fails (`backward_compatible` or `forward_compatible` false), record that in `decisions` as a rollout constraint (for example "backward only: deploy all consumers before the first producer").
7. For an L3 case call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "breaking event schema awaiting L3 approval", "needs": ["human-approval"]}`, attach the findings and the migration plan (dual-publish window, consumer cutover order, retention of old events), and return its `result_patch` as the BLOCKED `task.result`.
8. Write the head schema and a short change note as the `event.schema.set` files, call `shared_artifact_digest` with `{path}` for each, then see Handover.

## Outputs

- `event.schema.set`: head schema, compatibility note (mode, verdict, rollout order) and any L3 migration plan.
- Evidence: the script `comparison` object and `event_schema_compat` fields `compatible`, `breaking_changes`, finding ids.

## Failure Handling

- `event_schema_compat` INVALID_INPUT: fix the named field (`base_schema` or `head_schema` must be JSON text, at most 200000 characters; a `$ref` that does not resolve, such as the leading-zero index `#/allOf/01`, is reported on the field) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked part, give no verdict.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` and rely on the script, naming the gap.
- Script `tool-missing` and `scan-incomplete` (no PyYAML): convert the YAML to JSON text yourself for the tool call, or BLOCKED `needs=tool`.
- Rules that depend on a semantic the tools do not model (for example a registry-side check) are not verified: say so.
- L3 actions: BLOCKED `needs=human-approval`; never retry an escalation.

## Handover

`to_agent`: `A15` (consumes `event.schema.set`); name `A01` as assigner in `remaining` when the result is BLOCKED. Fill `goal` (document and publish this schema change), `completed`, `decisions` (mode, rollout order, `breaking_changes`), `artifacts` (schema files with digests, producer `A19`), `open_questions` (approval pending), `verification` (verified only when script and tool agreed). Call `shared_handover_validate`, then `shared_handover_write`.
