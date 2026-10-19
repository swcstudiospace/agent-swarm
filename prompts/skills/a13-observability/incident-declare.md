---
name: a13-observability--incident-declare
description: Use when SLO evaluation yields a sev 1 or 2 alert and the incident must be confirmed, recorded and handed to A12 with rollback and flag-off options.
owner: a13-observability
tools: [obs_multiwindow_alert, shared_artifact_digest, shared_handover_validate, shared_handover_write, shared_log_emit]
---

## When to Use

- The obs_slo JSON carries an `alert` with `sev` 1 or 2 (obs_slo numbers 1 as worst: sev 1 is a 1h burn of 14.4x or more, sev 2 a 6h burn of 6x or more). Spec section 4 item 2 (sev above the ticket level declares an incident and pages humans at L2) is read that way, matching `obs_escalation_due`.
- A sev 3 ticket is not an incident: only report it in the slo.report.
- A12 decides deploy-side actions; you only offer rollback or flag-off options. You never issue `promote.command` or `rollback.command`.

## Procedure

1. Use `read` on the obs_slo stdout JSON and the incident record `.swarm/incidents/<incident_id>.json`. Use the `incident_id` exactly as written; if there is no record, stop and rerun `skill://a13-observability--burn-evaluation`.
2. Confirm that the cross-checks recorded by burn-evaluation (`obs_error_burn` and `obs_budget_remaining` against `scripts/obs_slo.py`) matched. If they did not, stop: E-INTERNAL, no incident until a rerun agrees.
3. Call `obs_multiwindow_alert` with the three window error ratios and `slo_target` (the output `objective`). Compare `burn_1h`, `burn_6h`, `burn_3d` with the script. `page: true` corroborates on both windows. `page: false` with a script sev 1 or 2 is a single-window breach (the script alerts on one window, the tool needs 1h and 6h): declare per the script and write `single-window` in the evidence. A value mismatch is E-INTERNAL.
4. List suspect releases from the task inputs or A12's `deployment.event` records. Never guess; an empty `suspect_changes` goes into `open_questions`.
5. Call `shared_artifact_digest` with `path: ".swarm/incidents/<incident_id>.json"`.
6. Fill the incident block from the script `alert` and the prompt `output_format`: `incident_id`, `sev`, `slo`, `evidence` (burn_rate, threshold, window, sli, objective, error_budget_remaining), `suspect_changes`, `playbooks_available` (the alert carries `rollback:latest-release` and `flag-off:latest-feature`; replace them with runbook playbooks the task names), `runbook`, `declared_by`, `at`. Paging humans is `action: "page"` on this `incident.alert`; the registry has no separate paging tool.
7. Call `shared_log_emit` with `level: "error"`, `message: "incident declared"` and `fields` carrying `incident_id`, `sev` and `slo` (scalars only).
8. Call `shared_handover_validate`, then `shared_handover_write` to `A12`.

## Outputs

- `incident.alert` JSON block with `incident_id` and `sev` set, plus the markdown summary (SLI vs objective, budget left, windows burning, recommendation to A12).

## Failure Handling

- `obs_multiwindow_alert` DEPENDENCY_UNAVAILABLE: retry twice, then declare from the script values with `skipped:tool-missing` named.
- Incident record unreadable or id missing: FAILED E-INTERNAL; never mint an id.
- Handover write rejected: fix the field named in the error, validate again, write once.

## Handover

- `to_agent`: `A12`. `goal`: choose rollback or flag-off for the named release.
- `decisions`: severity and rationale (window and burn). `artifacts`: `{kind: "incident.alert", uri: ".swarm/incidents/<id>.json", digest, producer: "A13"}`. `remaining`: A12 deploy decision, mitigation per `skill://a13-observability--mitigation-run`. `constraints`: A13 cannot stop or roll back releases. `verification`: verified when the cross-checks agreed.
- Tools: `shared_handover_validate` then `shared_handover_write`.
