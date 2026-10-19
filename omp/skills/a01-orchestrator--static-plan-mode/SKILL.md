---
name: "a01-orchestrator--static-plan-mode"
description: "Use when the Task Store or bus is unavailable; continue from the last persisted plan, queue transitions in the scratchpad, replay them on recovery."
disable-model-invocation: true
---

# Static plan mode

## When to Use

- Task Store or bus calls fail persistently (prompts/A01 DEGRADED_MODE). One dead agent: `skill://a01-orchestrator--agent-recovery`.

## Procedure

1. Find the last persisted plan with `glob` on `.swarm/plans/*.json` and `read` it; its task `state` is the state at planning time. Call `shared_scratchpad_get` with `{correlation_id}` to list keys, then `{correlation_id, key: "static-queue-1"}` for transitions queued in earlier outages.
2. Build `tasks: [{id, depends_on, state}]` from the plan with the queue applied and call `orch_dag_blocked`; only `ready` tasks may be planned, and no L3 action. Without the Task Store `swarm_assignment` cannot return a verified assignment, so nothing is spawned in this mode.
3. Call `orch_dag_order` with `{nodes}` to fix the order of what remains; a cycle is `INVALID_INPUT` field `nodes`.
4. Queue each intended transition: `shared_scratchpad_put` `{correlation_id, key: "static-queue-<n>", value: {entries: [{seq, task_id, from, to, reason}]}}`, adding `if_version` when you overwrite a key. Values hold 8 KiB (64 keys per correlation), so open `static-queue-<n+1>` when full. The queue format is a convention of this skill.
5. On recovery, confirm `python3 scripts/orch_status.py --repo <repo> --correlation-id <id> --json` answers. Replay in `seq` order with `swarm_transition` or `python3 scripts/orch_status.py --repo <repo> --transition <task_id> <to> --reason <reason>`; skip an entry whose `from` no longer matches the row's `state` in the store (causal check) or that the store rejects as an illegal transition (E-CONTRACT), and keep it in the report.
6. Call `orch_reconcile` with `{correlation_id}`: it applies the gate and rework rules to every IN_REVIEW task of the correlation (rework, approval to DONE, escalation), not just a report. Confirm the result with `orch_status.py --json`.
7. Call `shared_log_emit` with `{level: warn, message: "static-plan replay", fields: {replayed, skipped}, correlation_id}`. Then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- Queue keys written, replay report (replayed, skipped with reason), final counts.

## Failure Handling

- Scratchpad also failing: stop planning, report BLOCKED with the tool name; never fabricate state.
- A replay entry the store rejects as illegal: record it, do not force it.

## Handover

- `to_agent` `A01` for the next round, `task_id` the orchestration task (or the first queued task when A01 has none). Fill `completed` (replayed transitions), `blockers` (skipped entries), `remaining` (unreplayed queue), `open_questions`.

<!-- generated from prompts/skills/a01-orchestrator/static-plan-mode.md; edit the source and run scripts/build_agents.py -->
