---
name: swarm-rework-intake
description: Use when a gate sent your task back for rework; collect the open findings, fix them by id and show each fix as evidence before resubmitting.
owner: shared
tools: [shared_attempt_history, shared_open_findings, shared_handover_bundle, shared_dod_check, shared_escalate, shared_status_report]
---

# Rework intake

## When to Use

- The assignment says rework, a gate failed your task, or `shared_handover_verify` showed a handover from a gate agent (A08, A09, A10 or A12).
- Run `swarm-handover-intake` first; this skill adds the findings.

## Procedure

1. Call `shared_attempt_history` with `{task_id}`. Read `rework_loops` against `max_rework_loops` (2). When `rework_loops` equals the maximum, this is the last loop: a repeated failure escalates the task.
2. Call `shared_open_findings` with `{task_id}`. Findings are ordered most severe first. Each has `gate`, `agent`, `id`, `severity`, `kind`, `summary`, `location`, `ac_ref`, `blocking`, `stale` and `expires_in_s`. A finding past its expiry no longer appears. In a rework the findings of the verdicts that sent the task back are `stale: true` (they predate the rework) and are your work list: the gate has not re-reviewed yet. A `stale: false` finding comes from a verdict issued after the rework.
3. Call `shared_handover_bundle` with `{task_id}` and read `verdicts[]`: `signature` must be `valid` and `expired` false before you treat a verdict as authoritative (`swarm-provenance-trust`). `stale: true` marks a verdict that predates the rework: it names what to fix, it does not say your fix failed.
4. Fix every `blocking: true` finding first, one by one, at its `location`, in the files you own. Fix non-blocking findings when cheap. Never edit the gate's tests, verdicts or the acceptance criteria.
5. For each finding from `shared_open_findings` record `finding <id> fixed: <what changed>` as a pass check in your draft result, keyed to its `ac_ref` where present. A finding you will not fix needs a stated reason in `decisions`, never silence.
6. Call `shared_dod_check` with `{result: <draft>, task_id}`. Anything `unknown` has no evidence yet: add it or say it is unmet.
7. Call `shared_status_report` with `{task_id, kind: progress, step: "rework <n>: <k> of <m> findings fixed", percent}` after each batch of fixes, with `percent` equal to fixed over total.
8. If a finding cannot be fixed within your ownership or contradicts another finding, call `shared_escalate` with `reason_code: conflict` and the finding ids in `needs`.

## Outputs

- Changed artifacts with fresh digests (`swarm-handover-emit`).
- A result whose checks name every finding id and its fix.

## Failure Handling

- `max_rework_loops` reached and a blocking finding remains: `shared_escalate`, then BLOCKED; do not resubmit the same failing work.
- `INVALID_INPUT` or `DEPENDENCY_UNAVAILABLE`: retry at most twice, then BLOCKED with the tool name.

## Handover

- Hand the fixed artifacts, the finding ids addressed and the decisions to the gate agent that issued the verdict.
