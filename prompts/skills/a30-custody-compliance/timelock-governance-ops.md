---
name: a30-custody-compliance--timelock-governance-ops
description: Use when a custody.policy task must size a timelock delay against value at risk, response time, cancel path, admin role and expiry window.
owner: a30-custody-compliance
tools: [shared_handover_read, custody_timelock_check, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Timelock governance operations

## When to Use

- A task asks whether a timelock (OpenZeppelin `TimelockController`, Compound-style `Timelock`, a Safe delay module) protects enough value, or what delay and roles to configure.
- Designing the policy is L2. Changing `minDelay`, roles or the admin of a live timelock is L3, and queueing, executing or cancelling a mainnet operation is L4 human work; none of it is done here.
- Code-level review of the timelock contract belongs to A27; wallet thresholds to `skill://a30-custody-compliance--multisig-setup-plan`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `audit.report` (A27: findings on admin, upgrade or timelock logic) and the `contract.deploy.plan` (A26: which role is held by the timelock, current delay). Record the present `delay_seconds`, who can propose, execute and cancel (OpenZeppelin roles `PROPOSER_ROLE`, `EXECUTOR_ROLE`, `CANCELLER_ROLE`, `DEFAULT_ADMIN_ROLE`), and whether queued operations expire. Any open audit finding on these roles is carried into the plan as a blocker until A27 closes it.
2. Establish the inputs: `value_at_risk_usd` (the most the timelock can release; from the task), `response_seconds` (detect + decide + cancel: monitoring alert latency, human decision time and cancel-transaction confirmation, each taken from the task or A28/A13 figures, never guessed), `canceller_count`, `guardian_can_cancel`, `admin_renounced` (true only when the admin is renounced or is the timelock itself), `execution_window_seconds` (0 means no expiry; Compound's `Timelock` uses a 14-day grace period).
3. Propose `delay_bands` as `[{min_value_usd, min_delay_seconds}]`, first band at 0. This skill's starting proposal: 86400 s from 0 USD, 172800 s from 1,000,000 USD, 604800 s from 100,000,000 USD. Adopt the project's own bands when the task gives them.
4. Call `custody_timelock_check` with all eight fields: `delay_seconds`, `value_at_risk_usd`, `delay_bands`, `response_seconds`, `canceller_count`, `guardian_can_cancel`, `admin_renounced`, `execution_window_seconds`. `fail` findings: `no-delay` (blocker), `band-gap-below-lowest`, `delay-below-required`, `delay-shorter-than-response`, `no-cancel-path`, `admin-not-renounced` (major). `warn` findings: `single-canceller`, `no-expiry`. Both `fail` and `warn` need a change or a written acceptance.
5. Cross-check the `custody_timelock_check` output by hand: `required_delay_seconds` is the largest `min_delay_seconds` among bands with `min_value_usd` at or below the value (0 when none applies); `delay_margin_seconds` equals `delay_seconds - max(required_delay_seconds, response_seconds)`; `delay_hours` equals `delay_seconds / 3600` to 2 places. A difference is E-CONTRACT: state no verdict.
6. Call `custody_timelock_check` again with `value_at_risk_usd` doubled (growth case). A margin that turns negative there goes into the plan as a re-check trigger with the USD figure.
7. `write` `custody/<name>.timelock.json` as `{"timelock": {<step 4 input>}}`, then run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json`; its `timelock:<id>` findings must match step 4.
8. For any change to a live timelock call `shared_escalate` with `{task_id, reason_code: "policy", summary: "live timelock change needs L3 approval; execution is L4", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the policy file, then see Handover.

## Outputs

- `custody.policy.set` (timelock policy JSON, band table, growth-case result, accepted warnings).

## Failure Handling

- INVALID_INPUT (two bands with the same `min_value_usd`, a value above its maximum): fix the named field once, then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no tool verdict means no `pass`.
- `response_seconds` unknown: BLOCKED needs=input; a delay sized without it is not evidence. Exact product limits (minimum and maximum delay of a given timelock version) are not checked by the tool: defer to the contract and A26.

## Handover

- `to_agent`: `A26` (it configures the contract). `goal`: deploy with this delay and role set; `decisions`: bands, accepted warnings, growth trigger; `artifacts`: policy file with digest; `open_questions`: unknown response time; `blockers`: `human-approval` for live changes; `verification`: verified only if all cross-checks agreed.
- End with `shared_handover_validate`, then `shared_handover_write`.
