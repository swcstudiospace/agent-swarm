---
name: a30-custody-compliance--recovery-drill
description: Use when a custody.recovery task must judge recovery drill recency, outcome and RTO per scenario, test shard survival and plan the next drills.
owner: a30-custody-compliance
tools: [shared_handover_read, custody_recovery_drill, custody_shard_distribution, custody_ceremony_checklist, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Recovery drill

## When to Use

- A `custody.recovery` task asks whether backup, shard and restore procedures have been proven recently enough, or asks for the next drill plan.
- You assess records and plan. Executing a drill touches key material and is human-only (L4); a drill on a production wallet or with funds is never proposed as a first step: use a testnet or staging wallet with the same signer topology.
- An actual loss or leak is `skill://a30-custody-compliance--key-compromise-playbook`; ceremony steps are `skill://a30-custody-compliance--key-ceremony-plan`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `release.plan` (A12: dates that constrain drill windows) and take from the task the required scenarios, the last drill records and the RTO target. Scenarios are exactly `signer_key_loss`, `signer_compromise`, `custodian_unavailable`, `site_loss`, `full_restore`. No RTO target is BLOCKED needs=input.
2. Call `custody_recovery_drill` with `as_of` (UTC timestamp with a zone), `max_age_days` (this skill's proposal: 180; use the policy figure when given), `target_rto_minutes`, `required_scenarios` and `drills: [{scenario, last_drill_at, passed, measured_rto_minutes}]` (at most one per scenario). Status per scenario, in precedence: `never` (major `never-drilled`), `failed` (blocker `drill-failed`: recovery is unproven), `rto_exceeded` (major, measured above target), `overdue` (major, age above `max_age_days`), else `current`.
3. Cross-check the `custody_recovery_drill` output: `counts` sum to `required_count`, which equals the number of distinct `required_scenarios`; `age_days` is -1 exactly for `never`; each other `age_days` equals `as_of` minus `last_drill_at` in days (2 places). A deviation is E-CONTRACT: state no verdict.
4. For `custodian_unavailable` and `site_loss`, test survival of the secret shares: call `custody_shard_distribution` with the real `threshold` and the `shards` that remain after removing every shard of the largest custodian (then of the largest country). `INVALID_INPUT` on `threshold` (threshold exceeds the remaining shards) means the secret cannot be rebuilt in that loss: record a blocker. A result with `single-custodian-quorum` or `single-organization-quorum` on the remainder is a blocker as well.
5. For each scenario that is not `current`, list its restore steps: call `custody_ceremony_checklist` with `ceremony_type: "recovery_restore"`, `min_witnesses` (the policy value, else 2) and `items: []` to get its 7 items (`verdict` is `fail` by construction; use `required_items` and `missing_items`). A finished drill is scored by the same call with the humans' record: `completion_pct` 100 and `verdict: pass` are required to count it as passed.
6. `write` `custody/recovery/<plan_id>.md`: per non-current scenario the drill date (earliest, blockers first, outside any release freeze), the test wallet and chain (testnet first), who performs and witnesses, the success criteria (RTO at or below the target, restore verified against the public key only) and the abort rule. State plainly that no production keys or funds are used and no step is automated.
7. Run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json` and require `secret_findings: 0`; a drill record that holds key-shaped text is a blocker.
8. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "recovery drills need L4 human execution", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the plan, then see Handover.

## Outputs

- `custody.evidence.pack` section: scenario status table, shard survival results and the drill plan.

## Failure Handling

- INVALID_INPUT (drill dated after `as_of`, a scenario twice, an unknown scenario): fix the named record once, then BLOCKED needs=input. The deliberate INVALID_INPUT of step 4 is a finding, not a tool fault.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; unverified scenarios stay unproven.
- The 180-day figure is a proposal; defer to policy and to the tool output. Never retry a failed drill step automatically.

## Handover

- `to_agent`: `A12` (it plans the human drill windows); `A10` for a failed-drill blocker. `goal`: schedule and witness the listed drills; `decisions`: limits and order; `artifacts`: plan with digest; `blockers`: `human-approval`, any unrecoverable-shard finding; `verification`: partial until drills are scored.
- End with `shared_handover_validate`, then `shared_handover_write`.
