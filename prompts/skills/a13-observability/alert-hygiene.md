---
name: a13-observability--alert-hygiene
description: Use when an alert rule set changes or the weekly review runs and rules without SLO or runbook links or with excess noise must be flagged.
owner: a13-observability
tools: [obs_alert_fatigue, obs_multiwindow_alert, obs_runbook_link_coverage, shared_handover_validate, shared_handover_write]
---

## When to Use

- A new or changed alert rule set (obs.alerts), or the periodic review of page quality.
- Policy (decision_logic 1): every alert maps to an SLO and a runbook or your own linter rejects it; metrics target 100 % runbook links and fewer than 1 false page per week.
- Metric shape and series budget belong to `skill://a13-observability--cardinality-guard`, not here.

## Procedure

1. Use `read` on the rule file(s) the task names and list each alert as `{name, slo, runbook, windows, thresholds}`. The repo defines no rule-file format: this skill assumes (convention) that each rule carries an `slo` label or annotation and a `runbook` annotation. A missing field stays empty; never fill it in.
2. Use `glob` on the runbook directory the task names (the `obs_slo.py` default is `runbooks/<service>.md`) to build `known_runbooks`.
3. Call `obs_runbook_link_coverage` with `alerts` (`{name, runbook}`; an empty string is "no link") and `known_runbooks` (exact path match, so normalise both sides the same way). Every name in `unlinked_alerts` is rejected.
4. Use `grep` on the rule files for the `slo` field and reject any alert without one, even when its runbook resolves.
5. For each burn-rate rule read its `slo` objective from `.swarm/slo/<service>.json` (`read`) and call `obs_multiwindow_alert` with `slo_target` set to it and each `error_ratio_*` set to the rule's own burn threshold for that window times `(1 - slo_target)`. The tool pages only when 1h is at least 14.4x and 6h at least 6x, and tickets at 3d 1x; a rule whose own thresholds give `page: false` or `ticket: false` is `drifted`. INVALID_INPUT on an `error_ratio_*` above 1 means the threshold is unreachable at that objective: also `drifted`.
6. Call `obs_alert_fatigue` with `pages` and `actionable` for the review period and `max_noise_ratio` from the task input. If a count or the ratio is not supplied, record an open question; do not pick a value.
7. Compose the alert-lint report from the `obs_runbook_link_coverage`, `obs_multiwindow_alert` and `obs_alert_fatigue` results (rejected alerts with reason, drifted rules, `coverage_ratio`, `noise_ratio`, `fatigued`) in the result summary; no rule is edited.
8. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- Alert-lint report: coverage ratio, rejected alerts, drifted rules, noise ratio. No rule is edited by this skill.

## Failure Handling

- `obs_runbook_link_coverage` INVALID_INPUT (empty `alerts`): the rule set is empty; report it and stop.
- `obs_alert_fatigue` INVALID_INPUT because `actionable` exceeds `pages`: the review data is inconsistent; ask for corrected counts.
- DEPENDENCY_UNAVAILABLE: retry twice, then name the check as `skipped:tool-missing`.

## Handover

- `to_agent`: the agent that assigned the task (`A01` unless the task names another). `goal`: fix the rejected alerts.
- `completed`: checks run. `remaining`: unlinked alerts and drifted rules. `open_questions`: missing `max_noise_ratio` or counts. `verification`: verified when coverage, drift and fatigue all ran.
- When any alert is unlinked, write a second handover to `A15` listing only those alerts (runbook owner).
- Tools: `shared_handover_validate` then `shared_handover_write`.
