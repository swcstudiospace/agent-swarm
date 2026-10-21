---
name: "a12-release--auto-rollback"
description: "Use when a canary guardrail is breached or a sev 2 or worse incident names the release and you must issue the rollback.command without approval."
disable-model-invocation: true
---

# Automatic rollback command

## When to Use

- `skill://a12-release--canary-step-decision` returned rollback, or an `incident.alert` with sev 2 or worse names this release (prompt rollback triggers).
- Rolling back needs no approval (spec section 4, item 3). Only the issuing path lives here; a rollback that fails is `skill://a12-release--rollback-failure-escalation`.

## Procedure

1. Call `rel_guardrail_breaches` with the last sample as `observed` and the plan limits (`error_rate_max`, `p95_ms_max`, `slo_burn_max` only) to confirm the breach and name the metrics in `breaches`. For an incident-driven rollback with no metric breach, record the alert `incident_id` instead and skip this confirmation.
2. Call `rel_resume_point` with `{steps_pct, confirmed, in_flight, breach: true}` taken from `.swarm/releases/<release_id>.plan.json` (use `read`) and the confirmed steps found as in `skill://a12-release--resume-partial-promotion` step 2. It must return `action: rollback`; any other action means the inputs are wrong, fix them once.
3. Use `write` to create the command file in the workspace outside `.swarm/` (your guard refuses writes there): by convention `release-records/<release_id>.rollback.json` (a convention of this skill, not a runtime path), holding `release_id`, `correlation_id`, `task_id`, the step reached (`confirmed` and `in_flight`), the breached metrics or alert id, the previous release identifier from your last `release.record` (`glob` `release-records/*.record.json`), and `reason`. You hold no signing tool and must never hand-sign: the command is unsigned until the runtime signs it, so say so in the result. If the task requires a signed command and nothing can sign it, call `shared_escalate` with `{task_id, reason_code: "blocked", summary, needs: ["tool: signing key"]}`.
4. Call `shared_artifact_digest` with `{path: "release-records/<release_id>.rollback.json"}`.
5. Stop every further `promote.command` for the release. Hand the incident to A13 with the breach evidence through `shared_handover_write` (see Handover) so A13 can declare and scope it.
6. Call `swarm_gate` with `{gate: "release", task_id: $TASK, correlation_id: $CORR}` (omp tool; your guard blocks `scripts/rel_plan.py` in `bash`, and outside omp the same run is `python3 scripts/rel_plan.py --task-id $TASK --correlation-id $CORR --json`) and report whether the summary says `FROZEN` and the verdict as they now stand. You cannot add a freeze yourself: that is `skill://a12-release--freeze-on-incident`.

## Outputs

- An unsigned `rollback.command` file, its digest, and an incident handoff naming the breached metrics.

## Failure Handling

- `rel_resume_point` INVALID_INPUT names the field: correct `steps_pct` from the plan file once, else BLOCKED needs=input.
- No previous release identifier on record: say so in `open_questions`; do not guess one.
- Command could not be applied: leave this skill and use `skill://a12-release--rollback-failure-escalation`.

## Handover

- `to_agent`: A13. `goal`: take the incident for the rolled-back release. `completed`: breach confirmed, rollback issued at the step reached. `remaining`: verify stabilisation, declare the incident, post-incident review. `decisions`: why rollback, with the metrics. `artifacts`: the rollback file and digest, producer A12. `constraints`: no promote until A13 clears it. `verification`: verified only when step 1 ran.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.

<!-- generated from prompts/skills/a12-release/auto-rollback.md; edit the source and run scripts/build_agents.py -->
