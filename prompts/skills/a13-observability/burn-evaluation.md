---
name: a13-observability--burn-evaluation
description: Use when an obs.slo task asks for an SLO check on a service that already has a manifest and the answer is a cross-checked slo.report.
owner: a13-observability
tools: [obs_budget_remaining, obs_error_burn, obs_multiwindow_alert, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- Periodic or on-demand evaluation of one service with an existing `slo.manifest`.
- This skill produces evidence only. When it finds sev 1 or 2, continue with `skill://a13-observability--incident-declare`; do not declare anything here.
- `obs_slo.py --evaluate` writes `.swarm/incidents/<id>.json` on any breach, sev 3 included, and mints a new id (`INC-<epoch mod 100000>`) on every breaching run. Keep the first id you cite; a later record is evidence, never a second incident. Never invent or change an id.

## Procedure

1. Run `python3 scripts/obs_slo.py --evaluate --service <service> --good <n> --total <n> --task-id $TASK --correlation-id $CORR --json`. For telemetry as a file use `--events <file.jsonl> --now <epoch>` instead of counts (lines are `{ts, ok, latency_ms}`); counts mode applies the same counts to all three windows. Add `--suspect REL-1,REL-2` when the task names suspect releases. Exit 0 means no sev 1/2 alert (a sev 3 ticket is still exit 0), exit 1 a sev 1/2 breach (not a tool error), exit 2 a taxonomy error. Reason over the stdout JSON only.
2. Read `manifest_found`. When it is `false` the script used its default objective 0.999, which no one supplied: do not cite any figure, use `skill://a13-observability--slo-manifest-intake`.
3. For every window in `windows` that breached, or whose `burn_rate` is at least half its threshold (7.2x for 1h, 3x for 6h, 0.5x for 3d: this skill's definition of "within reach"), call `obs_error_burn` with that window's `good`, `total` and the output `objective`. A window with `total: 0` has no data (its `burn_rate` is null): skip it and use `skill://a13-observability--pipeline-degraded`.
4. Compare `sli` and `burn_rate` from the `obs_error_burn` calls with the `scripts/obs_slo.py` values. Any disagreement is E-INTERNAL: report both values and state no severity until a rerun agrees.
5. Call `obs_budget_remaining` with the output top-level `good`, `total` and `slo_target` equal to `objective`. Its `remaining_fraction` must equal the script `error_budget_remaining` to 4 places; a mismatch is E-INTERNAL.
6. Call `obs_multiwindow_alert` with `error_ratio_1h`, `error_ratio_6h`, `error_ratio_3d` set to `(total - good) / total` per window (skip it when any window has `total: 0`) and `slo_target`. Its `burn_1h`, `burn_6h`, `burn_3d` must match the script window `burn_rate` values to 4 places. Its `page` is stricter than the script: it needs 1h at least 14.4x and 6h at least 6x, while the script alerts sev 1 on 1h alone and sev 2 on 6h alone. `page: false` with a script sev 1/2 therefore means a single-window breach: record that, it is not E-INTERNAL.
7. Take the severity only from the `scripts/obs_slo.py` fields `alert.sev` and `alert.evidence.window`, and the thresholds from `thresholds` or `alert.evidence.threshold`; never compute your own.
8. Assemble the `slo.report`: SLI vs objective, budget remaining, per-window burn, cross-check results. When the script wrote an incident record, call `shared_artifact_digest` with its path `.swarm/incidents/<id>.json`. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- `slo.report` (summary plus the prompt JSON block: `slo`, `evidence`; `incident_id` and `sev` null when there is no sev 1/2 alert).
- Recomputed `obs_error_burn` values cited as tool output, `skipped:tool-missing` named where a call failed.

## Failure Handling

- `obs_error_burn` DEPENDENCY_UNAVAILABLE: retry the same input twice, then record `skipped:tool-missing` and use the script value for that window with the gap named.
- INVALID_INPUT on `objective` (manifest holds 1.0): BLOCKED needs=input for A03 or A02; never correct it.
- `--evaluate` exit 2 with E-INPUT (no `--total` or `--events`): ask for counts or a telemetry file.

## Handover

- `to_agent`: `A12` when the evaluation serves a release decision, else the agent that assigned the task (`A01` unless the task names another). `goal`: the evaluated service and window.
- `completed`: checks run. `decisions`: severity cited from `alert.sev`. `artifacts`: the incident record with its `shared_artifact_digest` digest, if one was written. `open_questions`: skipped windows. `verification`: verified only if every cross-check agreed, else partial with the gaps in `checks`.
- Tools: `shared_handover_validate` then `shared_handover_write`.
