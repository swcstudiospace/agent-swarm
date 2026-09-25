---
name: swarm-orchestrate
description: "Drive AgentSwarm SDLC waves in-session via A01: status → lease → one tasks[] call (explicit agent per task) → ingest (runs reconcile) → DONE or ESCALATED. Parent never implements domain work."
disable-model-invocation: false
---

# swarm-orchestrate (in-session A01 driver)

## When to Use
- You are a01-orchestrator and a wave is ready, or the user explicitly asked for the full SDLC through the swarm.
- You were given a plan whose tasks carry `agent` fields.

Never use from a specialist session.

## Prerequisites
- Tools: swarm_plan, swarm_status, swarm_transition, swarm_ingest, task (plus read tools). You do **not** have `swarm_gate`: the gate agents (a08-qa, a09-reviewer, a10-security, a12-release) run it themselves.
- A correlation from swarm_plan or /swarm.

## Procedure

1. `swarm_status` for the correlation → the ready tasks, each with its `agent` and gate notes (`gate`, `gate_for`).
2. Lease every task of the ready wave with `swarm_transition`: PLANNED → CLAIMED, then CLAIMED → IN_PROGRESS. Lease gate tasks before their gate agent runs: `swarm_gate` refuses a gate task that is not IN_PROGRESS.
3. Make **one** `task` call for the wave:
   - `tasks[]` holds one item per task, each with `agent: <slug>` and a task.assign payload: `task_id`, `correlation_id`, `capability`, `inputs`, `acceptance`, `risk_class`.
   - Add `schemaMode: "strict"` to every item for a08-qa, a09-reviewer, a10-security and a12-release.
   - Gate agents run `swarm_gate` themselves. Never pass them findings or a verdict.
4. `swarm_ingest` every child's task.result before the next wave. Ingest applies the result and runs reconcile itself; there is no separate reconcile call. Reconcile:
   - moves a target failing a required gate CHANGES_REQUESTED → IN_PROGRESS (rework, feedback in its notes) and creates the gate rerun task `<gate-task-id>.r<N>`;
   - moves a target passing every required gate APPROVED → DONE;
   - after MAX_REWORK_LOOPS=2 rework loops, moves a still-failing target to ESCALATED and emits `escalation.request`.
5. Missing yield (D-07): a child whose output ends with `SUBAGENT_WARNING_MISSING_YIELD`, or has no parseable task.result, is ingested as FAILED with `error: {code: "E-CONTRACT", message: "missing yield"}`, or as BLOCKED with `needs` when it stated a need. Never IN_REVIEW.
6. Refusals and blocks:
   - Ingest refuses a gate result with E-CONTRACT `review verdict mismatch:` or `gate script not run`: nothing transitions and the gate task stays leased (IN_PROGRESS). Re-dispatch that gate agent with the error text. Never transition around it.
   - A BLOCKED task: escalate to the human. Never self-release it.
7. FAILED tasks: nothing in-session retries them (only the headless runner does). For each task FAILED after ingest, read `attempt` from `swarm_status` (`max_attempts` defaults to 3):
   - `attempt` < `max_attempts`: `swarm_transition` FAILED → RETRY, re-lease it RETRY → CLAIMED → IN_PROGRESS, and re-dispatch it to its agent with the error in the payload;
   - otherwise: FAILED → ESCALATED, and report it as an escalation.
8. Loop: re-dispatch rework tasks (IN_PROGRESS after reconcile, already leased) to their agent with the feedback from notes. Repeat from step 1 until every task is DONE, ESCALATED, CANCELLED or BLOCKED awaiting a human, then yield the swarm.status summary listing every ESCALATED, FAILED and BLOCKED task (with its escalation or need).

## Parent contract
The session that loaded this skill (the human's top-level) **must never** do domain work. All implementation goes through A01 waves.

## Depth cap & plan mode
- If you have no `task` tool and are not in plan mode, you may only `yield {state: "BLOCKED", needs: "depth"}`.
- In plan mode: yield a plan; perform zero Task Store writes.

## Negatives you must honour
- Specialists never receive the `task` tool.
- A01 running inside a workpool child yields BLOCKED depth.
- Plan mode never mutates the Task Store.
