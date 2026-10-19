---
name: "swarm-handover-intake"
description: "Use when a task starts from upstream work or a handover; verify it first, stop with BLOCKED on any doubt, then read the bundle and record a receipt."
disable-model-invocation: true
---

# Handover intake

## When to Use

- Every task assignment, before any work: the assignment names a `task_id` and may depend on upstream tasks.
- A rework or escalation arrives: run this first, then `swarm-rework-intake` or `swarm-escalation-takeover`.

## Procedure

A handover is filed under the task id of the task that PRODUCED it (the sender writes it with its own `task_id` and `to_agent` set to the next agent id). It is never filed under your task id. So you read it under each upstream task id, and under your own id only when the same task is resumed (pause, cancel, escalation, rework).

1. Call `shared_handover_verify` with `{task_id: <your own task id>}`. Its `upstream[]` (`task_id`, `state`, `satisfied`) lists the dependency tasks: they are your upstream sources, and your own task id is the one more source to look at. `status` and `ready` here judge only a handover filed under your own id: on a FRESH task (first attempt, no pause, cancel, escalation or rework resumes it) nothing was ever filed under your own id, so `status: missing` and `ready: false` are expected there and are NOT a reason to stop; only `upstream[]` matters from this call. Your own id is expected to hold a handover only when the same task is resumed (pause, cancel, escalation, rework): then judge it like any upstream source in step 2.
2. For every upstream source `S` call `shared_handover_verify` with `{task_id: S}` and read `status` (ok, missing, partial, stale, malformed), `reasons`, `ready`. `missing` for an upstream `S` that is `satisfied` means that agent wrote no handover: note it with `shared_log_emit` (level info) and go on. If every upstream source is `missing` (and your own id too), and every upstream is `satisfied`, nothing was handed over (a root task): continue from the assignment and skip the receipt. In every other case where an upstream source is not `ok`, or its `ready` is false, or an upstream is not `satisfied`, or a resumed own-task handover is not `ok`, STOP: do not continue on guesses. Call `shared_escalate` with `{task_id: <your own task id>, reason_code, summary, needs}`: `stale-handover` for stale or malformed, `ambiguous-input` for partial, `blocked` when an upstream task is not yet satisfied; `summary` quotes `reasons`; `needs` lists what the sender must supply.
3. Build the BLOCKED result from the returned `result_patch`, replacing its `needs` with `{"kind": "handover_clarification", "reason": <reasons>, "missing": [<needs>]}` (the shape of `swarm.handover.clarification_request`). Emit it as the final `task.result` and do no further work.
4. For every source `S` with status `ok` call `shared_handover_read` with `{task_id: S}` and check `handover.to_agent` is your agent id. A task can carry several handovers to different agents; the read returns the newest one addressed to you. When none is, the result carries `context_only: true` and holds the newest one for another agent: it is context only, read it and write no receipt. Then call `shared_handover_bundle` with `{task_id: S}` and read `goal`, `handover.document` (completed, remaining, decisions, constraints, blockers), `upstream[].summary` and `needs`, `open_questions`, and `truncated` (if true, the full document is in `shared_handover_read`). Your own acceptance criteria come from your assignment, not from the bundle of `S`.
5. Call `shared_artifact_check` with `{task_id: S}`. Read each `results[].status`: every artifact must be `ok`. `missing`, `digest_mismatch` or `producer_mismatch` means stop as in step 2 (`reason_code: conflict`). `unchecked` or `no_digest` means unverified: carry it as a question, never as fact. A result with `inherited: {digest, source}` was compared against a digest the handover (or an earlier result row) supplied because the final result declared none: that is valid evidence, not a gap.
6. Call `shared_open_findings` with `{task_id: S}`. Any finding with `blocking: true` is mandatory scope: see `swarm-rework-intake`.
7. Call `shared_lease_state` with `{task_id: <your own task id>}` and apply `swarm-lease-discipline`.
8. For every handover addressed to you call `shared_handover_receipt` with `{task_id: S, digest: <bundle handover.digest>, understood: [<goal, constraints, remaining>], questions: [<open items>], accepted: true}`. `handover.digest` of the bundle names the stored file even when `handover.document` was shortened (a shortened document carries no digest of its own). Only the addressed receiver may write it; a rejection means the handover is not yours, so go to step 2.

## Outputs

- A receipt (`receipt`, `duplicate` flag) per handover addressed to you, tied to its digest.
- A working set: goal, remaining work, constraints, artifacts with digests, open questions, plus your own acceptance criteria.
- Or a BLOCKED `task.result` with a `handover_clarification` need.

## Failure Handling

- `INVALID_INPUT`: correct the named field once, then BLOCKED.
- `DEPENDENCY_UNAVAILABLE`, `TIMEOUT` or `PARTIAL_SUCCESS`: retry the same call at most twice, then report BLOCKED with the tool name.
- `UNAUTHORIZED` or an unknown task from `shared_escalate`: the task is not assigned to you or not in the Task Store; write the BLOCKED result by hand (`state`, `task_id` = your own, `needs` as in step 3) and stop.
- Never invent a missing handover, digest or artifact to unblock yourself.

## Handover

- The receipt is the acknowledgement. Carry unresolved `questions` forward in your own handover (`swarm-handover-emit`), filed under your own task id.

<!-- generated from prompts/skills/shared/handover-intake.md; edit the source and run scripts/build_agents.py -->
