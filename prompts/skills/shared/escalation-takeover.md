---
name: swarm-escalation-takeover
description: Use when a task was escalated to you after retries or rework failed; read what was tried, decide re-plan, human review or cancel, and record it.
owner: shared
tools: [shared_escalation_owner, shared_attempt_history, shared_handover_bundle, shared_open_findings, shared_lease_state, shared_open_questions, shared_handover_write]
---

# Escalation takeover

## When to Use

- The swarm gave up on a task (retry cap or rework cap) and you are accountable: A14 when the failed task is downstream of an A14 work order, otherwise A01.
- The escalated task stays assigned to the agent that failed it. The status, escalate and receipt tools only act on a task assigned to the caller, so here you read with the tools below and report in your own `task.result`.

## Procedure

1. Call `shared_escalation_owner` with `{task_id}`. If `owner` is not your agent id, you are not accountable: stop with a BLOCKED result whose `needs` names `owner`.
2. Read the context the runner gave you: the escalation packet in your assignment, and `shared_handover_bundle` with `{task_id}` for `goal`, `acceptance`, `handover.document`, `upstream` and `verdicts`. A missing handover is normal here (the runner writes a packet, not a HandoverV1); apply `swarm-provenance-trust` to what you rely on.
3. Call `shared_attempt_history` with `{task_id}`. Read `attempt`, `max_attempts`, `rework_loops`, `last_error` and `transitions` to see what was already tried and why it failed.
4. Call `shared_open_findings` with `{task_id}`. Separate causes: bad input, bad plan, a real defect, a conflicting finding, missing authority.
5. Call `shared_lease_state` with `{task_id}`. An escalated node is normally `released` with action `handover`; never assume the node is yours to edit.
6. Call `shared_open_questions` with `{task_id}` and decide which questions only a human or the planner can answer.
7. Choose one outcome, one only, and justify it from the `shared_attempt_history` and `shared_open_findings` evidence: re-plan (smaller or reordered tasks), human review (a decision or authority is missing), or cancel (the goal is no longer worth it). Never rerun the failing step unchanged; a second identical failure proves nothing new.
8. Call `shared_handover_write` with `{task_id, to_agent, goal, decisions, remaining, open_questions}`. `decisions` carries the chosen outcome and its rationale; `to_agent` is the agent that executes it and never yourself: for a re-plan or cancel, A01 when you are A14, or the first agent of the re-planned work when you are A01.
9. If the outcome needs a person, finish with a BLOCKED `task.result` for your own task: `needs` is the exact decision requested, `summary_md` names the handover id.

## Outputs

- A takeover decision with rationale in a stored handover.
- A BLOCKED result when a human decision is required.

## Failure Handling

- Missing history (`task_known` false): BLOCKED result naming the unknown task.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry at most twice, then BLOCKED.

## Handover

- Hand the decision, the attempts already made and the unresolved questions to the executing agent (see step 8).
