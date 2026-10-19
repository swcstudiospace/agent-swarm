---
name: "a14-maintenance--hotfix-or-rollback"
description: "Use when an incident.alert at sev 1 or 2 (sev 1 is the worst) arrives before any patch exists and you must choose hotfix, rollback or defer under the 4 h rule."
disable-model-invocation: true
---

# Hotfix or rollback

## When to Use

- A13 sends `incident.alert` with a declared incident and no fix has shipped yet (sev 1 or 2 primes the hotfix path; sev 1 is the worst).
- Not for a regression after your own patch shipped (`a14-maintenance--regression-watch`) and not for an after-the-fact cause analysis (`a14-maintenance--incident-rca`).

## Procedure

1. Use `read` on the alert (`.swarm/incidents/<incident_id>.json` and the task bundle) for the suspect change. Get the fix estimate in hours from the owner (A05, A06 or A07) via the handover; if nobody has estimated it, set `remaining` to "estimate root-cause fix" and do not guess.
2. Call `maint_hotfix_decision` with `{severity, fix_est_h, rollback_available, rollback_risk}`. Pass the `sev` of the `incident.alert` unchanged: it uses A13's scale where 1 is the worst, and the tool primes the hotfix path for sev 1 and 2 and returns `defer` for 3 to 5. `rollback_available` is true only when `release.record` names a tested rollback target; `rollback_risk` is `high` when that rollback crosses a schema or data migration.
3. Read `path`. `rollback`: prepare a rollback recommendation for A12 (A12 decides deploy-side actions); with `high` rollback risk it also needs the escalation of step 6. `hotfix`: prepare a `hotfix.task` with minimal-diff discipline. `defer` with `primes_hotfix: true` (no rollback and a fix of 4 h or more): go to step 6.
4. Call `shared_open_findings` with `{task_id}` of the suspect change to list the unexpired failing-gate findings on it; a hotfix still needs the same A08, A09 and A10 gates, only prioritised (A14 spec section 4, rule 3), and an open blocking finding is carried into the `hotfix.task` constraints.
5. If the incident is driven by a vulnerability, call `maint_patch_deadline` with `{detected_at, kev, severity, sla_hours}` (`detected_at` UTC `YYYY-MM-DDTHH:MM:SSZ`; `severity` is the vulnerability word critical, high, medium or low, not the incident sev) and carry `deadline` into the hotfix.task. For non-security incidents the deadline comes from A13 and A01, not from this tool.
6. Call `shared_escalate` with `{task_id, reason_code: "policy", summary, needs: [...]}` when the path is `defer`, when a rollback is `high` risk, or when any hotfix needs signed approval; return its `result_patch`. Emergency work never skips signed approvals (A14 spec section 7). You never close the incident.
7. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- A decision record: `path`, `primes_hotfix`, `reason`, and the inputs used.
- Either a `hotfix.task` (minimal diff, normal gates, owner class) or a rollback recommendation for A12.

## Failure Handling

- `INVALID_INPUT` naming `fix_est_h` or `severity`: the input is missing or out of range; ask through `open_questions`, do not substitute a value.
- `maint_hotfix_decision` timing out twice: escalate with `reason_code: "blocked"`; a rollback preference never defaults to hotfix on silence.
- `primes_hotfix: false` with `defer`: leave the alert to the normal `patch.task` flow.

## Handover

- Fill `goal` (execute the chosen path), `completed`, `remaining`, `decisions` (the tool `path` and `reason`), `blockers` (approvals pending), `constraints` (gates unchanged, signed approval, A14 cannot close incidents), `verification`.
- `shared_handover_write` to `A12` for a rollback recommendation, to `A01` for a hotfix.task so it assigns the worker. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/hotfix-or-rollback.md; edit the source and run scripts/build_agents.py -->
