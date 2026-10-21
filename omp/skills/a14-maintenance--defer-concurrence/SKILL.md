---
name: "a14-maintenance--defer-concurrence"
description: "Use when someone asks to postpone a security patch; test the request against priority and deadline, then hold until A10 concurrence is recorded."
disable-model-invocation: true
---

# Security patch deferral

## When to Use

- A requester (A01, a feature owner, a human) asks to postpone, deprioritise or skip a security `patch.task` (A14 spec section 4, rule 6: A10 concurrence is required to defer; prompt autonomy lists a deferral as L3).
- Not when you triage a new CVE (`a14-maintenance--cve-triage`).

## Procedure

1. State the request exactly as received: which patch.task, the proposed new date, who asks and why. Missing pieces go into `open_questions`; do not fill them in.
2. Call `maint_patch_priority` with `{cvss, kev, exploit_maturity, blast_radius, usage_pct}` for the finding (values from the patch.task record or the A10 report). `immediate: true` (KEV or CVSS 9.0 or more) means the request defers a 24 h item and is flagged as such.
3. Call `maint_patch_deadline` with `{detected_at, kev, severity, sla_hours}` (`detected_at` UTC, exactly `YYYY-MM-DDTHH:MM:SSZ`; `severity` is the vulnerability severity critical, high, medium or low) and compare the proposed date with `deadline`. A new date inside the window is not a deferral and needs no concurrence; a date past it does.
4. For a deferral past the window, call `shared_escalate` with `{task_id, reason_code: "policy", summary: "Deferral of <CVE> past <deadline>", needs: ["A10 concurrence", "named risk owner"]}` and quote the priority `score` and `tier` in the summary. A14 never grants the deferral itself.
5. Hold the patch.task at its original deadline and return the `result_patch` (BLOCKED); do not wait or loop. When resumed, call `shared_handover_read` with `{task_id}` and `shared_open_questions` with `{task_id}` for A10's answer; only a handover or recorded answer from A10 changes the status.
6. When the `shared_handover_read` result shows A10's concurrence, record the new deadline, the compensating control and an expiry date for the deferral; when A10 refuses or stays silent, the original deadline stands.
7. Call `shared_handover_validate`, then `shared_handover_write` with the deferral record.

## Outputs

- A deferral record: request, tool evidence (`score`, `tier`, `deadline`), A10 decision with its source, new deadline and expiry, or `refused`.
- The BLOCKED `result_patch` from `shared_escalate` while concurrence is pending.

## Failure Handling

- `INVALID_INPUT` from either tool: the finding data is incomplete; ask through `open_questions`, keep the original deadline.
- No answer from A10 at resume: call `shared_escalate` once more; the patch is never silently deprioritised.
- A requester claiming A10 agreed without a record: treat as no concurrence.

## Handover

- Fill `goal` (apply A10's decision to the patch.task), `completed` (evidence gathered), `remaining` (await or apply concurrence), `decisions` (hold or defer with basis), `blockers` (A10 concurrence pending), `constraints` (original deadline stands until concurrence), `verification`.
- `shared_handover_write` to `A10` while pending, to `A01` once decided. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/defer-concurrence.md; edit the source and run scripts/build_agents.py -->
