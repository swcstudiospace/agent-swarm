---
name: a12-release--plan-canary
description: Use when a release.plan task is assigned and you must build the canary plan, guardrails and approval class for one release at its risk class.
owner: a12-release
tools: [rel_canary_steps, rel_gate_order_check, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Plan a canary release

## When to Use

- `task.assign` has capability `release.plan` (or the planning half of `release.promote`) with a `release_id` and `risk_class`.
- You need the plan, not the gate judgement. Judging gates alone is `skill://a12-release--gate-conjunction`; a high risk plan stops at `skill://a12-release--high-risk-approval`.

## Procedure

1. Read the signed `task.assign` in your prompt and echo `task_id`, `correlation_id`, `capability`, `release_id`, `risk_class`. When any is missing or `inputs[]` is empty, call `shared_escalate` with `{task_id, reason_code: "ambiguous-input", summary, needs: ["input"]}` and stop with the BLOCKED patch it returns.
2. Call `rel_canary_steps` with `{release_id, risk: risk_class}`. Record `steps_pct` (a comma string such as `5,25,50,100`) and `approval`. When `approval` is `L4:human-four-eyes`, stop this path and follow `skill://a12-release--high-risk-approval`.
3. Run the release gate: call `swarm_gate` with `{gate: "release", task_id: $TASK, correlation_id: $CORR}`. It is an omp tool, not a registry tool; your guard blocks `scripts/rel_plan.py` and `scripts/ts/rel_plan.ts` in `bash`, and outside omp the same run is `python3 scripts/rel_plan.py --task-id $TASK --correlation-id $CORR --json`. Use `dry_run: true` only when the task says it is a runner dry-run. The reply is one summary line (prefix `FAIL:` on a failing verdict, `FROZEN` when a freeze is active). `swarm_gate` cannot pass `--release-id`, so the plan file is `.swarm/releases/REL-<id>.plan.json`, `<id>` being your task id or the first target of your gate task: use `glob` to find the newest and `read` it for `guardrails`, `gates_required`, `rollback_triggers`, `gate_verdict` and `artifacts`. `read` `.swarm/verdicts/<task_id>.release.json` for `payload.findings` and `payload.runs`. In an agent session the runner records the verdict rows after your session.
4. Cross-check the plan file `steps_pct` against step 2 and `guardrails` against the `GUARDRAILS` dict in `scripts/rel_plan.py` (low 0.01 / 500 ms / 6.0 / 5 min, medium 0.005 / 320 / 2.0 / 15, high 0.002 / 250 / 1.0 / 30; `read` it). A difference is a finding of kind `gate`, severity `major`; never edit the plan by hand (your guard also refuses writes into `.swarm/`).
5. Call `rel_gate_order_check` with `steps` = the gate sequence you will print in the plan (`gates_required`, then `release`, then `deploy`) and `required_order` = `GATES_BY_RISK[risk]` from `swarm/taskstore.py` followed by `release` unless the list already ends with it (low: review, release; medium: review, quality, release; high: review, quality, security, release). A non-empty `missing` or `out_of_order` is a `gate` finding.
6. Call `shared_artifact_digest` with `{path: ".swarm/releases/REL-<id>.plan.json"}` for the handover.
7. Emit the one fenced `json` block of `output_format` from the `rel_canary_steps` output and the plan file; `gate_verdict` is the plan's `gate_verdict`, `frozen` true when the summary says `FROZEN`. Use the `release_id` the assignment names; when the plan file name differs, say so in `decisions`.

## Outputs

- `.swarm/releases/REL-<id>.plan.json`, the `release.plan` json block, and the release `gate.verdict` file the gate wrote.

## Failure Handling

- `rel_canary_steps` INVALID_INPUT: fix `risk` to low, medium or high once; otherwise BLOCKED needs=input.
- `rel_canary_steps` or `swarm_gate` failing after two retries: FAILED with E-DEP. Never write a plan or verdict by hand.
- `frozen` true: the verdict is `fail` with a `blocker` freeze finding; still report the plan.

## Handover

- `to_agent`: A01. `goal`: gate and queue release `<release_id>`. `completed`: plan written, steps and approval class. `remaining`: gate conjunction and promotion. `decisions`: risk class and approval class with rationale. `artifacts`: the plan file with its digest, producer A12. `verification`: verified only when steps 2 to 5 all ran.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.
