---
name: a14-maintenance--regression-watch
description: Use when a telemetry.anomaly appears after one of your patches shipped and you must judge regression, recommend rollback to A12 and reopen a narrower fix.
owner: a14-maintenance
tools: [shared_handover_read, shared_attempt_history, maint_hotfix_decision, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Patch regression watch

## When to Use

- A13 sends `telemetry.anomaly` and a patch you opened or merged went live shortly before (A14 spec section 5: patch regression detected by A13).
- Not before any patch exists (`a14-maintenance--hotfix-or-rollback`) and not for a cause-only write-up (`a14-maintenance--incident-rca`).
- The rollback-or-hotfix rule, its severity convention and its escalation conditions are those of `skill://a14-maintenance--hotfix-or-rollback`; this skill adds the correlation, the narrower fix and the tally.

## Procedure

1. Call `shared_handover_read` with `{task_id: <the patch task>}` to read what was shipped, the decisions and the verification claimed. A `stale` or `missing` status goes into `open_questions`.
2. Call `shared_attempt_history` with `{task_id}` for the patch: attempt count, rework loops and last error show whether the patch already had a shaky gate history; `task_known: false` is stated and skipped.
3. Use `read` on the `release.record` that shipped the patch and compare its deploy time with the anomaly start from the `telemetry.anomaly`. The anomaly counts as a regression only when it starts after the deploy and touches the patched component; otherwise report `no-regression-evidence` with both timestamps and stop.
4. For a regression call `maint_hotfix_decision` exactly as in `hotfix-or-rollback` step 2, passing the `sev` of A13's `incident.alert` unchanged (with no declared incident, ask A13 through `open_questions` and do not call the tool). `rollback` means send the rollback recommendation to A12; `hotfix` means reopen a narrower patch.task alongside it.
5. Reopen the work as a narrower `patch.task` (smaller diff than the original, same gates). The incident-style postmortem item (A14 spec section 5) is an `a14-maintenance--incident-rca` follow-up once the incident is stabilized; list it as `remaining`.
6. Call `shared_escalate` with `{task_id, reason_code: "policy", summary, needs: [...]}` under the same conditions as `hotfix-or-rollback` step 6 (path `defer`, or a high-risk rollback). A13 declares incidents and humans resolve them; you never close one.
7. Update the regression tally for A14 spec section 6 (target under 3 %): patches regressed over patches shipped in the window, both counts taken from the `release.record` entries and earlier reports in the task bundle; if they cannot be counted write `tally unavailable`. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- A `patch.regression.report`: verdict, evidence (timestamps, components), rollback recommendation, narrower fix reference, tally.

## Failure Handling

- No `release.record` found: you cannot correlate; verdict `inconclusive`, escalate for the record rather than assume.
- `shared_attempt_history` returns `task_known: false`: say so in the report and continue with the other evidence.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry once, then report the missing check as a gap in `verification`.

## Handover

- Fill `goal` (decide rollback and ship the narrower fix), `completed` (correlation done), `remaining` (rollback decision, narrower fix, postmortem), `decisions` (verdict, tool `path`), `artifacts` (release.record uri), `open_questions`, `constraints` (gates unchanged), `verification`.
- `shared_handover_write` to `A12`. See `skill://swarm-handover-emit`.
