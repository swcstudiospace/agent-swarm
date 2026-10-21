---
name: "swarm-cooperative-cancel"
description: "Use when a run may be cancelled or hit its deadline; poll shared_cancel_check at step boundaries, stop cleanly, hand over what is done and report state."
disable-model-invocation: true
---

# Cooperative cancel

## When to Use

- Throughout every task: at start, at each step boundary, before any command expected to run longer than 30 seconds, and before the final write.

## Procedure

1. Call `shared_cancel_check` with `{task_id}` at each boundary. Read `cancel_requested`, `grace_remaining_s`, `has_deadline`, `deadline_remaining_s` and `deadline_exceeded`.
2. If `shared_cancel_check` reports `cancel_requested` false and `deadline_remaining_s` leaves room (or `has_deadline` is false), continue. If a deadline is within one step of expiring, wrap up: finish only what a consistent state needs.
3. If `cancel_requested` is true or `deadline_exceeded` is true, stop starting new work immediately: no new subagent (only A01 has one), no new long command, no new file group. If you edit files, finish or revert the single edit in progress so no file is half-written. Do all of it within `grace_remaining_s`.
4. Call `shared_artifact_digest` with `{path}` for each file that already exists and matters.
5. Call `shared_handover_write` with `{task_id, to_agent: "A01", goal, completed, remaining, artifacts, constraints, verification: {status: "partial", confidence, checks: []}}`. `remaining` lists exactly what was not done, so a later run resumes without redoing it.
6. Call `shared_status_report` with `{task_id, kind: blocked, step: "cancelled: <reason>", note}`.
7. Call `shared_escalate` with `{task_id, reason_code: other, summary: "cancel requested: <reason>"}` (use `budget` when the deadline was exceeded) and emit its `result_patch` as the final BLOCKED `task.result`, with the handover id in `summary_md`. Report no success you did not reach.
8. After `shared_escalate` returned and the result is written, stop; do not reopen work, retry or spawn a `task` subagent.

## Outputs

- A consistent working tree, a stored partial handover and a BLOCKED `task.result` naming the cancel reason.

## Failure Handling

- The grace is nearly gone (`grace_remaining_s` under a few seconds): skip digests, write the handover with `remaining` only, then the result.
- `shared_handover_write` fails: still emit the BLOCKED result and say the handover is missing.
- `DEPENDENCY_UNAVAILABLE` on `shared_cancel_check`: treat as not cancelled, retry at the next boundary, and do not loop on it.
- `UNAUTHORIZED` or an unknown task from `shared_status_report` or `shared_escalate`: the task is not assigned to you; write the BLOCKED result by hand and stop.

## Handover

- The partial handover is the product of a cancel: `completed`, `remaining` and `verification: partial` must be honest.

<!-- generated from prompts/skills/shared/cooperative-cancel.md; edit the source and run scripts/build_agents.py -->
