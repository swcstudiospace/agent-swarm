---
name: swarm-orchestrate
description: "Drive AgentSwarm SDLC waves in-session via A01: status → tasks[] (explicit agent per task) → ingest → gates → DONE or ESCALATED. Parent never implements domain work."
disable-model-invocation: false
---

# swarm-orchestrate (in-session A01 driver)

## When to Use
- You are a01-orchestrator and a wave is ready, or the user explicitly asked for the full SDLC through the swarm.
- You were given a plan whose tasks carry `agent` fields.

Never use from a specialist session.

## Prerequisites
- Tools: swarm_status, swarm_ingest, task (and the normal read tools). You do **not** have the `swarm_gate` tool — you dispatch gate *agents* (a09 etc.) via `task`.
- A plan/correlation from swarm_plan or /swarm.
- Absolute SWARM_DIR for the target.

## Procedure

1. `swarm_status` (pass correlation if known) → obtain ready waves.
2. For a ready wave:
   - Emit **one** `task` call.
   - The `tasks` array contains one entry per plan task in the wave.
   - Every entry **must** contain the `agent` field (e.g. "a05-backend").
   - Gate agents (a08/a09/a10/a12) also receive `schemaMode: "strict"`.
3. After the children complete, `swarm_ingest` every `task.result`.
4. For gates: dispatch the corresponding gate *agent* (e.g. a09-reviewer) via another `task` call, passing the findings for that target. The gate agent's session will call its `swarm_gate` tool.
5. On CHANGES_REQUESTED: create rework tasks for the failed items and repeat the wave.
6. After two full rework loops on a task that still fails a required gate → ESCALATED + record `escalation.request`.
7. When the wave's gates are all green, move to the next wave or overall DONE.

## Parent contract
The session that loaded this skill (the human's top-level) **must never** do domain work. All implementation goes through A01 waves.

## Depth cap & plan mode
- If you have no `task` tool and are not in plan mode, you may only `yield {state: "BLOCKED", needs: "depth"}`.
- In plan mode: yield a plan; perform zero Task Store writes.

## Negatives you must honour
- Specialists never receive the `task` tool.
- A01 running inside a workpool child yields BLOCKED depth.
- Plan mode never mutates the Task Store.

## Post-run evidence
- `orch_status --history <corr>` shows the full transition chain and at least one signed gate verdict.
- Injected failure produces CHANGES_REQUESTED → rework → ESCALATED + escalation.request event.
