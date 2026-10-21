---
name: a01-orchestrator--budget-enforce
description: Use when a task reaches 80% of its budget or a breach is reported; warn, hard-stop at 100%, requeue once with an annotated budget, escalate the second breach.
owner: a01-orchestrator
tools: [orch_budget_class, orch_budget_guard, shared_cancel_check, shared_scratchpad_get, shared_scratchpad_put, shared_handover_validate, shared_handover_write]
---

# Budget enforce

## When to Use

- A task's usage reaches 80 % of any budget dimension, or a runner reports E-TIMEOUT. Rule: hard-stop at 100 %, checkpoint, requeue once with an annotated budget, second breach ESCALATED (agents/A01, rule 5).

## Procedure

1. Read the task's budget (`max_tokens`, `max_wall_s`, `max_cost_usd`) from its entry in `.swarm/plans/<correlation_id>.json` with `read`. Take `wall_s` from `live.runs[].elapsed_s` of `python3 scripts/swarm_live.py --repo <repo> --json` (or `duration_s` of `swarm.run.finished` in `.swarm/events.jsonl`); take `tokens` and `cost_usd` only from the result's `metrics` when the agent reported them. A dimension nobody measured is passed as 0 and named as unmeasured in the note; never invent numbers.
2. Call `orch_budget_class` with `{risk: <task risk_class>}` and compare its three integers with the plan entry; a mismatch goes into the note, it never overrides the plan.
3. Call `shared_scratchpad_get` with `{correlation_id, key: "budget-<task id, lowercase>"}` for earlier breaches (`value.breaches`; 0 when `found` is false).
4. Call `orch_budget_guard` with `{budget: {max_tokens, max_wall_s, max_cost_usd}, usage: {tokens, wall_s, cost_usd}, prior_breaches}`. Each budget value must be above 0.
5. `WARN` from `orch_budget_guard`: put the `ratios` in the status note and keep running; no state change.
6. `HARD_STOP`: call `shared_cancel_check` with `{task_id}`. `cancel_requested` or `deadline_exceeded` true means the runner is already stopping the session; no A01 tool interrupts a live session, so wait until it has ended. Then call `shared_scratchpad_put` with `{correlation_id, key: "budget-<task id, lowercase>", value: {breaches: breaches_after, new_budget, checkpoint}}`. Move the task with `swarm_transition` (or `python3 scripts/orch_status.py --repo <repo> --transition <task_id> FAILED --reason "E-TIMEOUT budget breach"`, then `RETRY`) and redispatch once through `skill://a01-orchestrator--dispatch-ready`.
7. `ESCALATED` from `orch_budget_guard`: no retry; run `skill://a01-orchestrator--escalate-human` with `reason_code: budget`.
8. If the annotated budgets push the plan total (sum of the plan entries) over its budget by more than 10 %, stop BLOCKED needs=human-approval (L3). Then call `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `decision`, `ratios`, `breached`, `breaches_after`, the annotated budget.

## Failure Handling

- `INVALID_INPUT` field `usage` or `budget`: fix the named value once; otherwise BLOCKED needs=input.
- Never raise a budget silently and never retry a second breach.
- Convention: the Task Store budget column is not editable by any A01 tool, so the annotated budget lives in the scratchpad value and the handover `constraints`.

## Handover

- `to_agent` the agent id that resumes the task (for example `A05`). Fill `completed` (checkpointed work), `remaining` (unfinished work), `constraints` (annotated budget, "last requeue"), `decisions` (breach and why).
