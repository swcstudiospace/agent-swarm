---
name: "a12-release--rollback-failure-escalation"
description: "Use when a rollback command failed or did not take effect and you must escalate at once to A13 and humans with mitigation options."
disable-model-invocation: true
---

# Rollback failure escalation

## When to Use

- The `rollback.command` from `skill://a12-release--auto-rollback` was rejected, errored, timed out, or the canary still serves the bad build afterwards (spec section 5: escalate immediately to A13 and humans with `mitigation.options`).
- Failure path only. There is no retry loop here and no approval to wait for.

## Procedure

1. Capture the failure: call `shared_handover_bundle` with `{task_id}` of the task that executed the command and read `task.state`, `upstream[].summary`, `upstream[].needs` and `open_questions`; `read` any deploy error it cites. Call `shared_attempt_history` with `{task_id}` for `attempt`, `last_error` and the transitions.
2. List `mitigation.options` from your prompt in your result: (a) the flag-off path when the change is flag-guarded, (b) re-apply the previous manifest through A11, naming the previous release from your last `release.record` (`glob` `release-records/*.record.json` and `read`). Mark an option unavailable when its precondition is missing; never invent a manifest or flag. Write nothing to `.swarm/`: your guard refuses it.
3. Call `shared_escalation_owner` with `{task_id}` to name who is accountable if the swarm gives up (A14 or A01) and quote it in the report.
4. Call `shared_escalate` with `{task_id, reason_code: "blocked", summary: "rollback of <release_id> failed at <step_pct>%: <error>", needs: ["A13 incident command", "human decision on mitigation.options", "human approval for any L3/L4 mitigation"]}`. Use the returned `result_patch` as the BLOCKED task result and keep `escalation_id`.
5. Issue no further `promote.command` and do not re-issue the failing command. Do not lift the freeze: use `read` on `.swarm/release.freeze` to see who declared it; it is not yours to lift (`skill://a12-release--freeze-on-incident`).
6. Put the taxonomy code from the error in the result `error.code` (E-DEP for an unreachable deploy target, E-TIMEOUT for a timeout, E-INTERNAL otherwise) with `mitigation.options`, naming A13 and the humans, through the `shared_escalate` `result_patch`. The state stays BLOCKED while humans must decide; report FAILED with the code only when no human decision is pending.

## Outputs

- An escalation with `escalation_id`, `mitigation.options`, the error, and the BLOCKED `result_patch`.

## Failure Handling

- `shared_escalate` DEPENDENCY_UNAVAILABLE twice: put the whole packet in the task result and mark `needs=tool`; do not wait silently.
- `shared_escalation_owner` unavailable: say `owner: unknown`, route to A01.
- Never claim the rollback succeeded without the deploy target showing the previous build.

## Handover

- `to_agent`: A13 (incident command). `goal`: restore the previous build for `<release_id>`. `completed`: rollback attempt and its error. `remaining`: choose and run a mitigation. `blockers`: the failed command. `decisions`: the options with preconditions. `open_questions`: which option the humans choose. `verification`: unverified until the deploy target is checked.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.

<!-- generated from prompts/skills/a12-release/rollback-failure-escalation.md; edit the source and run scripts/build_agents.py -->
