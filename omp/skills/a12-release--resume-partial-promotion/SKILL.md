---
name: "a12-release--resume-partial-promotion"
description: "Use when a canary promotion was interrupted and you must resume from the last confirmed step or roll back cleanly, with no step repeated blind."
disable-model-invocation: true
---

# Resume a partial promotion

## When to Use

- A session ended, a lease expired or a step failed half way: some `promote.command` steps were issued and not all confirmed (spec section 5: steps are transactional, resume from the last confirmed step or roll back cleanly).
- Fresh telemetry for a live step is `skill://a12-release--canary-step-decision`; this skill handles the interruption itself.

## Procedure

1. Call `shared_attempt_history` with `{task_id}` and read `attempt`, `last_error` and the `transitions` so the retry starts from what already failed.
2. Use `read` on `.swarm/releases/<release_id>.plan.json` for `steps_pct`. Find the `promote.command` step tasks A11 ran: their ids are in the task inputs or in your earlier handover (`shared_handover_bundle` with `{task_id}` on your own task shows its `handover.document` and `artifacts`). For each step task call `shared_handover_bundle` with that task id: a step is `confirmed` only when `task.state` is IN_REVIEW, APPROVED or DONE and its result names that `step_pct`; a step issued but not confirmed is `in_flight`. List `confirmed` in plan order.
3. Call `rel_guardrail_breaches` with the newest sample for the highest confirmed step (`observed` and the three `limits` keys from the plan, not `soak_min`). `breached` true makes `breach: true` below; a non-empty `missing_metrics` means stop here and use `skill://a12-release--monitoring-degraded-halt`.
4. Call `rel_resume_point` with `{steps_pct, confirmed, in_flight, breach}` (integers; omit `in_flight` when none). It returns `action` (`resume`, `rollback`, `complete`), `next_pct` and `reason`. A `confirmed` list that skips or reorders steps yields `rollback`; trust it.
5. Act on the `rel_resume_point` `action`: `resume` re-issues the promote for `next_pct` only after the gates were re-judged (`skill://a12-release--gate-conjunction`: gate summary not `FAIL:` and not `FROZEN`); report that `promote.command` as unsigned, as in `skill://a12-release--canary-step-decision` step 7. `rollback` continues with `skill://a12-release--auto-rollback`; `complete` continues with `skill://a12-release--release-record-seal`.
6. Record the action and `reason` in the task result and in the `decisions` of the `shared_handover_write` document so the next reader does not redo step 2.

## Outputs

- `action`, `next_pct`, the confirmed and in-flight steps, and the reason chain.

## Failure Handling

- `rel_resume_point` INVALID_INPUT on `steps_pct`: re-read the plan; a plan whose last step is not 100 or is not increasing is a plan defect, BLOCKED needs=input.
- No record of which steps were confirmed: do not assume. Choose rollback by passing a `confirmed` list you can prove, or BLOCKED needs=input.
- `shared_attempt_history` at the retry cap (`retries_left` 0): do not resume; hand over with the blocker.

## Handover

- `to_agent`: A01. `goal`: finish or reverse the interrupted promotion of `<release_id>`. `completed`: confirmed steps. `remaining`: the `next_pct` step or the rollback. `decisions`: the action with `reason`. `open_questions`: any step whose confirmation you could not prove. `verification`: verified only when step 2 found evidence for every confirmed step.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.

<!-- generated from prompts/skills/a12-release/resume-partial-promotion.md; edit the source and run scripts/build_agents.py -->
