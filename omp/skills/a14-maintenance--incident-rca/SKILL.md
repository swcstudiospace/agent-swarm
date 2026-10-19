---
name: "a14-maintenance--incident-rca"
description: "Use when an incident is closed or an issue cluster needs a root cause; build an evidence-backed causal chain and an rca.report without choosing the remediation."
disable-model-invocation: true
---

# Incident root-cause analysis

## When to Use

- Capability `maint.rca`: an incident has been closed by A13 or humans, or several issues share a stack trace, and the team needs the cause and escape point (A14 spec section 2: issue-cluster triage by stack-trace similarity).
- Not to decide hotfix or rollback (`a14-maintenance--hotfix-or-rollback`) and not to decide an action after a patch (`a14-maintenance--regression-watch`).

## Procedure

1. Call `shared_handover_bundle` with `{task_id}` to get the goal, acceptance, handover, upstream results, artifacts, `verdicts` and attempts of the incident's task in one object. Check `truncated` and `task_known` before relying on it. Use `read` on `.swarm/incidents/<incident_id>.json` for the alert itself (`at`, `evidence`, `suspect_changes`).
2. Call `shared_attempt_history` with `{task_id}` for each involved task to place failures in time: transitions, rework loops and `last_error`.
3. Call `shared_open_findings` with `{task_id}` for the findings of failing gate verdicts that are still unexpired. A gate verdict in the bundle `verdicts` with `expired: true`, or a gate with no verdict at all, before the change shipped is a candidate escape point (expired findings are not listed by this tool).
4. For each log or trace file you cite, call `shared_artifact_digest` with `{path}` so the report names the exact bytes you read. Group stack traces by identical top frames with `grep` and count occurrences per group.
5. Build the causal chain from the `shared_artifact_digest` digests, finding ids and timestamps gathered so far: trigger, proximate cause, contributing condition, and the check that should have caught it (test, review, alert). A link without evidence is written as `hypothesis`.
6. Call `shared_open_questions` with `{task_id}` and list unresolved questions in the report instead of closing them by assumption.
7. Use `write` to save the `rca.report` at the path the task names and call `shared_artifact_digest` with it. Call `shared_handover_validate`, then `shared_handover_write` with the report as an artifact.

## Outputs

- An `rca.report`: timeline, causal chain with evidence per link, escape point, hypotheses, follow-up candidates (not assigned work).

## Failure Handling

- `shared_handover_bundle` says `task_known: false`: ask A01 for the correct task id through `open_questions`; do not reconstruct the incident from memory.
- Logs are missing or the digest call returns `INVALID_INPUT`: name the gap; the chain is then `partial`, confidence lowered accordingly.
- Do not remediate, roll back or close the incident from this skill.

## Handover

- Fill `goal` (decide follow-ups from this cause), `completed` (chain steps proven), `remaining` (hypotheses to test), `decisions` (cause statement and basis), `artifacts` (report and logs, each with digest), `open_questions`, `verification` (status `partial` when any link is a hypothesis).
- `shared_handover_write` to `A01` so follow-ups compete in planning. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/incident-rca.md; edit the source and run scripts/build_agents.py -->
