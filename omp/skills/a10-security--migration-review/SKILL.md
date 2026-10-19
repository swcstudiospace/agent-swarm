---
name: "a10-security--migration-review"
description: "Use when A07 sends a schema.migration and you must check its SQL for embedded credentials, over-broad grants and unclassified PII columns."
disable-model-invocation: true
---

## When to Use
- A `schema.migration` (SQL or migration-framework file) from A07 reaches the security gate. A10 consumes it per `agents.json`.
- Data scope only: grants, seed values, columns holding personal or secret data. Infrastructure belongs to `skill://a10-security--iac-review`.

## Procedure
1. Call `shared_handover_read` with the task `task_id`. Take the migration files, the expand/contract phase, the tables touched and any PII classification A07 attached.
2. Read each migration with `read`. Collect the lines of seed data, `INSERT` statements, `COMMENT`s and connection or role statements into a list, overwriting every literal value with a same-length run of `x`.
3. Call `sec_credential_scan` with that `lines` list (at most 10000 lines of 4096 characters). Hits give a rule name and a 1-based line number within your list; map back to the file and line. Masked values keep `generic_secret_assignment` firing on names such as `password`, but `INSERT ... VALUES` rows do not match its `name = value` shape (an `UPDATE ... SET password = '...'` does), and format-specific key rules cannot fire on a mask: so also `grep` the raw file for `AKIA`, `ghp_`, `BEGIN PRIVATE KEY` and report only the line number, never the text.
4. `grep` for privilege statements: `GRANT ALL`, `GRANT .* TO PUBLIC`, `WITH GRANT OPTION`, `CREATE ROLE .* SUPERUSER`, `ALTER DEFAULT PRIVILEGES`. Each is a finding with the role and the object.
5. `grep` column definitions for names that suggest personal data (`email`, `phone`, `ssn`, `dob`, `address`, `token`, `password`). A column that stores a credential without hashing, or personal data absent from A07's classification, is a finding addressed to A07.
6. Read for threats: does the migration widen who can read sensitive tables, drop an audit trail, or relax a constraint that enforces tenancy?
7. Rate the findings from `read`, `grep` and `sec_credential_scan`, by convention of this skill: a credential value in a migration `critical` (the gate rates committed credentials critical), an over-broad grant `major`, an unclassified PII column `minor` unless it holds secrets. Each finding is `{severity, summary, kind: "credential"|"grant"|"pii", location: "file:line", evidence}`; evidence never holds a value.
8. Record the gate once: call `swarm_gate` with `gate` "security", `task_id`, `correlation_id` and `per_target_findings` `{target: [findings]}`; its script adds its own secret scan of the workspace. The recording rules (no `bash` run of the script, no `dry_run`) are those of `skill://a10-security--pr-gate` step 3. Read `.swarm/verdicts/<gate task_id>.security.json` with `read`, then call `shared_verdict` with the severities of `payload.findings` and compare with `payload.verdict`; the stricter wins.
9. Call `shared_artifact_digest` on the verdict path, then `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- A finding list (`file:line`, kind: credential, grant or PII, severity, recommendation) and the recorded pass or fail.

## Failure Handling
- `sec_credential_scan` PARTIAL_SUCCESS: keep unscanned lines as unresolved; DEPENDENCY_UNAVAILABLE after two retries: report `skipped:tool-missing`.
- Migration too large for one call (over 10000 lines): split into batches of the seed and role statements only.
- Never run SQL or connect to a database; review text only.
- A credential found is also a rotation task: tell A07 and A05, do not quote the value.

## Handover
`shared_handover_write` to A07 with `goal` (resolve the migration findings), `completed` (files and lines reviewed), `remaining` (fixes), `decisions` (severity rationale), `open_questions` (columns needing PII classification), `blockers` (blocking finding ids), `artifacts` (kind `gate.verdict`, the verdict path, digest from step 9, producer A10), and `verification` {status `verified` or `partial`; checks `sec_credential_scan:findings=<n>`, `grep:grants`, `shared_verdict:<verdict>`}.

<!-- generated from prompts/skills/a10-security/migration-review.md; edit the source and run scripts/build_agents.py -->
