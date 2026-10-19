---
name: "a02-requirements--clarification-round"
description: "Use when ambiguity exceeds 0.4 or over 30% of criteria are manual-only; issue a clarification.request within the 2-round, 48 h limit."
disable-model-invocation: true
---

# Clarification Round

Actively asks the stakeholder. A spec cannot be marked VALIDATED while the ambiguity score is above 0.4 or more
than 30 % of criteria are manual-only (spec section 4, rule 2). At most 2 rounds with a 48 h window each; what
happens once those are spent belongs to `a02-requirements--provisional-spec`.

## When to Use

- `req_ambiguity_scan` returned `needs_clarification` true, or `req_criteria_testability` did.
- A stakeholder answered and a further round may still be needed.

## Procedure

1. Call `shared_handover_read` with `task_id`; take earlier rounds from `handover.decisions` (convention: one decision per round whose text holds `CR-<n>`, `sent_at` and `answered_at`) and the current `handover.open_questions`.
2. Call `req_ambiguity_scan` with `text` set to the current requirement text. Keep `score`, `terms` and the flagged sentences.
3. Call `req_criteria_testability` with `criteria` as `{id, text, automated}`. Keep `manual_ratio` and `unmeasurable_ids`.
4. If neither check triggers, stop: no round is needed. Otherwise get the time with `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) and call `req_clarification_window` with `rounds` as `{id, sent_at, answered_at?}` for every earlier round, `now` as that ISO timestamp, and the defaults for `max_rounds` (2) and `window_h` (48).
5. If `req_clarification_window` shows `can_send_round` false (two rounds used), do not compose a new round: switch to `a02-requirements--provisional-spec`. If `open_within_window` is above 0, wait for that answer instead of asking again.
6. Use `write` to compose the `clarification.request` in `docs/requirements/clarification-<round>.md` (convention path): a round id `CR-<n>`, one numbered question per flagged term group or unmeasurable criterion id, each quoting the sentence and offering concrete options, plus the answer deadline (`now` plus 48 h). Call `shared_artifact_digest` with that `path`.
7. Call `shared_escalate` with `task_id`, `reason_code` `ambiguous-input`, `summary` naming the round and the deadline, and `needs` holding the numbered questions. It records an `escalation.request` event (no ACP route lets A02 emit `clarification.request` itself) and returns a BLOCKED `result_patch`: finish BLOCKED with it so A01 and the human inbox see the questions.
8. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs

- `clarification.request` with round id, questions, options and deadline.
- Round count and `req_clarification_window` figures cited as evidence.

## Failure Handling

- `req_clarification_window` INVALID_INPUT (timestamp order or duplicate id): correct the round record; never invent a timestamp.
- `shared_escalate` fails: retry once, then BLOCKED needs=tool; the round is not counted as sent.
- Any scan TIMEOUT: retry once, then E-TIMEOUT; do not ask questions from an unverified score.

## Handover

Fill `blockers` (waiting for stakeholder answers, deadline), `open_questions` (the round's questions),
`decisions` (the round record with its `sent_at`), `remaining` (finish stories or criteria once answered),
`artifacts` (kind clarification, the saved path, its digest, `producer` A02) and
`verification` (`status` unverified, `confidence` low). Call `shared_handover_write` with `to_agent` `A01` (an agent id, never the slug).

<!-- generated from prompts/skills/a02-requirements/clarification-round.md; edit the source and run scripts/build_agents.py -->
