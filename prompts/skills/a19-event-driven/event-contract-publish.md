---
name: a19-event-driven--event-contract-publish
description: Use when a new or changed event needs its contract set: AsyncAPI document, CloudEvents attributes and data schema, linted and aligned with A03's contract.
owner: a19-event-driven
tools: [event_asyncapi_lint, event_cloudevents_check, shared_handover_read, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Event contract publish

## When to Use

- An event gets its first documented contract: AsyncAPI channel or operation, message payload schema, CloudEvents envelope. Output is an `event.schema.set`: L2 (`event_design`).
- A03 owns the system-level API and AsyncAPI contract (`api.contract`). You describe the events A19 designs and never rewrite A03's document; a difference is E-CONTRACT.
- Not for changing a schema that is already published (`skill://a19-event-driven--schema-evolution`).

## Procedure

1. Call `shared_handover_read` with `{task_id: <assigner task id>}` and read the `api.contract` and `architecture.blueprint` it names: event names, producers, consumers, channel names. Use `glob` and `grep` for existing `asyncapi` documents and `*.avsc` under the repo. Missing event owner or channel name: BLOCKED `needs=input`.
2. Define the envelope in the schema file with `write`. CloudEvents 1.0 requires `id`, `source`, `specversion` (`1.0`) and `type`. Optional: `datacontenttype`, `dataschema` (an absolute URI), `subject`, `time` (RFC 3339). Attribute names use only lower-case a-z and 0-9, at most 20 characters recommended (the tool allows 64). Give `type` a reverse-DNS prefix and a version suffix (for example `com.example.order.created.v1`), and `source` a stable URI-reference without spaces.
3. Write the payload schema (JSON Schema or Avro) with explicit `required` fields, defaults on every optional field that may be added later, and no free-form maps for fields that consumers branch on. Write the AsyncAPI document (2.x or 3.x, as in the repo) with `info.title`, `info.version`, `channels` or `operations` (`action` is `send` or `receive`), `messages` and local `$ref` only.
4. Call `event_asyncapi_lint` with `document` as the JSON text of the AsyncAPI file (convert YAML to JSON text first). Read `asyncapi_version`, `channels`, `operations`, `messages`, `verdict` and `findings`. Blockers: missing `info` keys, unsupported major version, a 2.x document without a `channels` object, operation without a valid action or channel `$ref`, a server without `url`/`host` or `protocol`. Majors: unresolved `$ref`, duplicate operationId, undeclared channel parameter. Minors (valid per the AsyncAPI schema, but consumers get no contract): a 2.x operation without `message`, a channel without messages, a message without `payload` or content type, empty 2.x `channels`. Fix every blocker and major.
5. Call `event_cloudevents_check` for one realistic sample per event type: `attributes` as a list of `{name, value}` strings (names unique) and `has_data` true or false. Read `valid`, `required_present` (must be 4), `optional_present`, `extension_attributes` and `findings`. Blockers: a required attribute missing or empty, duplicate names, bad characters in a name. Majors: `time` not RFC 3339, `dataschema` not an absolute URI, invalid media type.
6. Run `python3 scripts/event_schema_check.py --task-id $TASK --correlation-id $CORR --root <repo> --json --path <contract-dir>` and compare: its `scanned.asyncapi` and `scanned.cloudevents` counts must equal the number of documents and samples you wrote, and its `findings` of rule `asyncapi` and `cloudevents` must match steps 4 and 5 (same severities). A difference is E-CONTRACT. Exit 1 means a major or blocker finding.
7. Compare your channel names, event types and payload fields with the A03 `api.contract`; list differences. Do not edit A03's file: put them into `open_questions` for A03.
8. Call `shared_artifact_digest` with `{path}` for the AsyncAPI document, the payload schemas and the samples, then see Handover.

## Outputs

- `event.schema.set`: AsyncAPI document, payload schemas, CloudEvents samples, lint evidence (verdicts, counts, finding ids).

## Failure Handling

- INVALID_INPUT: `document` must be JSON text of at most 200000 characters; fix it or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name the documents not linted.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Script finding `tool-missing` (no PyYAML): YAML descriptors were not scanned; keep a JSON copy or report the gap.
- The lint is structural, not the full AsyncAPI schema: say what it does not cover.
- Disagreement with A03: E-CONTRACT, BLOCKED to A03 `needs=input`.

## Handover

`to_agent`: `A15` (documents the contracts). Fill `goal` (document and publish this event contract set), `completed`, `decisions` (type naming, versioning), `artifacts` (digests, producer `A19`), `open_questions` (A03 differences), `verification` (verified only if tool and script agreed). Call `shared_handover_validate`, then `shared_handover_write`.
