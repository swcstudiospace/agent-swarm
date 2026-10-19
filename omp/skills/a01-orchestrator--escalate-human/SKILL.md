---
name: "a01-orchestrator--escalate-human"
description: "Use when a gate failed a third time, a budget breached twice, a poison task repeats, or L3 or L4 action is needed; record the escalation with evidence."
disable-model-invocation: true
---

# Escalate to a human

## When to Use

- Third gate failure, second budget breach, a poison task (N validation failures), or any L3 or L4 action: killing a release, accepting security risk, approving a high-risk release (prompts/A01 autonomy). A01 decides conflicts itself: `skill://a01-orchestrator--conflict-arbitrate`.

## Procedure

1. Call `shared_attempt_history` with `{task_id: <child>}`; copy `attempt`, `rework_loops`, `last_error`, `transitions` into the evidence list.
2. Call `shared_handover_bundle` with `{task_id: <child>}` for the diagnostic bundle (goal, verdicts, lease, attempts).
3. Call `shared_escalation_owner` with `{task_id: <child>}`; `owner` is A14 when a direct upstream task is an A14 work order, otherwise A01.
4. Move the child to ESCALATED with `swarm_transition` or `python3 scripts/orch_status.py --repo <repo> --transition <child_id> ESCALATED --reason "<code>"`. Legal from FAILED, BLOCKED, RETRY and CHANGES_REQUESTED; from IN_PROGRESS or IN_REVIEW move to FAILED first. A CHANGES_REQUESTED move past the rework cap already lands in ESCALATED, and the reconcile step has emitted `escalation.request`.
5. Record the escalation: `shared_escalate` `{task_id, reason_code, summary, needs}` works only for a task assigned to you, so use it when your assignment carries an A01 task id. Otherwise call `shared_log_emit` `{level: error, message: "escalation", fields: {reason_code, child_task}, task_id: <child>}` and finish BLOCKED with `needs` in your final result. Reason codes: `budget` for a budget breach, `policy` for L3 or L4 (`needs: ["human-approval: <capability>"]`), `conflict` for unresolved arbitration, `blocked` for the third gate failure, `other` for a poison task. Name the child in `summary`.
6. Call `orch_dag_blocked` with the plan tasks; each id in `blocked` with `dead_dependency` is stranded: list it, and do not cancel human-approved work.
7. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- `escalation_id` and `result_patch` when `shared_escalate` ran, `owner`, options for the human, stranded dependents.
- swarm.status escalation entry: `{task_id, reason_code (E-INPUT, E-TIMEOUT, E-DEP, E-CAPACITY, E-CONTRACT or E-INTERNAL), evidence, options}`.

## Failure Handling

- Do not retry a task that qualifies for escalation.
- `shared_escalate` UNAUTHORIZED (the task is not assigned to A01): do not retry on the child; use `shared_log_emit` and the BLOCKED result.

## Handover

- `to_agent` the `owner` from step 3 (an agent id, A01 or A14). Fill `goal` (decision needed), `blockers`, `decisions` (what was tried), `open_questions` (human options).

<!-- generated from prompts/skills/a01-orchestrator/escalate-human.md; edit the source and run scripts/build_agents.py -->
