---
name: "a06-frontend--rework-intake"
description: "Use when review comments, a11y violations, security findings or a gate verdict return A06 work; route findings to A06 skills and stop at the loop cap."
disable-model-invocation: true
---

## When to Use
- A gate or reviewer sent frontend work back. Run `skill://swarm-handover-intake` and then `skill://swarm-rework-intake` for the generic loop (fix by finding id, show evidence, `shared_dod_check`, progress reports). This file states only the A06 delta: finding sources when the list is empty, routing to A06 skills, the loop cap.

## Procedure
1. Call `shared_attempt_history` with `{task_id}`. `rework_loops` is the loop you are in and `max_rework_loops` is 2: the controller itself moves the task to ESCALATED on a third CHANGES_REQUESTED (prompt failure mode: "Third gate failure: do not retry; A01 escalates"). At `rework_loops` 2 this is the last loop, so fix everything blocking in it. A `task_state` of ESCALATED means stop: report BLOCKED and fix nothing.
2. Call `shared_open_findings` with `{task_id}`. Verdicts from before the rework are dropped by the store itself (prompt failure mode "Stale verdict"), but the call also returns an empty list for the verdicts that caused the rework (known registry defect). An empty list is therefore not "no findings": call `shared_handover_read` with `{task_id}` and take the findings from the gate agent's `handover.remaining` and `handover.blockers`, and from the `review.verdict`, `a11y.violations` or `security.findings` entries of `inputs[]`.
3. Route every finding by its source and fix it with `edit` under the matching skill: a11y findings to `a06-frontend--a11y-selfgate`; colour or spacing literals to `a06-frontend--token-binding`; missing or extra states to `a06-frontend--state-parity`; bundle, Lighthouse or CWV findings to `a06-frontend--perf-budget`; XSS, CSP, SRI or storage to `a06-frontend--client-security`; API shape mismatches to `a06-frontend--api-contract-bind`; intermittent visual failures to `a06-frontend--visual-flake-triage`; a spec question to `a06-frontend--design-deviation`. A finding about backend code, contracts or designs is not yours: record it in `blockers` for A01 instead of editing.
4. On a stop, or on a finding that cannot be fixed inside A06's ownership, call `shared_escalate` with `{task_id, reason_code: "blocked", summary, needs: ["<finding id>: <what is missing>"]}` and use its `result_patch` as the BLOCKED result.
5. Otherwise rebuild the closing handover with the fix list and call `shared_handover_write` for the gate agent that issued the verdict (review gate A09, quality gate A08, security gate A10).

## Outputs
- A fix plan (finding id, source, A06 skill used, outcome) and the resubmitted patch or the BLOCKED result.

## Failure Handling
- `shared_open_findings` returns `task_known` false: BLOCKED `needs=input` with the task id; do not guess the findings.
- A fix needs the E2E suite, an API shape or a design change: those are not A06's; escalate rather than work around.

## Handover
Fields filled: `completed` (finding ids fixed), `decisions` (declined findings and why), `blockers` (unfixable findings), `verification.checks` (reruns of the routed skills). Written with `shared_handover_write` to the issuing gate agent (A09, A08 or A10).

<!-- generated from prompts/skills/a06-frontend/rework-intake.md; edit the source and run scripts/build_agents.py -->
