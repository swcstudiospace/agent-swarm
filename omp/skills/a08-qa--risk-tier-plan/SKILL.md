---
name: "a08-qa--risk-tier-plan"
description: "Use when a test run is about to start and the tier selection for the task's risk class must be decided and recorded before anything executes."
disable-model-invocation: true
---

## When to Use

Before `a08-qa--gate-run` or any suite run. The plan fixes which tiers are required (low: unit; medium: unit and integration; high: unit, integration, e2e and perf) and what a missing tier means. It plans only; `gate-run` executes and issues the verdict.

## Procedure

1. Call shared_handover_bundle with `task_id`. Read `task.risk_class` and `acceptance`. A gate task covers its targets, so the effective class is the highest across the gate task and its `gate_for` targets; `scripts/qa_gate.py` applies that rule itself, and swarm_gate offers no way to override it. A release candidate that must run high therefore needs `risk_class` high on the gate task: if it is lower, put that request in `open_questions` for A01.
2. Call qa_runner_detect with `scan` true. `runner_count` 0 means every selected tier will report `skipped:infra` and the plan must say so.
3. Decide `degraded` from the qa_runner_detect result and the task's environment notes: true only when the environment is known to be degraded (the spec's smoke-only mode); otherwise false. It is a planning input only: `scripts/qa_gate.py` has no degraded mode and still selects tiers by class, so record a smoke-only decision as a constraint for A01.
4. Dry-plan with qa_tier_plan: `risk_class` = the chosen class, `runs` = what you expect each tier to report (for a pre-run plan, set tiers with no harness to `skipped:infra`, the rest to `pass`), `degraded`. The response gives `required`, `missing_required` and the `verdict` the gate would reach if the run looked like that. A non-empty `missing_required` is a plan risk to name, not a result.
5. Call shared_dod_check with `criteria` the task's `acceptance` lines and `result` whose `completed` and `checks` (`{name, status}`) lines state which tier covers each criterion, to confirm the plan addresses every criterion with evidence rather than keywords alone.
6. Record the plan: risk class and why, `required` tiers from qa_tier_plan, `degraded`, expected `missing_required`, and that the gate will run through swarm_gate (`skill://a08-qa--gate-run`), not through a shell command with `--risk-class` or `--tier` flags. Never call `gate_risk_class`; it is a Python function inside `swarm/verdicts.py`, not a registry tool.

## Outputs

- `test.plan`: risk class and why, tier list, `degraded` flag, expected `missing_required`, the swarm_gate parameters (`gate` "quality", gate task id).
- The runner detection numbers that justified it.

## Failure Handling

- `risk_class` empty in the bundle: take the class from the assignment text; if absent, BLOCKED with `needs=input`, never default silently to low.
- qa_tier_plan INVALID_INPUT names the bad field (an unknown tier key or status string); correct it once.
- shared_handover_bundle reports `task_known` false: plan from the assignment and mark the handover `verification` as `unverified`.

## Handover

Call shared_handover_write to the agent that will run the gate (usually yourself on the next task, else A01): `goal` "run the quality gate with this plan", `decisions` the class and tier choice with rationale, `constraints` the required tiers and any smoke-only or class-raise request, `remaining` the tiers still to run, `verification` status `partial`.

<!-- generated from prompts/skills/a08-qa/risk-tier-plan.md; edit the source and run scripts/build_agents.py -->
