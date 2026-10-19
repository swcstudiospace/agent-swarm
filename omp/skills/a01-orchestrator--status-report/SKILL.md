---
name: "a01-orchestrator--status-report"
description: "Use when a run ends or an operator asks for status; reconcile the correlation, check gate coverage per task, emit the table and swarm.status JSON."
disable-model-invocation: true
---

# Status report

## When to Use

- End of a run, a stalled plan, or an operator request. Per-task approval decisions belong to `skill://a01-orchestrator--gate-approval`.

## Procedure

1. Call `orch_reconcile` with `{correlation_id}`. It is not report-only: it applies the gate and rework rules to every IN_REVIEW task of the correlation (rework, DONE, escalation) and lists each outcome in `aggregated`. Skip it when the operator asked for a read-only look.
2. Run `python3 scripts/orch_status.py --repo <repo> --correlation-id <id> --json` (omp: `swarm_status`); keep `tasks`, `counts`, `escalated`, `complete`. Trust this output for state, never an `orch_*` SUCCESS alone.
3. For each task row with a non-empty `gates_required`, call `orch_gate_coverage` with `{rules: [{risk: "row", gates: <gates_required>}], risk: "row", results: <the row's verdicts as [{gate, verdict}]>}`. Tasks with no required gates are reported as "no gates". A gate in `missing_gates` is missing whatever its row says.
4. Only when your assignment carries a Task Store task assigned to A01, call `shared_status_report` with `{task_id, kind: progress|blocked|done, step: "status report", percent, note: counts}`; `done` only when `complete` is true. For any other task id it returns UNAUTHORIZED: then skip it.
5. Emit the markdown table (task, agent, state, gates) and exactly one json block: `{type: "swarm.status", correlation_id, state: IN_REVIEW|BLOCKED|FAILED, pattern, counts, escalations[], arbitrations[], next_actions[]}` (prompts/A01 output_format). Under omp the final `yield` carries this block inside `summary_md` with `task_id` and `state`.
6. Cite for every fact the `scripts/orch_status.py` output or tool call that produced it; uncited claims go under UNVERIFIED.
7. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- Table, one `swarm.status` block, per-task coverage (`covered`, `missing`, `failed`).

## Failure Handling

- A tool failing: say which and show the data without it; never claim a state change from an `orch_*` SUCCESS alone.
- `state` is never SUCCESS or PARTIAL_SUCCESS.

## Handover

- `to_agent` the operator-facing owner (`A01` when unknown, as an agent id). Fill `completed` (done task ids), `remaining` (open tasks), `blockers` (escalated), `verification` (`verified` only when every row was confirmed in `orch_status.py`).

<!-- generated from prompts/skills/a01-orchestrator/status-report.md; edit the source and run scripts/build_agents.py -->
