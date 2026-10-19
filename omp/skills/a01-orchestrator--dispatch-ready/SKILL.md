---
name: "a01-orchestrator--dispatch-ready"
description: "Use when a scheduling round starts with PLANNED or RETRY tasks; start only ready tasks within each agent ceiling and the global cap, leasing before spawning."
disable-model-invocation: true
---

# Dispatch ready

## When to Use

- A round starts and the Task Store is healthy. Store or bus down: `skill://a01-orchestrator--static-plan-mode`.

## Procedure

1. Run `python3 scripts/orch_status.py --repo <repo> --correlation-id <id> --json` (omp: `swarm_status`) and call `orch_dag_blocked` with `{tasks: [{id: task_id, depends_on, state}]}` from its rows. Only ids in `ready` continue; list `blocked` (cycle, missing or dead dependency) for `skill://a01-orchestrator--escalate-human`.
2. Call `orch_dispatch_batch` with `{ready: [{id, agent_id, priority?}], running: {<agent id>: CLAIMED + IN_PROGRESS count}, ceilings: {<agent id>: max_parallel from agents.json}, global_cap}`. Use the same agent ids (`A05`) as keys everywhere; every `agent_id` needs a ceiling. Start only `start`; `deferred` waits for the next round with its reason (`agent_ceiling` or `global_cap`).
3. For each started id lease it: `swarm_transition` to CLAIMED, or `python3 scripts/orch_status.py --repo <repo> --transition <task_id> CLAIMED --reason "lease granted"`. Call `shared_lease_state` with `{task_id}`: `lease.expired` must be false. Then transition to IN_PROGRESS the same way.
4. Obtain the assignment with `swarm_assignment` `{task_id, correlation_id}` and require `assignment_verified: true`. Under omp the shell refuses `scripts/orch_assign.py`, so the script is only for a non-omp runtime: `python3 scripts/orch_assign.py --task-id <task_id> --correlation-id <correlation_id> --json --root <repo>`. Never call the `orch_assign` or `orch_run` registry tools: both return DEPENDENCY_UNAVAILABLE by design.
5. Spawn every started lane in one message with the `task` tool: `agent` set to the returned `agent_slug`, `task` set to the returned prompt unchanged, `schemaMode: "strict"` for a08-qa, a09-reviewer, a10-security and a12-release. The same slug may repeat up to its `max_parallel`. Run `python3 scripts/swarm_run.py --repo <repo> --max-parallel <n> --once` only when the operator's assignment tells you to. Confirm each task is IN_PROGRESS in `orch_status.py` before reporting it started.
6. Call `shared_handover_validate`, then `shared_handover_write`, for each started task (`skill://swarm-handover-emit`).

## Outputs

- Started ids, deferred ids with reason, lease holder and ttl per task.

## Failure Handling

- `shared_lease_state` expired or foreign holder: do not spawn; re-claim once, then escalate.
- Assignment refused or unverified (E-CONTRACT: task has no manifest agent): stop, no retry loop.
- Unsigned or unleased task: never spawn.

## Handover

- `to_agent` the task's agent id (for example `A05`). Fill `goal` (task goal), `constraints` (budget, lease seconds, claims), `remaining` (acceptance), `artifacts` (upstream results with digests).

<!-- generated from prompts/skills/a01-orchestrator/dispatch-ready.md; edit the source and run scripts/build_agents.py -->
