---
name: a11-devops--secret-block
description: Use when a scan finds a static credential in pipeline or infrastructure code and the change must be blocked at once without ever printing the value.
owner: a11-devops
tools: [devops_ci_kind, shared_verdict, shared_log_emit, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Secret block

## When to Use

- `scripts/devops_ci_check.py` or another scan reports a finding with `kind` `secret` in CI, Dockerfile, compose, Terraform or Kubernetes files.
- Rule (agents/A11-devops.md section 4.5): a static credential in infra code is an immediate block under A10 policy. There are no static keys anywhere (section 2).
- Reactive only; general lint is `skill://a11-devops--pipeline-lint`.

## Procedure

1. Run `python3 scripts/devops_ci_check.py --task-id $TASK --correlation-id $CORR --json` (with `--path <file>` to narrow) and read only `id`, `severity`, `kind` and `location` (`file:line`). The script's `evidence` holds up to 80 characters of the line: never copy it into a message, log, artifact or handover.
2. Call `devops_ci_kind` with `{"rel": "<file part of location, without the :line suffix>"}` to label the manifest kind in your report.
3. Call `shared_verdict` with `{"severities": ["critical"]}` (plus the other findings' severities) and record `verdict`; the block holds while it is `fail`.
4. Remove the plaintext with one targeted `edit` of that line, without echoing the value in any message: replace it with a reference to a Vault path or OIDC role that already exists (confirm with `grep` on names, never invent one), or an environment variable provided by the pipeline.
5. Re-run `python3 scripts/devops_ci_check.py --task-id $TASK --correlation-id $CORR --json --path <file>`; the secret finding must be gone. The script skips lines that hold a `${{ ... }}` or `$VAR` reference or an obvious placeholder, so a clean re-run proves only that no pattern match is left, not that history is clean.
6. Call `shared_log_emit` with `{"level": "warn", "message": "static credential finding blocked", "fields": {"location": "<file:line>", "kind": "secret"}, "task_id": "<id>"}`. Put no value, prefix or fragment of it into `fields`.
7. If you cannot remove it at L2 (for example production infrastructure), call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "static credential in <file>", "needs": ["human-approval"]}`.
8. State the rotation request (the owner must rotate a committed credential) in the `shared_handover_write` described under Handover.

## Outputs

- Block record: `location`, `kind`, `verdict`, remediation state, rotation request. No credential material.

## Failure Handling

- Script missing: `skipped:tool-missing`, no invented findings, and say the scan did not run.
- `shared_verdict` DEPENDENCY_UNAVAILABLE: treat as `fail`.
- Never rewrite git history yourself or paste the value to ask for help.

## Handover

Fill `goal` (confirm rotation and that policy allows the change), `completed` (reference replaced), `remaining` (rotation, history scrub decision), `blockers` (open secret), `constraints` (reference by Vault path only), `verification`. Call `shared_handover_validate`, then `shared_handover_write` to A10.
