---
name: a25-resilience--chaos-experiment-design
description: Use when one chaos experiment needs a hypothesis, blast-radius limit, abort conditions and staged promotion to production under an error-budget gate.
owner: a25-resilience
tools: [resil_chaos_spec_check, resil_chaos_budget_gate, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Chaos experiment design

## When to Use

- One fault on one service (pod kill, node drain, latency, dependency outage, zone isolation) needs a spec a human can approve.
- Not for sequencing several experiments or roles: `skill://a25-resilience--game-day-plan`. Not for the failure ranking: `skill://a25-resilience--failure-mode-analysis`.
- Staging is L2; production is L3 and always error-budget gated (autonomy ceiling `experiment_prod`).

## Procedure

1. Run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <manifest-dir> --json`. A `fail` with `pdb-*`, `readiness-missing` or `startup-budget-too-small` for the target workload means the experiment would only rediscover a known defect: fix it first (`skill://a25-resilience--pdb-and-spread-hardening`, `skill://a25-resilience--probe-tuning`) and stop here.
2. Write the hypothesis with `write`, following the Principles of Chaos Engineering: steady-state metrics from the `slo.manifest` SLIs as `{metric, comparison, threshold}` (`lt`, `lte`, `gt`, `gte`), one injected variable, the expected result. Abort conditions are the hard stop that limits customer harm: set them at or inside the SLO boundary, never looser than it, and at least one must watch a steady-state metric (the tool checks only that a steady-state metric is watched, not the thresholds).
3. Size the blast radius as `affected_instances` of `total_instances`; start staging at the smallest unit (one pod, one node). Take `max_blast_percent`, `max_duration_minutes` and the freeze calendar from the task; none given is BLOCKED needs=input.
4. Call `resil_chaos_spec_check` with `environment` `staging` and every required field: name, start (ISO-8601 with UTC offset), duration_minutes, max_duration_minutes, steady_state, abort_conditions, affected_instances, total_instances, max_blast_percent, customer_facing, automatic_abort, rollback_documented, approval_recorded, error_budget_gate_passed, freeze_windows. Staging may run only with `blockers` 0 and `required_autonomy` `L2`. `no-steady-state-hypothesis` and `no-abort-conditions` are blockers; `abort-not-tied-to-steady-state` is a fix.
5. Cross-check the `resil_chaos_spec_check` output: `blast_radius_percent` must equal `affected_instances / total_instances * 100` to 4 places and `window_end` must equal `start` plus `duration_minutes`; a mismatch is E-CONTRACT.
6. After a recorded staging pass, call `resil_chaos_budget_gate` with `environment` `production`, `slo_target`, `window_days`, `budget_remaining_fraction`, `current_burn_rate` (both from A13, never guessed), `open_incident`, `experiment_duration_minutes`, `expected_error_ratio` (worst case), `reserve_fraction`, `max_cost_fraction`. Verify `cost_fraction` by hand: `expected_error_ratio * duration / (window_days * 1440 * (1 - slo_target))`. `decision` `no-go` stops; shorten to `max_duration_minutes` and retry once.
7. Re-run `resil_chaos_spec_check` with `environment` `production` and `error_budget_gate_passed` set from the gate `decision`. `required_autonomy` must be `L3`; only `approval-missing` may remain. `manual-abort-only`, `freeze-window` and `blast-radius-exceeds-limit` are blockers to fix, not to waive.
8. Use `write` for `chaos-plans/<service>/<experiment>.md`, call `shared_artifact_digest` with `{path}`, then `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "production chaos experiment awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `chaos.plan` for one experiment: hypothesis, spec-check and gate results, rollback step, digest.

## Failure Handling

- INVALID_INPUT: fix the named `field` once (`start` and `freeze_windows` need a UTC offset) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction` and name the gap. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and treat the gate as not passed.
- Script exit 2 (E-INPUT, bad `--path`): fix the path. Fault tool names (Chaos Mesh, Litmus, Chaos Toolkit, a cloud fault service) come from the task; never invent one.
- Never inject a fault here; production is BLOCKED needs=human-approval.

## Handover

`to_agent` A12. `goal`: run the approved experiment in its window. `decisions`: gate `decision`, `cost_fraction`, `required_autonomy`. `artifacts`: spec file with digest (producer A25). `open_questions`: approval, staging result. `verification`: `partial`. Call `shared_handover_validate`, then `shared_handover_write`.
