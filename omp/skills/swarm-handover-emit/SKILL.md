---
name: "swarm-handover-emit"
description: "Use when your work ends or pauses and another agent continues it; write a validated HandoverV1 with digests and cite its id in the task result."
disable-model-invocation: true
---

# Handover emit

## When to Use

- Before the final `task.result` of every task that has a downstream agent, a reviewer, or A01 as the next reader.
- On cooperative cancel, escalation or a pause: hand over what exists (`swarm-cooperative-cancel`).

## Procedure

Every `task_id` below is YOUR OWN task id (the producer's), never the receiver's: the stored handover is `<your task id>.<seq>` with `to_agent` naming the next agent. The receiver finds it by reading your task id (`swarm-handover-intake`), and `swarm_run` shows it to the tasks that depend on yours.

1. Call `shared_artifact_digest` with `{path}` for every file you produced or changed that a reader needs. Keep `{kind, uri, digest, producer}` with `digest` exactly as returned (`sha256:` plus 64 hex) and `producer` set to your own agent id (for example `A05`, never the slug). A non-file output has no digest and cannot be listed in `artifacts`: name it in `completed` instead.
2. Call `shared_open_questions` with `{task_id}` and collect what is still unresolved for the next reader.
3. Call `shared_dod_check` with `{result: <your draft result>, task_id}` and set `verification` from it: `verified` only when `all_met` is true and artifacts were checked, `partial` when some criteria are `unknown`, otherwise `unverified`. `confidence` (0 to 1) is your honest estimate, never rounded up.
4. Fill the document for `shared_handover_write`: `goal` (one sentence, what the next agent must achieve), `completed` (short facts), `remaining` (concrete next steps), `decisions` (each `{decision, rationale}`), `artifacts`, `open_questions`, `blockers`, `constraints` (rules the next agent must keep), `verification` (`{status, confidence, checks: []}`). Every list is present, empty when there is nothing.
5. Call `shared_handover_validate` with `{document, draft: true}`, where `document` holds the fields of step 4 plus `task_id`, `correlation_id` (the `task.correlation_id` of `shared_handover_bundle`), `from_agent` (you) and `to_agent`; `draft` fills only `schema_version`, `handover_id`, `created_wall`, `summarized` and `bytes`. Fix every message in `errors`. If `would_summarize` is true, shorten `completed`, `decisions` and `artifacts` yourself so `goal`, `remaining`, `blockers`, `open_questions` and `constraints` survive whole.
6. Call `shared_handover_write` with `{task_id, to_agent, goal, ...}`; `to_agent` is the next agent id (`A01`..`A15`), or `A01` when you do not know. `from_agent` is always you. Check `status` is `ok` and note `handover_id`, `digest`, `summarized`, `duplicate`.
7. Call `shared_handover_read` with `{task_id}` once and confirm the returned `handover.handover_id` equals the written one and `status` is `ok`.
8. Put `Handover: <handover_id> <digest>` in the `summary_md` of your `task.result`.

## Outputs

- One stored handover (`handover_id`, `ref`, `seq`, `digest`).
- A `task.result` whose `summary_md` names that handover id.

## Failure Handling

- `INVALID_INPUT`: fix the named field once and retry; a second failure is a BLOCKED result naming the field.
- `status` of `partial` or `malformed` after writing: add `remaining` or `artifacts` and write again (a changed document gets a new seq).
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry at most twice, then report BLOCKED. Never claim a handover that was not written.
- Never put secrets in a handover; they are redacted, but do not rely on it.

## Handover

- This skill produces the handover; the receiver runs `swarm-handover-intake` against it.

<!-- generated from prompts/skills/shared/handover-emit.md; edit the source and run scripts/build_agents.py -->
