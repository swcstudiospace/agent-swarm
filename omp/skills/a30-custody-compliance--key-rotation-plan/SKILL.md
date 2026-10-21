---
name: "a30-custody-compliance--key-rotation-plan"
description: "Use when a key.ceremony task needs a rotation schedule from key age versus maximum age, with a safe add-then-remove order that keeps the multisig within policy."
disable-model-invocation: true
---

# Key rotation plan

## When to Use

- A `key.ceremony` task covers a key inventory with creation or last-rotation dates and a maximum age, or a signer replacement after a departure.
- The schedule is L2. Changing a signer on a live wallet is L3 (BLOCKED needs=human-approval); the new key's generation is an L4 ceremony. Never read, move or destroy key material.
- A suspected leak is `skill://a30-custody-compliance--key-compromise-playbook`, not a scheduled rotation. The ceremony script itself is `skill://a30-custody-compliance--key-ceremony-plan`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `release.plan` (A12: freeze windows and dates) and collect the inventory from the task: `key_id` (a label), `created_at`, `last_rotated_at` (empty string when never rotated) and `max_age_days` per key. Missing dates are BLOCKED needs=input.
2. When the policy gives no maximum, propose per class for `custody_key_rotation` and mark it a proposal: 90 days for software or mobile signers, 365 for hardware signers, 730 for cold root keys. These are this skill's defaults (NIST SP 800-57 describes cryptoperiods only as guidance); the project's own figures win.
3. Call `custody_key_rotation` with `as_of` (UTC timestamp with a zone, for example `2026-10-20T00:00:00Z`; get it from `date -u +%Y-%m-%dT%H:%M:%SZ` via `bash`), `warn_days` (30 unless the task says otherwise) and `keys: [{key_id, created_at, last_rotated_at, max_age_days}]`. Age counts from `last_rotated_at` when set, else `created_at`. `overdue` is age above maximum (major), above twice the maximum a blocker; `due_soon` is `days_remaining` at or below `warn_days` (minor).
4. Cross-check each `custody_key_rotation` row, remembering that `age_days` and `days_remaining` are each rounded to 2 decimals: `age_days + days_remaining` equals `max_age_days` within 0.01; `status: overdue` implies `days_remaining` at most 0 (a key overdue by under 7.2 minutes shows 0.0) and `status: ok` implies `days_remaining` at least `warn_days`; compare nothing for equality at those boundaries; `overdue_count + due_soon_count + ok_count` equals `key_count`. A larger mismatch is E-CONTRACT. Order the work by `days_remaining` ascending, blockers first.
5. For each key to rotate, call `custody_ceremony_checklist` with `ceremony_type: "key_rotation"`, `min_witnesses` and `items: []` to list its 9 steps (the verdict is `fail` by construction; use `missing_items`). The on-chain order is fixed by those steps: new key added before the old one is removed, then rotation verified on-chain. On Safe this is `swapOwner`, or `addOwnerWithThreshold` then `removeOwner`.
6. Prove the intermediate and final signer sets with `custody_multisig_policy` (`threshold`, `signers`): once with the new signer added and the old still present (n + 1 signers), once after removal. Both must be `pass` or an accepted `warn`. Rotate at most `loss_tolerance` signers at once, preferably one per window, and never inside a freeze window from step 1.
7. `write` `custody/rotation/<plan_id>.md` with the ordered table (key label, status, window, ceremony id, approver role) and run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json`; `secret_findings` must be 0.
8. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "signer change needs L3 approval; ceremonies are L4", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the plan, then see Handover.

## Outputs

- `key.ceremony.plan` for the rotation (schedule, per-key ceremony steps, interim and final signer-set checks).

## Failure Handling

- INVALID_INPUT (`last_rotated_at` before `created_at`, a date after `as_of`, a duplicate `key_id`, a key-shaped label): fix that field once, then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the keys not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- An interim signer set that fails `custody_multisig_policy`: redesign the order; never accept a weaker interim state silently. Rotation steps are non-idempotent: never retry them.

## Handover

- `to_agent`: `A12` (it schedules the L3/L4 steps). `goal`: execute the rotation in this order with human approval; `decisions`: maxima used, order, windows; `artifacts`: plan with digest; `blockers`: `human-approval`; `open_questions`: missing dates; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/key-rotation-plan.md; edit the source and run scripts/build_agents.py -->
