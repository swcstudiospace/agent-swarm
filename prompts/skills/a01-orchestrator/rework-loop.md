---
name: a01-orchestrator--rework-loop
description: Use when a gate fails or a task enters CHANGES_REQUESTED; decide rework or escalation from the loop count, send findings back, third failure escalates.
owner: a01-orchestrator
tools: [shared_open_findings, shared_attempt_history, orch_rework_bound, orch_reconcile, shared_handover_bundle, shared_handover_validate, shared_handover_write]
---

# Rework loop (A01 side)

## When to Use

- A required gate failed on an IN_REVIEW task. This is only the orchestrator's delta over `skill://swarm-rework-intake`, which the producer runs; it covers the decision and the routing.

## Procedure

1. Call `shared_open_findings` with `{task_id}`; use `blocking` and the finding ids.
2. Call `shared_attempt_history` with `{task_id}`; read `rework_loops` and `max_rework_loops`.
3. Call `orch_rework_bound` with `{rework_loops, blocking_findings: <blocking>}`. `PASS` means nothing blocks; `REWORK` means one more loop; `ESCALATED` means the cap (2) is spent.
4. Apply it with `orch_reconcile` `{correlation_id}`, not by hand (an ingest already runs the same reconcile): a failing gate moves the task CHANGES_REQUESTED then IN_PROGRESS (`rework #n` in `aggregated`) and creates one rerun task per gate lineage `<base>.r<N>`; at the cap it moves the task to ESCALATED and emits `escalation.request`. A hand `--transition CHANGES_REQUESTED` would skip the gate reruns.
5. Call `shared_handover_bundle` with `{task_id}` and put the merged findings (id, gate, severity, location) from step 1 into the handover to the producer.
6. On `ESCALATED` from `orch_rework_bound`: arbitrate if two gates contradict each other (`skill://a01-orchestrator--conflict-arbitrate`), otherwise run `skill://a01-orchestrator--escalate-human`.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` to the producer.

## Outputs

- Decision (`PASS`, `REWORK`, `ESCALATED`), `loops_remaining`, rerun gate task ids, finding ids sent.

## Failure Handling

- Never exceed 2 loops and never reset `rework_loops`.
- A finding expired from `shared_open_findings` is not rework work.
- `DEPENDENCY_UNAVAILABLE`: retry twice, then hold the task unchanged.

## Handover

- `to_agent` the producing agent id (for example `A05`). Fill `goal` (clear blocking findings), `blockers` (finding ids), `constraints` (loops remaining, last loop warning), `remaining`.
