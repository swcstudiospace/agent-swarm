---
name: a11-devops--prod-infra-change
description: Use when an iac.change touches production infrastructure and needs a change ticket, blast-radius note, rollback plan and an L3 human approval before apply.
owner: a11-devops
tools: [devops_env_tier, devops_tf_blast_radius, devops_freeze_window, devops_change_gate, shared_verdict, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Production infrastructure change

## When to Use

- An `iac.change` targets a production environment (autonomy ceiling L3, agents/A11-devops.md section 4.1).
- You never apply production changes alone: the result is BLOCKED `needs=human-approval` with plan, diff and rollback attached (prompt, OPERATING_PROCEDURE step 6). Your guard also refuses `terraform apply|destroy`, prod `kubectl` changes and `helm upgrade`.

## Procedure

1. Call `devops_env_tier` with `{"env_name": "<name>"}`; continue only when `tier` is `production` and `autonomy` is `L3`.
2. Get the production plan from the pipeline's plan job: production access is by pipeline identity only, so do not run `terraform plan` against production with session credentials. Turn the plan file the job publishes into JSON with `terraform show -json plan.bin` (read-only). Call `devops_tf_blast_radius` with `{"resource_changes": [{type, address, actions}, ...]}`, taking `type` and `address` from each `resource_changes` entry and `actions` from its `change.actions`. Keep `note` (it is the blast-radius text) and `destructive`. Keep the plan files out of git: they can embed secret values. No plan from the pipeline: BLOCKED `needs=input`.
3. Get the instant with `date -u +%Y-%m-%dT%H:%M:%SZ` and call `devops_freeze_window` with `{"now": "<instant>", "windows": [{start, end}, ...]}` using the freeze calendar from the task. `frozen` true means hold until `remaining_s` elapses. No calendar supplied is BLOCKED `needs=input`, never "clear".
4. Run `python3 scripts/devops_ci_check.py --task-id $TASK --correlation-id $CORR --json --path <iac-dir>` and call `shared_verdict` with the finding severities; `fail` stops the change (Checkov gates come from A10).
5. Write the rollback plan: the previous state version id from the state backend's version history and the revert steps; no nameable previous version means BLOCKED `needs=input`. Call `devops_change_gate` with `{"env_tier": "production", "change_ticket": "<id, 64 characters at most>", "blast_radius": "<note>", "rollback_plan": "<text>"}`. `blocked` lists `missing` evidence: fill it. `needs-human-approval` is the expected decision.
6. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod iac.change awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as the BLOCKED `task.result`.
7. Write the diff and rollback plan as files in the workspace outside `.swarm/` (the guard refuses writes there), at the paths the task names; when none are named use `<iac-dir>/records/<change_ticket>.diff` and `.rollback.md` (a convention of this skill). Call `shared_artifact_digest` with `{path}` for each (and for `plan.bin` only if it is kept in the workspace), then see Handover.

## Outputs

- `iac.change` with ticket, `note`, `destructive`, plan, diff and rollback artifacts; BLOCKED `needs=human-approval`.

## Failure Handling

- `destructive` true and no tested rollback: BLOCKED, do not request approval yet.
- INVALID_INPUT on `devops_tf_blast_radius` (unsupported action list): regenerate the plan JSON once, then BLOCKED `needs=input`.
- DEPENDENCY_UNAVAILABLE on any gate tool: retry twice, then treat the gate as not passed.
- `terraform` or `tofu` missing: `skipped:tool-missing`, BLOCKED `needs=tool`; never describe a plan you did not produce.
- Never use interactive production access or standing credentials; pipeline identities only.

## Handover

Fill `goal` (approve and apply this production change), `completed`, `remaining` (human approval, A10 review of the `iac.change`, which A10 consumes), `artifacts` (plan, diff, rollback with digests, producer A11), `decisions` (gate `decision`, `destructive`), `blockers` (`human-approval`), `constraints` (freeze window, ticket), `verification` (`partial`). Call `shared_handover_validate`, then `shared_handover_write` to A01.
