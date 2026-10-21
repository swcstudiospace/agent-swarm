---
name: a11-devops--dependency-outage
description: Use when the OCI registry, a CDN or the credential provider is down so builds queue and deploys must stay blocked with E-DEP.
owner: a11-devops
tools: [shared_lease_state, shared_status_report, devops_env_tier, devops_deploy_gate, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Dependency outage

## When to Use

- Pulling or pushing to the registry or CDN fails, or the credential provider is unreachable (agents/A11-devops.md section 5, prompt error_handling).
- The failure is an outage, not a failed apply (`skill://a11-devops--apply-recovery`).

## Procedure

1. Reproduce the failing call once with `bash`, classify the dependency (registry or CDN versus credential provider) from the error and map it to `E-DEP` in the report. Retry the same call at most twice before declaring an outage. Note the start time with `date -u`.
2. Call `shared_lease_state` with `{task_id}`. When `lease.has_lease` is true and `lease.expired` is true, or `ttl_s` minus `age_s` is shorter than the retry wait, finish or hand over now instead of retrying on a dead lease.
3. Call `shared_status_report` with `{task_id, "kind": "blocked", "step": "waiting for <dependency>", "note": "E-DEP"}` so the live bar shows the wait.
4. Call `devops_env_tier` with `{"env_name": "<name>"}` for every environment you work on. Non-production tiers may keep running on the short-lived credential cache for at most 15 minutes from issue (section 5). Take the issue time from the credential provider's cache metadata and compute the age with `date -u`; when you cannot read the issue time, treat the cache as expired. `production` deploys halt.
5. For each queued deploy, call `devops_deploy_gate` with its real `security_verdict`, `frozen`, `drift_frozen` and `env_tier` (read them as in `skill://a11-devops--deploy-gate`, steps 2 to 5) and `"provenance_ok": false`, because provenance cannot be verified while the registry or provider is down. `block` with blocker `provenance` is the expected decision. Builds stay queued; do not drop them.
6. If the outage lasts more than 30 minutes (the MTTR target for infra failures, section 6) or the cache expired on a non-prod tier, call `shared_escalate` with `{task_id, "reason_code": "blocked", "summary": "E-DEP outage: <dependency>", "needs": ["dependency restored"]}`.
7. When the dependency answers again, re-run the blocked step once and clear the blocked status with `shared_status_report` and `{"kind": "progress", ...}`; see Handover.

## Outputs

- Outage note: dependency, start time, queued builds, blocked deploys with `blockers[]`, credential cache state, E-DEP.

## Failure Handling

- DEPENDENCY_UNAVAILABLE on a gate tool: treat the gate as blocked.
- Never use standing credentials or interactive production access to bypass the outage.
- Never mark a deploy clear because the check could not run.

## Handover

Fill `goal` (resume queued builds when the dependency is back), `completed`, `remaining` (queued builds, blocked deploys), `blockers` (the dependency), `constraints` (cache at most 15 minutes, prod halted), `verification` (`unverified` while down). Call `shared_handover_validate`, then `shared_handover_write` to A13, or to A01 when escalated.
