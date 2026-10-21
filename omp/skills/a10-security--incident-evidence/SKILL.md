---
name: "a10-security--incident-evidence"
description: "Use when A13 sends an incident.alert and needs a forensic evidence bundle: relevant artifacts with digests and log lines checked for credentials before sharing."
disable-model-invocation: true
---

## When to Use
- An `incident.alert` from A13 asks for forensic context (it reaches you in the task inputs: it is not in A10's ACP `accepts` list) (A10 prompt, incident duty; 03-agents A10 section 7). You assemble evidence; you do not issue a gate verdict, fix code or contact anyone outside the swarm.
- Emergency disclosure is coordinated by humans. Repository secret hits are handled by `skill://a10-security--secret-triage`.

## Procedure
1. Call `shared_handover_bundle` with the incident `task_id`. It returns the goal, acceptance criteria, the latest handover, upstream results, artifacts, verdicts, lease and attempts in one bounded object; check `truncated` and list what is missing.
2. Identify evidence candidates: log files, deployment manifests, the last security verdicts, SBOM files and config snapshots named by the alert or found with `glob` and `grep` under the workspace. Keep a note of why each is relevant (time window, service, request id).
3. Before anything leaves your hands, screen log excerpts: split the lines you intend to share, overwrite every token, key and password value with a same-length run of `x`, and call `sec_credential_scan` with the resulting `lines` (at most 10000 lines per call, each at most 4096 characters). Each hit (rule and line number) marks a line that held credential-like material; drop or further mask the original line in the bundle and note that it was redacted, never quoting the value.
4. For each kept file call `shared_artifact_digest` with its `path` so the bundle lists a sha256 digest; only files inside the workspace or the swarm directory can be digested, any other is listed by name only (INVALID_INPUT).
5. Compile the evidence index in the `task.result` summary and handover (A10 has no write tool; use `read` and `grep` for the facts): artifact, digest, why relevant, redaction applied, and the observations that bear on security (suspicious access, expired attestation, missing signature). Stay factual; separate observation from hypothesis.
6. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover). Do not paste log content into the handover; list artifacts and digests.

## Outputs
- An evidence index with digests, a redaction note per file, and a short factual timeline for A13. Nothing contains credential values.

## Failure Handling
- `shared_handover_bundle` `task_known` false: BLOCKED needs=input with the task id you were given.
- `sec_credential_scan` PARTIAL_SUCCESS or DEPENDENCY_UNAVAILABLE after two retries: withhold the unscanned excerpts and say so.
- Scan output stored encrypted, redacted before any cross-agent advisory (A10 prompt, security section).
- A live credential seen in a log means rotation: tell A13 and, through them, the humans; do not rotate anything yourself.

## Handover
`shared_handover_write` to A13 (bundle) with `goal` (evidence for the incident), `completed` (files collected and screened), `remaining` (items not obtainable), `decisions` (what was redacted and why), `artifacts` ({kind, uri, digest, producer} for every file, producer being its author), `open_questions` (missing logs or time windows), `constraints` (redacted copies only, no credential values), and `verification` {status `partial` while any excerpt stayed unscanned, else `verified`; checks `sec_credential_scan:findings=<n>`}.

<!-- generated from prompts/skills/a10-security/incident-evidence.md; edit the source and run scripts/build_agents.py -->
