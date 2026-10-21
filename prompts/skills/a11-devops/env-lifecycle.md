---
name: a11-devops--env-lifecycle
description: Use when an environment.request, a closed PR, an elapsed TTL or a downscale asks to create or tear down an ephemeral or staging environment.
owner: a11-devops
tools: [devops_env_tier, devops_change_gate, devops_cost_overrun, shared_lease_state, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Environment lifecycle

## When to Use

- `environment.request` routed by A01, a closed PR with a live `env-pr<N>`, or an environment whose TTL elapsed.
- Ephemeral and staging only (autonomy L2, agents/A11-devops.md section 4.1). Production is `skill://a11-devops--prod-infra-change`.

## Procedure

1. Call `devops_env_tier` with `{"env_name": "<name>"}`. `tier` `production` (autonomy L3) ends this skill: refuse create or teardown and switch skills. A name with no marker returns INVALID_INPUT: ask for the tier, never guess.
2. Call `devops_change_gate` with `{"env_tier": "<tier>"}`; for ephemeral and staging `decision` is `allowed`. Any other decision is a stop.
3. For teardown, decide expiry from the environment record: expired when now (`date -u`) minus the creation time in the environment record is at least `ttl_s` (the section 3 example uses `ttl_s` 86400). No registry tool computes it; do the arithmetic and write both numbers in the result.
4. Before any destroy, call `shared_lease_state` with `{task_id}`: this is your own task lease, not an environment lock. Continue only when `lease.has_lease` is false or `lease.expired` is false; an expired lease means stop and hand over. The environment itself is protected by the IaC state lock: never bypass it (no `-lock=false`, no `force-unlock`).
5. Run `python3 scripts/devops_ci_check.py --task-id $TASK --correlation-id $CORR --json --path <iac-dir>` first and fix `major` findings. Then provision or destroy by editing the module inputs on your task branch; the pipeline applies the change. Your guard refuses `terraform apply|destroy`, so report the environment as requested, never `provisioned` or `decommissioned`, until the pipeline log or `terraform output -json` shows the result; if that cannot be seen, BLOCKED `needs=human-approval` for the apply. Secrets mode is `vault-oidc`: reference Vault paths and OIDC roles only.
6. After provisioning, call `devops_cost_overrun` with `{"modeled_cost": <budget>, "actual_cost": <first reading>}` to store the baseline `ratio` (the ephemeral budget cap applies).
7. Write the environment record as a file in the workspace outside `.swarm/` (your guard refuses writes there), at the path the task names; when none is named use `<iac-dir>/records/<env_id>.env.json` (a convention of this skill, not a runtime path). Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `environment.provisioned` JSON: `env_id`, `tier`, `ttl_s`, `endpoints`, `secrets_mode`, `provenance` (section 3 shape); or `environment.decommissioned` with `env_id` and reason (`pr-closed`, `ttl-elapsed`, `downscale`).
- Cost baseline `ratio`.

## Failure Handling

- `devops_env_tier` INVALID_INPUT: ask once, then BLOCKED `needs=input`.
- Own lease expired or the state lock is held: never destroy; report it with `age_s`.
- IaC apply failure: switch to `skill://a11-devops--apply-recovery`.
- Registry or credential outage: `skill://a11-devops--dependency-outage`.

## Handover

Fill `goal` (use or confirm removal of `env_id`), `completed`, `artifacts` (environment record path, digest, producer A11), `decisions` (tier, TTL basis), `constraints` (TTL, budget cap, L2 only), `verification`. Call `shared_handover_validate`, then `shared_handover_write` to the requester named in the task (A01 when unknown); a second write to A13 for monitoring when an environment was created.
