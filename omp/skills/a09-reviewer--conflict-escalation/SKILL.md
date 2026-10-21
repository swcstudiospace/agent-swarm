---
name: "a09-reviewer--conflict-escalation"
description: "Use when A08 or A10 reached a different verdict than yours, or a third gate failure or poison task occurs, and A01 must arbitrate."
disable-model-invocation: true
---

## When to Use
- Your verdict conflicts with A08's (gate `quality`) or A10's (gate `security`) on the same task.
- `shared_attempt_history` shows the rework cap reached or a third failure, or the task is poison: do not retry.
- Not for producing your own verdict (`a09-reviewer--verdict-assembly`).

## Procedure
1. Call `shared_handover_bundle` with the `task_id` of the gated target (a `gate_for` PR task: verdict rows are stored per target, so repeat per target). Its `verdicts[]` rows carry `gate`, `verdict`, `agent`, `signature`, `expired`, `findings`, `blocking`: find your own row (gate `review`) and the A08 and A10 rows.
2. Call `shared_open_findings` with the same target `task_id`. It lists only failing, unexpired verdicts, each finding with `gate`, `agent`, `id`, `severity`, `kind`, `location`.
3. From the `shared_handover_bundle` and `shared_open_findings` results, name the disagreement exactly: gate, agents, verdict values and the finding or rule each side cites. Do not pick a winner and never override A08 or A10 (prompt ESCALATION).
4. Call `shared_attempt_history` with the target `task_id` (rework loops belong to the PR task) and with your gate `task_id` for `attempt` and `last_error`. When `rework_loops` has reached `max_rework_loops`, or the task is on its third failure or is poison, stop: finish FAILED with the taxonomy error and your findings so far. Do not retry and do not escalate yourself; A01 escalates (prompt ESCALATION). Go to the handover step.
5. Otherwise it is a verdict conflict: call `shared_escalation_owner` with your gate `task_id` (`owner` is A14 for a maintenance work order, else A01). Call `shared_route_check` with `sender` A09, `recipient` that owner and `message_type` `gate.verdict`; the ACP allows it for A01 but not for A14. The check is informational: a handover is not ACP-gated, so the escalation goes to `owner` either way.
6. Call `shared_escalate` with `task_id`, `reason_code` "conflict", a `summary` (clipped at 500 characters) naming both verdicts and the rule, and `needs` ["<owner> arbitration"]. Finish BLOCKED with its `result_patch`.
7. Call `shared_artifact_digest` with `path` `.swarm/verdicts/<gate task_id>.review.json`, then `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs
- FAILED (third failure or poison) with the findings so far, or BLOCKED with an arbitration request: both verdicts, the disputed rule, attempts used, the `escalation_id`.

## Failure Handling
- A verdict missing from the bundle: say so and escalate with `reason_code` "other"; never assume it passed.
- `shared_escalate` DEPENDENCY_UNAVAILABLE: retry once, then return BLOCKED with the same summary in plain text.
- A row with `expired` true is stale: report it as stale, not as a conflict.
- `shared_escalate` is refused unless the task is assigned to you; use your own gate task id.

## Handover
- Fill `decisions` (your verdict and rule, `{decision, rationale}`), `blockers` (the unresolved conflict), `open_questions` (what A08 or A10 must show), `artifacts` (kind `gate.verdict`, uri the verdict file, digest from step 7, producer A09), `verification` (`partial`).
- Call `shared_handover_write` with `to_agent` the `owner` from step 5 (A01 unless a maintenance work order makes it A14). A FAILED third failure goes to A01.

<!-- generated from prompts/skills/a09-reviewer/conflict-escalation.md; edit the source and run scripts/build_agents.py -->
