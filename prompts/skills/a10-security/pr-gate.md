---
name: a10-security--pr-gate
description: Use when a code.patch arrives for the security gate and you must run the full scan, cross-check the verdict and issue one signed security gate result.
owner: a10-security
tools: [shared_handover_read, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- A `task.assign` with capability `gate.security` and a `code.patch` input (a pull request from A05/A06) reaches you.
- This is the only full-gate path. Narrow subtasks have their own skills: `skill://a10-security--secret-triage`, `skill://a10-security--dep-request`, `skill://a10-security--iac-review`, `skill://a10-security--migration-review`.
- Not for release candidates; those need a strict scan and a freshness check (`skill://a10-security--release-strict`).

## Procedure
1. Run `swarm-handover-intake`, then call `shared_handover_read` with `task_id` of the gate task. Take the producer's goal, constraints and artifact list. When `found` is false or `status` is not `ok`, continue from the `task.assign` inputs and record the gap as an open question for the final handover.
2. Threat-model the diff before scanning, with `read` and `grep` over the patch files (`git diff <base>` through `bash`) and the `threat.model` when one exists: for each security-relevant change (new input path, authn/authz change, new dependency, crypto, deserialisation) cite the file and line that shows it. A concern the greps cannot see becomes a finding with only the fields `swarm_gate` accepts (`severity`, `summary`, `kind` such as `threat`, `evidence`, `location`, optional `ac_ref` for the CWE). Edit nothing.
3. Record the gate once: call `swarm_gate` with `gate` "security", `task_id` (your gate task), `correlation_id`, and `per_target_findings` `{target: [finding]}` only when step 2 produced findings (a target you omit gets the script's findings only). The scan covers the whole workspace, not just the diff; say which findings lie outside it. A failing verdict returns text starting `FAIL:`. Never run `scripts/sec_gate.py` through `bash` in omp (the guard blocks it) and never pass `dry_run`. Outside omp, run `SWARM_AGENT_SESSION=1 python3 scripts/sec_gate.py --task-id <id> --correlation-id <cid> --json` as a non-recording preview and report it as advisory.
4. Read `.swarm/verdicts/<gate task_id>.security.json` with `read`: `payload.verdict`, `payload.findings` (stable `SEC-` ids, `kind` secrets, sast, iac or deps, `ac_ref` carrying the CWE, redacted evidence), `payload.suppressed`, `payload.runs` (`skipped:tool-missing` or `skipped:no-iac` is a coverage gap, say so) and `payload.scan_digest`. Hand each ambiguous secret hit to `skill://a10-security--secret-triage` and each requested suppression to `skill://a10-security--risk-exception`.
5. Call `shared_verdict` with `severities` set to the severity of every finding in `payload.findings`. If its `verdict` differs from `payload.verdict`, the stricter one wins: report `fail` and an E-INTERNAL entry naming both values.
6. Call `shared_artifact_digest` with `path` `.swarm/verdicts/<gate task_id>.security.json` so the verdict is pinned in the handover by digest.
7. Finish with `yield` carrying `{gate, task_id, correlation_id, verdict, blocking[], advisories[], suppressed[], scan_digest, expires_s}` (86400), built from the verdict file and `shared_verdict`. Each blocking item is `{id, tool: "sec_gate", rule (the summary text before the colon), severity, cwe (from ac_ref), location, fix_hint_md, must_fix_by: "pre-merge"}`; fix hints recommend, never patch. Outside omp, emit it as one fenced `json` block after a short summary.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- The signed `gate.verdict` (`gate="security"`) under `.swarm/verdicts/`, valid 24 h, and the result above.

## Failure Handling
- Scanner unavailable or `swarm_gate` error: the gate is `fail` (fail-closed), never pass-by-default.
- `swarm_gate` has no `strict` or `allow_list` parameter: an omp run never suppresses a finding and never turns a missing audit tool into a finding on its own. Do not claim either.
- `shared_verdict` PARTIAL_SUCCESS or DEPENDENCY_UNAVAILABLE after two retries: report the cross-check as skipped; the script verdict stands unverified.
- Unsigned or malformed `task.assign`: BLOCKED needs=input.
- Never edit product code and never waive your own gate.

## Handover
Call `shared_handover_write` with `to_agent` A01, `goal` (gate verdict for the patch), `completed` (scans run, with `runs` states), `remaining` (fixes owed by A05/A06), `decisions` (cross-check outcome), `artifacts` (kind `gate.verdict`, uri the verdict path, digest from step 6, producer A10), `open_questions` (gaps from steps 1 and 4), `blockers` (blocking finding ids), and `verification` {status `verified` when no `runs` entry is skipped, else `partial`; confidence; checks such as `swarm_gate:fail`, `shared_verdict:fail`}.
