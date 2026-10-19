---
name: "a02-requirements--provisional-spec"
description: "Use when both rounds are used and one went 48 h unanswered; publish a PROVISIONAL spec with assumption log, per-item confidence and risk of medium or more."
disable-model-invocation: true
---

# Provisional Spec

The fallback once the stakeholder is unreachable (spec section 5). Work cannot be DONE; it becomes PROVISIONAL and
ESCALATED. This skill asks nothing new: it records what was assumed and how sure A02 is.

## When to Use

- `req_clarification_window` reports `must_go_provisional` true (two rounds used and a round still unanswered after its window).
- Not while a round is still within its window, and not for contradictions (use `a02-requirements--conflict-resolution`).

## Procedure

1. Call `shared_handover_read` with `task_id` to get the rounds and the questions that were asked from `handover.decisions` and `handover.open_questions`.
2. Get the time with `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) and call `req_clarification_window` with `rounds`, that `now` and the defaults. Continue only when `must_go_provisional` is true; copy `open_overdue` and `p50_latency_h` (when present) into the record.
3. Call `shared_open_questions` with `task_id` for the unanswered list; each one becomes one assumption.
4. Use `write` to build the assumption log in `docs/requirements/assumptions.md` (convention path): id `AS-<n>`, the question, the assumption taken, the story or NFR ids that depend on it, and a confidence between 0 and 1 (house rule: 0.3 when nothing in the brief supports the choice, 0.6 when one document does). Call `shared_artifact_digest` with that `path`.
5. Use `edit` on the spec to mark every dependent story and NFR `PROVISIONAL` and to state `risk_class: medium` as the minimum, `high` when any assumption below 0.5 touches a compliance NFR (house rule). PROVISIONAL is a spec marker, not a Task Store state; A02 cannot write the store, so A01 applies the risk class when it re-plans.
6. Call `shared_scratchpad_put` with `correlation_id`, `key` `assumption-log` and `value` set to the log object (at most 8 KiB), `shared` true, so downstream agents read the same log.
7. Call `shared_escalate` with `task_id`, `reason_code` `ambiguous-input`, `summary` stating that two rounds were used and one went unanswered 48 h, and `needs` `["stakeholder-confirmation: <assumption ids>"]`. Finish BLOCKED with the returned `result_patch`, so A01 can move the task to ESCALATED.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs

- Spec marked PROVISIONAL with `risk_class` of at least medium.
- Assumption log with a confidence per item, also stored in the scratchpad.
- Escalation record naming the assumptions to confirm.

## Failure Handling

- `must_go_provisional` false: stop and report which round is still open; do not publish early.
- `shared_scratchpad_put` fails or the value is too large: keep the log in the file and name it in `artifacts`.
- Never mark PROVISIONAL work VALIDATED or finish it IN_REVIEW as final; the gates must see it as provisional.

## Handover

Fill `blockers` (stakeholder unreachable), `decisions` (each assumption with its rationale), `open_questions`
(the unanswered list), `constraints` (risk_class and that confirmation is pending), `artifacts` (kind assumptions, the saved path, its digest, `producer` `A02`) and `verification` (`status`
partial, `confidence` the lowest item confidence). Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).

<!-- generated from prompts/skills/a02-requirements/provisional-spec.md; edit the source and run scripts/build_agents.py -->
