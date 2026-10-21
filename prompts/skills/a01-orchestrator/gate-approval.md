---
name: a01-orchestrator--gate-approval
description: Use when a task sits IN_REVIEW with gate verdicts; check each gate it requires passed or was waived, let reconcile approve, otherwise hold and name the gaps.
owner: a01-orchestrator
tools: [orch_gate_coverage, orch_reconcile, shared_dod_check, shared_open_findings, shared_handover_validate, shared_handover_write]
---

# Gate approval

## When to Use

- A task is IN_REVIEW and its gate tasks have reported. Gate failures that need producer changes: `skill://a01-orchestrator--rework-loop`.

## Procedure

1. Run `python3 scripts/orch_status.py --repo <repo> --history <task_id>` and read `verdicts` (gate to verdict row) and `missing_gate_reasons` (gate to `absent`, `unsigned`, `stale`, `expired` and so on). Run `orch_status.py --correlation-id <id> --json` for the row's `gates_required`; a task with none needs no gate and is reported as "no gates".
2. Call `orch_gate_coverage` with `{rules: [{risk: "row", gates: <gates_required>}], risk: "row", results: [{gate, verdict}] from step 1}`. A gate listed in `missing_gate_reasons` counts as missing even when its row says `pass`: the Task Store only counts verified, current verdicts. Only `pass` or a human `waive` covers a gate.
3. Call `shared_open_findings` with `{task_id}`; `blocking` must be 0.
4. If the task has acceptance criteria, call `shared_dod_check` with `{task_id, result: <the task.result>}`; `all_met` must be true, each `unknown` is named as unmet. A task with no acceptance rows returns INVALID_INPUT field `criteria`: say so and skip this step.
5. Approval is done by the Task Store, never by you: when `covered` is true, `blocking` is 0 and step 4 passed or was skipped, call `orch_reconcile` with `{correlation_id}`; its `aggregated` shows `<task>: DONE` once every required gate has an accepted verified verdict. Never run `swarm_transition` or `--transition` to APPROVED or DONE, and never write or waive a verdict.
6. Otherwise hold: do not call `orch_reconcile` for this task (an ingest of any other result still reconciles the whole correlation, so the gates alone decide), report `missing`, `failed` and `extra` from step 2 and the unmet criteria from step 4, and leave the task IN_REVIEW. Send the unmet criteria to the producer in the step 7 handover; a failed gate goes to `skill://a01-orchestrator--rework-loop`.
7. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- Approve or hold, with `passed`, `waived`, `failed`, `missing` lists, the findings count and the reconcile note.

## Failure Handling

- Most restrictive verdict wins; a `fail` after a `pass` for the same gate holds the task.
- A gate row the store marks unaccepted, or a gate stall note (`gate stall [...] → escalation.request` in `aggregated`), counts as missing: re-run the gate through `skill://a01-orchestrator--dispatch-ready` or escalate.
- `INVALID_INPUT` on `risk`: pass a risk that appears in `rules`.

## Handover

- `to_agent` the downstream consumer as an agent id (A12 for release work, else A01 for the next round). Fill `decisions` (approve or hold and why), `verification` (`verified` only when covered), `blockers` (missing gates), `remaining`.
