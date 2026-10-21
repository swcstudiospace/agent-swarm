---
name: a01-orchestrator--agent-recovery
description: Use when a run is stalled or an agent's lease is dead; confirm the lease and attempts, requeue its in-flight task to RETRY and redispatch with the checkpoint.
owner: a01-orchestrator
tools: [shared_lease_state, shared_attempt_history, shared_cancel_check, shared_handover_bundle, shared_handover_validate, shared_handover_write]
---

# Agent recovery

## When to Use

- A run shows `stalled: true` (no activity for `SWARM_STALL_AFTER_S`, default 180 s) or three `SWARM_HEARTBEAT_S` beats (default 10 s) are missing, or a CLAIMED or IN_PROGRESS task's lease expired. Task Store itself down: `skill://a01-orchestrator--static-plan-mode`.

## Procedure

1. Run `python3 scripts/swarm_live.py --repo <repo> --correlation-id <id> --json` and take `live.runs[]` with `stalled` true or `state` failed or timed_out; then `python3 scripts/orch_status.py --repo <repo> --correlation-id <id> --json` for that agent's CLAIMED or IN_PROGRESS rows.
2. Call `shared_lease_state` with `{task_id}` for each. Requeue only when `lease.expired` is true, or `lease.has_lease` is false (substrate leases off) and step 1 shows the run stalled or gone.
3. Call `shared_attempt_history` with `{task_id}`. `retries_left` 0 sends the task to `skill://a01-orchestrator--escalate-human`; a claim beyond `max_attempts` fails with E-TIMEOUT.
4. Call `shared_cancel_check` with `{task_id}`. `cancel_requested` true means the runner is already stopping the session: do not requeue until it has left. A runner still working the task (notes.running) owns it; wait for its result.
5. Requeue with `swarm_transition` `{task_id, state: RETRY, reason: "3 missed heartbeats"}`, or `python3 scripts/orch_status.py --repo <repo> --transition <task_id> RETRY --reason "3 missed heartbeats"` (legal from CLAIMED, IN_PROGRESS and FAILED). `orch_status.py --json` must show RETRY.
6. Call `shared_handover_bundle` with `{task_id}`; its `handover` and `attempts` are the checkpoint. Redispatch through `skill://a01-orchestrator--dispatch-ready`; a RETRY row is `ready` again once its dependencies hold.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (`skill://swarm-handover-emit`).

## Outputs

- Requeued task ids, `retries_left`, replacement agent id, checkpoint handover id.

## Failure Handling

- Requeue is idempotent: a task already in RETRY is left alone.
- No handover in the bundle (`status` missing): the replacement restarts from the task goal, and the new handover says so.

## Handover

- `to_agent` the replacement as an agent id (for example `A05`, never the slug). Fill `completed` (checkpointed work), `remaining` (the rest), `blockers` (why the first run died, if known), `constraints` (attempts left).
