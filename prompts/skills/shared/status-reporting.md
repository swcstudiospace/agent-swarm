---
name: swarm-status-reporting
description: Use when a task runs longer than a few steps; report truthful progress, blocked and done through shared_status_report so the live bar and A01 see real state.
owner: shared
tools: [shared_status_report, shared_cancel_check, shared_escalate]
---

# Status reporting

## When to Use

- At the start of a task, at each step boundary, when you become blocked, and once when you are done.

## Procedure

1. At the first step call `shared_status_report` with `{task_id, kind: progress, step: "<what you do now>"}`. Report your own task only; another task is refused.
2. After each completed milestone (a file group written, a test run finished, a phase done) call `shared_status_report` again. Report at most once per step boundary and not more than about once every 15 to 30 seconds: identical consecutive reports are skipped and rapid progress reports are throttled to one event per second, so a flood adds nothing.
3. Set `percent` in `shared_status_report` only from counted work (`done / total`, for example 3 of 8 files). Omit it when you cannot count. Never round up, never report 100 before the work is verified, and never move it backwards without a `note` saying why.
4. Keep the `step` and `note` of `shared_status_report` short (240 characters are kept) and factual: verbs and file names, no prompts, no tokens, no credentials, no personal data. Values are redacted, but do not rely on it.
5. When you cannot continue, call `shared_status_report` with `kind: blocked` and the reason in `step`, then call `shared_escalate` and finish with the BLOCKED result.
6. Poll `shared_cancel_check` with `{task_id}` at the same boundaries; a cancel request overrides the plan (`swarm-cooperative-cancel`).
7. When verified (`swarm-self-verify`), call `shared_status_report` with `kind: done` and a one-line `step`, then emit the final `task.result`. A `done` report is not a substitute for the result.

## Outputs

- A stream of accurate steps in the live view; the latest status stored for the task.

## Failure Handling

- `INVALID_INPUT`: `kind` must be progress, blocked or done; `step` non-empty; `percent` 0 to 100.
- `UNAUTHORIZED` (not your task) or `DEPENDENCY_UNAVAILABLE`: status is advisory; continue the work, do not retry in a loop, and mention it in `summary_md`.
- `recorded: false` with `emitted: false` is a harmless duplicate.

## Handover

- The final status text is not a handover; put durable facts in the handover (`swarm-handover-emit`).
