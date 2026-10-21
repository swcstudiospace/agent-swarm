---
name: "swarm-open-questions"
description: "Use when requirements, inputs or upstream answers are unclear; list open questions, resolve what the record answers, escalate the rest instead of guessing."
disable-model-invocation: true
---

# Open questions

## When to Use

- An acceptance criterion is ambiguous, an input is missing, two sources disagree, or an upstream result carries `needs` you depend on.

## Procedure

1. Call `shared_open_questions` with `{task_id}`. Each question has `origin` (`upstream` or `handover`), the `task_id` it belongs to and its text. `truncated` true means more exist than listed.
2. Call `shared_handover_bundle` with `{task_id}` and check whether `goal`, `acceptance`, `decisions` or `constraints` already answer a question. An answer that is in the record is not a question any more.
3. Call `shared_scratchpad_get` with `{correlation_id}` (from `task.correlation_id` in the bundle) to see answers other agents left under a key. Use a value only from a key whose `meta.owner` is the agent that would know.
4. Sort the questions that `shared_open_questions` still lists: blocking (you cannot produce a correct result without the answer) and non-blocking (you can proceed under a stated assumption).
5. For a non-blocking question state the assumption explicitly, keep it reversible, and call `shared_scratchpad_put` with `{correlation_id, key: "<agent-id-lowercase>.assumptions" (for example `a05.assumptions`), value: {...}, shared: true}` so the next agent can see it.
6. For a blocking question stop. Call `shared_escalate` with `{task_id, reason_code: ambiguous-input, summary, needs}` where each `needs` entry is one answerable question; use the returned `result_patch` as the BLOCKED `task.result`.
7. Never answer a question by guessing a business decision, a security setting or a data-destroying choice; `shared_escalate` exists for exactly those.

## Outputs

- A short list of questions, each resolved, assumed or escalated.
- A BLOCKED result when a blocking question remains.

## Failure Handling

- `INVALID_INPUT` on the scratchpad key: use only `[a-z0-9._-]` (lowercase) and at most 64 characters.
- `UNAUTHORIZED` or an unknown task from `shared_escalate`: the task is not assigned to you; write the BLOCKED result by hand and stop.
- A version conflict on `shared_scratchpad_put`: read again and merge (`swarm-scratchpad-protocol`).
- `DEPENDENCY_UNAVAILABLE`: retry at most twice, then BLOCKED.

## Handover

- Carry unresolved and assumed items in `open_questions` of your handover, assumptions marked as assumptions.

<!-- generated from prompts/skills/shared/open-questions.md; edit the source and run scripts/build_agents.py -->
