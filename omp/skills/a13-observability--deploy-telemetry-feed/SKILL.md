---
name: "a13-observability--deploy-telemetry-feed"
description: "Use when A12 asks for canary telemetry on a release step and the answer is a deploy.telemetry record with a continue, hold or rollback-suggested verdict."
disable-model-invocation: true
---

## When to Use

- A12 sends a `release.record` or `deployment.event` for a canary step (`release_id`, `step_pct`) and needs the guardrail stream (prompt outputs: `deploy.telemetry`).
- You supply telemetry; A12 decides (its `rel_canary_verdict` tool). The `verdict` here is a suggestion and the record must say so.
- User-visible incident breaches are a different job: `skill://a13-observability--incident-declare`.

## Procedure

1. Use `read` on the A12 message and note `release_id`, `step_pct`, the telemetry file holding only the step window (`{ts, ok, latency_ms}` lines), the service and the probe results.
2. Run `python3 scripts/obs_slo.py --evaluate --service <service> --events <step-window.jsonl> --now <epoch> --suspect <release_id> --task-id $TASK --correlation-id $CORR --json`. Read `manifest_found` (false means the objective is the script default: hold and use `skill://a13-observability--slo-manifest-intake`), `good`, `total`, `sli`, `objective` and `alert`. A breach also writes `.swarm/incidents/<id>.json`; that record is not a declared incident and its id is not cited in `deploy_telemetry`.
3. Call `obs_error_burn` with the output top-level `good`, `total` and `objective` to recompute `sli` and `burn_rate`. Disagreement with the script is E-INTERNAL: report it and hold.
4. Call `obs_probe_availability` with the canary's synthetic probe `results` (booleans in time order) and `min_availability` from the task. A probe set that misses the target turns `continue` into `hold`.
5. Map the `scripts/obs_slo.py` `alert` and the `obs_probe_availability` result to a verdict (producer convention of this skill): no alert and probes meeting target is `continue`; an alert with sev 3, sev 2 or a missed probe target is `hold`; an alert with sev 1 is `rollback-suggested`. `confidence`: no registry tool computes it; set 0 when the `obs_error_burn` or `obs_probe_availability` call was skipped, otherwise state your own estimate with the event `total` as basis.
6. Fill the spec shape from the script and tool outputs: `release_id`, `step_pct`, `error_rate` (1 - `sli`), `verdict`, `confidence`. `p95_ms` only when the events carry `latency_ms`: compute it once with `bash` (nearest-rank over that file) and say it was shell-computed; otherwise omit it and say so.
7. Call `shared_handover_validate`, then `shared_handover_write` to `A12`.

## Outputs

- `deploy.telemetry` block (spec section 3 shape) and a one-line summary naming the step, error rate and verdict.

## Failure Handling

- Step-window `total: 0`: no data; verdict `hold`, and use `skill://a13-observability--pipeline-degraded`.
- `obs_error_burn` or `obs_probe_availability` DEPENDENCY_UNAVAILABLE: retry twice, then `hold` with `skipped:tool-missing` named. Never emit `continue` on missing evidence.
- INVALID_INPUT on a field: fix the payload from the event; no retry on the same input.

## Handover

- `to_agent`: `A12`. `goal`: gate the next canary step for the release.
- `decisions`: verdict with the burn and probe numbers as rationale. `open_questions`: missing p95 or skipped evidence. `constraints`: verdict is advisory. `verification`: verified only when script, recompute and probes all ran.
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a13-observability/deploy-telemetry-feed.md; edit the source and run scripts/build_agents.py -->
