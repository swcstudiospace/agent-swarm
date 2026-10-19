---
name: a11-devops--apply-recovery
description: Use when an IaC apply fails and you must diagnose, retry once, then roll back to the last-good state snapshot and write the post-mortem note.
owner: a11-devops
tools: [shared_attempt_history, devops_env_tier, devops_tf_blast_radius, devops_rollback_target, shared_escalate, shared_log_emit, shared_handover_validate, shared_handover_write]
---

# Apply recovery

## When to Use

- A Terraform or OpenTofu apply, or an equivalent IaC step, failed (03-agents/A11-devops.md section 5, prompt error_handling): diagnose, retry once, then roll back to the last-good snapshot.
- Only the failure path. A registry or credential outage is `skill://a11-devops--dependency-outage`.
- Your omp guard refuses `terraform|tofu apply`, `destroy`, `import`, `taint`, `refresh`, `force-unlock` and `state rm|mv|push` (rule `terraform-apply-destroy`, `BLOCKED needs: human-approval`). You diagnose and prepare; the pipeline applies.

## Procedure

1. Call `shared_attempt_history` with `{task_id}`. Read `attempt`, `last_error` and `retries_left`. Count `attempts` as the failed applies so far: failures in this run plus earlier task attempts whose `last_error` names an apply failure.
2. Call `devops_env_tier` with `{"env_name": "<name>"}`. For `production` (L3) do not retry or roll back yourself: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod apply failed", "needs": ["human-approval"]}` and use its `result_patch`.
3. Diagnose with `bash` (read-only): `terraform plan -refresh-only -out=refresh.plan` and `terraform show -json refresh.plan` (`tofu` is the same). Then plan what a retry would do: `terraform plan -out=retry.plan` and `terraform show -json retry.plan`. Call `devops_tf_blast_radius` with `{"resource_changes": [{type, address, actions}, ...]}` taking `type` and `address` from each `resource_changes` entry and `actions` from its `change.actions`. `destructive` true on a non-prod tier is allowed, but put `note` in the post-mortem. Keep plan files out of git: they can embed secret values.
4. Build the snapshot list `{id, at, applied_ok}` (ISO 8601 with offset) from the state backend's version history; `applied_ok` is true only for a version whose apply the pipeline log shows finished. Call `devops_rollback_target` with `{"snapshots": [...], "attempts": <n>}`.
5. Act on `action`. `retry`: fix the diagnosed cause by editing the IaC, then re-run the failed apply job through the CI system (the pipeline identity applies it); recount `attempts` from the pipeline result. `rollback`: have the pipeline re-apply the IaC revision of `target_id`, then confirm with `terraform plan` that nothing unexpected changes; a direct state restore is guard-blocked, so report BLOCKED `needs=human-approval` naming `target_id`. `escalate` (no good snapshot): `shared_escalate` with `{task_id, "reason_code": "blocked", "summary": "no last-good snapshot", "needs": ["state recovery"]}`.
6. Write the post-mortem: cause, `attempts`, `action`, `target_id`, minutes to recover (compute with `date -u`; target under 30 minutes, section 6). Record it with `shared_log_emit` using `{"level": "error", "message": "apply failure post-mortem", "fields": {"action": "<action>", "target_id": "<id>", "attempts": <n>}, "task_id": "<id>"}`.

## Outputs

- Recovery record: `action`, `target_id`, `attempts`, blast-radius `note`, post-mortem note.

## Failure Handling

- `devops_rollback_target` INVALID_INPUT (timestamp without offset, duplicate `id`): fix the snapshot list once, then BLOCKED `needs=input`.
- `terraform`/`tofu` missing: `skipped:tool-missing`, BLOCKED `needs=tool`; never invent plan output.
- Guard refusal or a failed rollback: `shared_escalate` immediately; do not loop and do not retry the refused command.
- Never print state files or secrets; reference snapshot ids only.

## Handover

Fill `goal` (confirm the environment is healthy or take over recovery), `completed`, `remaining`, `decisions` (`action`, `target_id`), `blockers` (when escalated), `constraints`, `verification`. Call `shared_handover_validate`, then `shared_handover_write` to A13 after a recovery, or to A01 when escalated.
