---
name: "a10-security--iac-review"
description: "Use when A11 sends an iac.change or deployment.manifest and you must review infrastructure exposure, TLS, headers and privilege settings and score each finding."
disable-model-invocation: true
---

## When to Use
- An `iac.change` or `deployment.manifest` (Terraform `*.tf`, Kubernetes or compose `*.yaml`/`*.yml`) from A11 needs the infrastructure half of the security gate.
- Infrastructure only. Data schemas go to `skill://a10-security--migration-review`; application code to `skill://a10-security--pr-gate`.

## Procedure
1. Call `shared_handover_read` with the task `task_id`. Take the target environment, the changed files and A11's stated intent.
2. Read each changed file with `read` and `grep` for what the gate's greps cannot see: public buckets or load balancers, wildcard IAM actions, missing encryption at rest, secrets in plain variables, disabled logging. Each is a finding `{severity, summary, kind: "iac", location: "file:line", evidence}`; add `ac_ref` (a CWE) only when you are certain (the script uses CWE-284 for exposure).
3. For every TLS listener or ingress setting call `sec_tls_min_version` with `protocols` (versions the config allows; at least one) and `minimum_version` (`TLS1.2` or `TLS1.3`, the policy). `below_minimum` entries are findings (by convention of this skill `major`), `unrecognized` entries are findings (`minor`).
4. For every edge or proxy configuration call `sec_header_presence` with `headers` (the ones configured) and `required_headers` (the policy list, for example `Strict-Transport-Security`; at least one). `missing` entries are findings (by convention `minor`).
5. Where a scanner such as Checkov or tfsec supplied a CVSS base score, call `sec_cvss_band` with that `score` (0 to 10); where none exists, keep the gate severity (`info` to `blocker`) and do not invent a score.
6. Record the gate once: call `swarm_gate` with `gate` "security", `task_id`, `correlation_id` and `per_target_findings` `{target: [findings from steps 2 to 5]}`. Its script adds the IaC findings: `iac.open-ingress` (`0.0.0.0/0` or `::/0`, major), `iac.privileged` (major), `iac.host-network` (hostNetwork, hostPID, hostIPC, major) and `iac.priv-escalation` (allowPrivilegeEscalation or runAsUser 0, minor), on `*.tf`, `*.yaml` and `*.yml` files, comment lines skipped; with no IaC file `runs.iac` is `skipped:no-iac`. The recording rules (no `bash` run of the script, no `dry_run`) are those of `skill://a10-security--pr-gate` step 3.
7. Read `.swarm/verdicts/<gate task_id>.security.json` with `read`. Call `shared_verdict` with the severities of every `payload.findings` entry and compare with `payload.verdict`; the stricter wins. A critical IaC misconfiguration blocks (A10 decision logic item 2). Write a fix recommendation per finding for A11.
8. Call `shared_artifact_digest` on the verdict path, then `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- The security verdict for the IaC change, a finding list (id, `file:line`, severity, fix recommendation) and the list of rule families checked.

## Failure Handling
- Production DAST or any live probing is out of scope: staging only, coordinated with A11 and A12.
- `swarm_gate` error or a `skipped:*` coverage entry in `payload.runs`: record it; the gate fails closed.
- INVALID_INPUT on `sec_cvss_band` (score outside 0 to 10): drop the score, keep the gate severity, note the scanner output as unusable.
- You recommend; you never apply or edit infrastructure (A10 has no write or edit tool).

## Handover
`shared_handover_write` to A11 with `goal` (fix the listed infrastructure findings), `completed` (rule families and files reviewed), `remaining` (fixes per finding id), `decisions` (severity choices and any score used), `constraints` (no production scanning), `blockers` (blocking finding ids), `artifacts` (kind `gate.verdict`, the verdict path, digest from step 8, producer A10), and `verification` {status `verified` or `partial`; checks `swarm_gate:iac`, `sec_tls_min_version`, `sec_header_presence`, `shared_verdict`}.

<!-- generated from prompts/skills/a10-security/iac-review.md; edit the source and run scripts/build_agents.py -->
