---
name: a10-security--threat-review
description: Use when A03 publishes a threat.model or blueprint and you must test each threat and control against the design and report gaps.
owner: a10-security
tools: [shared_handover_read, shared_handover_bundle, shared_artifact_check, sec_tls_min_version, sec_header_presence, shared_open_questions, shared_handover_validate, shared_handover_write]
---

## When to Use
- A03 hands over an `architecture.blueprint` or `threat.model` (trust boundaries, data flows, assets). Review is design-time: cheapest place to fix (A10 prompt, decision logic item 5).
- No scanner is run here. Code and diffs go through `skill://a10-security--pr-gate`; infrastructure through `skill://a10-security--iac-review`.

## Procedure
1. Call `shared_handover_read` with the review `task_id`, then `shared_handover_bundle`: take the goal, constraints, listed `artifacts[]`, open questions and the A03 task in `upstream[]` (its `task_id` and `agent`).
2. Call `shared_artifact_check` with `task_id` of that upstream A03 task, optionally `outputs` (`[{kind, uri, digest, producer}]` for the blueprint and threat-model files; omitted, it checks the task's handover and stored artifacts) and `agent` `A03`. Proceed only on results with status `ok` (file exists, digest and producer match); `missing`, `digest_mismatch` and `producer_mismatch` are blocking, and `unchecked` or `no_digest` (for example a non-file uri) means review as unverified.
3. Read the documents with `read`. Build a table of threats (STRIDE category if the document uses one), the control claimed for each, and the component that should implement it. Use `grep` and `glob` over the repository to find whether that component and control already exist in code; cite file and line.
4. For each stated transport control, call `sec_tls_min_version` with `protocols` (the versions the design allows; at least one) and `minimum_version` `TLS1.2` or `TLS1.3` as the policy states. `below_minimum` and `unrecognized` entries are findings.
5. For each stated response-header control, call `sec_header_presence` with `headers` (the headers the design specifies) and `required_headers` (the policy list). `missing` entries are gaps.
6. From the `read` and `grep` evidence, mark every threat `covered`, `partial` or `uncovered`. Uncovered high-impact threats on authentication, authorization, secrets handling or a trust boundary block the blueprint.
7. Call `shared_open_questions` with the review `task_id` to list questions already open, then add yours only if new; do not repeat any.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- A threat-coverage table (threat, control, evidence, status), the transport and header findings, and a list of required changes to the design. Policy gaps become a `policy.recommendation`, never an edit.

## Failure Handling
- Blueprint file missing, `digest_mismatch` or `producer_mismatch`: BLOCKED needs=input; do not review from memory.
- An unknown protocol token comes back in `unrecognized` (a blank one is INVALID_INPUT): list it and ask A03.
- DEPENDENCY_UNAVAILABLE after two retries: report `skipped:tool-missing` for that control and keep it uncovered.
- You review and recommend; you never edit the blueprint.

## Handover
`shared_handover_write` to A03 (gaps) with `goal` (close the listed design gaps), `completed` (threats reviewed), `remaining` (uncovered and partial threats), `decisions` (accepted controls with rationale), `open_questions` (answers needed from A03), `blockers` (high-impact uncovered threats), `artifacts` (kind `security.findings` if a report file was kept, with digest), and `verification` {status `partial` when any artifact was unchecked, else `verified`; checks `shared_artifact_check`, `sec_tls_min_version`, `sec_header_presence`}. Copy A01 on the task result when blockers exist.
