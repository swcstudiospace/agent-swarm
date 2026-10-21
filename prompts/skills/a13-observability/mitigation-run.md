---
name: a13-observability--mitigation-run
description: Use when a whitelisted runbook mitigation is proposed during an open incident and must be gated, executed with before and after evidence, and counted.
owner: a13-observability
tools: [obs_error_burn, obs_mitigation_gate, shared_escalate, shared_handover_validate, shared_handover_write, shared_log_emit, shared_scratchpad_get, shared_scratchpad_put]
---

## When to Use

- An incident exists and a runbook step (for example `flag-off:checkout-v2`) is proposed as an automatic mitigation.
- Rules (prompt decision_logic 3, spec section 4): whitelisted playbooks only, canary or staging, full prod only together with A12, at most 3 automatic mitigations per incident, every action logged with before and after evidence. A production scale-out or any non-whitelisted action is L3: BLOCKED needs=human-approval, whatever the gate says.
- No registry tool or script executes playbooks; the runbook's own documented command is run through `bash`. Reporting the timeline to humans is `skill://a13-observability--escalation-timeline`.

## Procedure

1. Use `read` on the runbook named in the incident record. Take the playbook id and its documented command from it, and the `whitelist` (playbook ids) from the task inputs; without a whitelist the playbook is not whitelisted.
2. Call `shared_scratchpad_get` with the task `correlation_id` and `key: "mitigations.<incident id lowercased>"` (keys match `[a-z0-9._-]`, at most 64 characters). Use `value.auto_count` (0 when `found` is false) and remember `meta.version` (0 when not found).
3. Call `obs_mitigation_gate` with `playbook`, `whitelist`, `environment` (`canary`, `staging` or `prod`), `auto_count`, and `full_prod_with_a12: true` only when the task input or a handover addressed to you records A12's confirmation.
4. If `allowed` is false, stop. For `not-whitelisted` or `prod-needs-approval` call `shared_escalate` with `reason_code: "policy"` and `needs: ["human approval for <playbook>"]`; for `cap-reached` use `reason_code: "budget"` and `needs: ["human incident command"]`. Return its `result_patch` (BLOCKED needs=human-approval).
5. Capture before evidence: current window counts from the telemetry the task names, then `obs_error_burn` with `good`, `total` and the manifest `objective`. If you re-run `scripts/obs_slo.py --evaluate` for the counts, it writes a new incident record on a breach; keep the original incident id.
6. Use `bash` to run the runbook command once, with the scoped credential the runbook names; do not loop.
7. Capture after evidence the same way with `obs_error_burn` on a fresh window; `burn_rate` must not rise. If it did, do not retry the mitigation: record it and escalate.
8. Call `shared_scratchpad_put` with the same `key`, `value: {"auto_count": <n+1>, "actions": [<earlier actions>, {"playbook", "environment", "burn_before", "burn_after"}]}` (at most 3 actions, under 8 KiB), `if_version` set to that `meta.version` (0 creates the key) and `shared: true` on creation, so another A13 replica may continue the count.
9. Call `shared_log_emit` with `level: "info"`, `message: "mitigation executed"` and `fields` for `playbook`, `environment`, before and after burn (scalars).
10. Call `shared_handover_validate`, then `shared_handover_write` to `A12`.

## Outputs

- A mitigation log entry: playbook, environment, gate reason, before and after `burn_rate`, `auto_count` after the action (stored in the scratchpad key above).

## Failure Handling

- `shared_scratchpad_put` version conflict or UNAUTHORIZED (key owned by another replica and not shared): re-read, recompute `auto_count` from the new value, rerun `obs_mitigation_gate` before any second action.
- `obs_error_burn` DEPENDENCY_UNAVAILABLE on the before sample: do not execute (no evidence, no action); retry twice, then escalate with `reason_code: "blocked"`.
- Command failure: log it, count it as an attempt against the cap, stop.

## Handover

- `to_agent`: `A12`. `goal`: confirm stabilization or decide rollback.
- `completed`: mitigation and evidence. `decisions`: gate reason. `remaining`: further steps or escalation. `blockers`: pending approvals. `verification`: verified when both burn samples exist.
- Tools: `shared_handover_validate` then `shared_handover_write`.
