---
name: a13-observability--slo-manifest-intake
description: Use when an obs.slo task names a service that has no slo.manifest yet and A03 or A02 has supplied the objective to define one.
owner: a13-observability
tools: [obs_budget_remaining, shared_artifact_digest, shared_escalate, shared_handover_validate, shared_handover_write]
---

## When to Use

- A signed `task.assign` with capability `obs.slo` names a service and `.swarm/slo/<service>.json` does not exist.
- Not for reading burn on an existing manifest: use `skill://a13-observability--burn-evaluation`.
- The objective is never yours to choose (prompt OPERATING_PROCEDURE step 2, decision_logic 6). It must arrive in the task inputs from A03 or A02. `obs_slo.py` defaults `--objective` to 0.999 and `--evaluate` falls back to that default without a manifest: always pass the supplied value explicitly.

## Procedure

1. Use `read` on the task.assign envelope and note `service`, `sli` (availability or latency), `objective`, `window` (7d, 28d or 30d) and the runbook path from `inputs[]`.
2. Use `glob` on `.swarm/slo/<service>.json`. If it exists, stop and hand the existing manifest path back; `--define` would overwrite it.
3. Check the supplied objective is a fraction strictly between 0 and 1 and names A03 or A02 as its source. If it is missing, out of range or unattributed, call `shared_escalate` with `task_id`, `reason_code: "ambiguous-input"`, `summary` naming the missing field and `needs: ["objective from A03 or A02"]`, then stop with its `result_patch` (state BLOCKED needs=input).
4. Run `python3 scripts/obs_slo.py --define --service <service> --sli <availability|latency> --objective <objective> --window <7d|28d|30d> --runbook <runbook path> --latency-ms <ms> --task-id $TASK --correlation-id $CORR --json`. Always pass `--runbook` (the script default is the literal text `runbooks/<service>.md`) and, for a latency SLI, `--latency-ms` (default 300).
5. Sanity-check the budget with `obs_budget_remaining` using `good`, `total` from a sample the task supplies and `slo_target` equal to the objective. `exhausted: true` on a sample the task calls healthy means the objective is mis-set: report it, do not edit it.
6. Use `glob` on the runbook path. A missing runbook is recorded as an open question for A15 (runbook owner); the alert linter rejects alerts without one.
7. Call `shared_artifact_digest` with `path` set to the manifest `path` returned in step 4.
8. Build the handover and call `shared_handover_validate` with `document` set to it, then `shared_handover_write`.

## Outputs

- `slo.manifest` at `.swarm/slo/<service>.json` (`kind: slo.manifest`, `owner: A13`, `alerting` rows 1h 14.4x sev 1 page, 6h 6x sev 2 page, 3d 1x sev 3 ticket).
- Markdown summary of service, SLI, objective, window and runbook, and one fenced `json` block per the prompt `output_format` with `incident_id` and `sev` null.

## Failure Handling

- `obs_slo.py` exit 2 (E-INPUT on objective): BLOCKED needs=input; do not retry with another number.
- `obs_budget_remaining` INVALID_INPUT on `good` (above `total`): the sample is inconsistent, ask for a corrected one; DEPENDENCY_UNAVAILABLE: retry twice, then record `skipped:tool-missing`.
- Manifest already present: report it as `completed`, change nothing.

## Handover

- `to_agent`: the agent that supplied the objective (`A03` or `A02`). `goal`: confirm the manifest matches the intended SLO.
- `completed`: manifest defined. `remaining`: runbook creation if missing. `artifacts`: `{kind: "slo.manifest", uri, digest, producer: "A13"}`. `decisions`: objective source and rationale. `open_questions`: runbook gap. `verification`: status verified when the budget sample passed.
- Tools: `shared_handover_validate` then `shared_handover_write`.
