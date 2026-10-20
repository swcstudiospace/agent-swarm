---
name: a30-custody-compliance--signer-policy-authoring
description: Use when a custody.policy task needs per-tx and daily limits, allowlist cool-off, velocity and approval-expiry rules for a signer, linted and probed.
owner: a30-custody-compliance
tools: [shared_handover_read, custody_signer_policy_lint, custody_tx_policy_eval, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Signer policy authoring

## When to Use

- A `custody.policy` task needs the rule set a signing service or policy engine enforces before a signature is requested: limits, allowlist, velocity, approvals.
- New policy text is L2. Putting a changed policy live on an operating signer is L3 (BLOCKED needs=human-approval). Signing or sending is never done here.
- Signer membership and thresholds are `skill://a30-custody-compliance--multisig-setup-plan`; judging one concrete transaction is `skill://a30-custody-compliance--tx-policy-evaluation`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `chain.infra.plan` (A28: where the signer service runs, which chains) and take the wallet's `balance_usd` and the multisig `signing_threshold` from the task or the `custody.policy.set` already written. A missing balance or threshold is BLOCKED needs=input.
2. Draft the ten `custody_signer_policy_lint` values: `per_tx_limit_usd`, `daily_limit_usd`, `balance_usd`, `velocity_max_tx_per_hour`, `allowlist_enforced`, `allowlist_cooloff_seconds`, `allowlist_reattest_days`, `signing_threshold`, `policy_change_approvals`, `approval_expiry_seconds`. Targets of this skill: per-tx below daily below balance, `allowlist_enforced` true, cool-off at least 86400 s, re-attest at most 365 days, approval expiry at most 86400 s, `policy_change_approvals` at least `signing_threshold`. A limit of 0 means "none configured".
3. Call `custody_signer_policy_lint` with those ten fields. `verdict: fail` lists major findings (`no-per-tx-limit`, `no-daily-limit`, `allowlist-not-enforced`, `no-cooloff`, `weak-policy-change-quorum`, `approvals-never-expire`): fix the value and rerun. `warn` lists minor ones (`short-cooloff`, `per-tx-limit-not-binding`, `no-velocity-limit`, `long-approval-expiry`, `allowlist-reattest-too-long`): accept each in writing or fix it. `rules_evaluated` below 14 is normal when the allowlist is off (cool-off rules are skipped).
4. Build the runtime policy object for `custody_tx_policy_eval`: `allowed_chain_ids`, `allowlist`, `denylist`, `per_tx_limit_usd`, `daily_limit_usd`, `hold_above_usd`, `required_approvals`, `cooloff_seconds`, `max_tx_per_hour`, `allow_delegatecall` (false by default). Reuse the lint numbers unchanged; `hold_above_usd` is below `per_tx_limit_usd` (a manual-review band).
5. Probe it with four descriptors (lowercase placeholder addresses from the task, `operation: "call"`, `selector: ""`): (a) listed address, value under all limits, enough `approvals`: expect `allow`; (b) unlisted address: expect `hold` with `destination-not-allowlisted`; (c) `value_usd` just above `per_tx_limit_usd`: expect `deny` with `per-tx-limit-exceeded`; (d) `destination_listed_seconds` below `cooloff_seconds`: expect `hold` with `destination-cooloff`. Any other `decision` means the object and the lint input diverge: E-CONTRACT, fix the object, do not paper over it.
6. `write` `custody/<wallet>.signer-policy.json` as `{"signer_policy": {<the lint input>}}`, then run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json`. Its `signer_policy:<id>` findings must equal the lint findings; exit 1 means major or blocker findings remain.
7. For a policy that replaces one already live, call `shared_escalate` with `{task_id, reason_code: "policy", summary: "signer policy change needs L3 approval", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the policy file, then see Handover.

## Outputs

- `custody.policy.set` (signer policy JSON plus the four probe results and the lint figures).

## Failure Handling

- INVALID_INPUT (a limit above its schema maximum, a negative value): fix the named field once, then BLOCKED needs=input. `custody_tx_policy_eval` also refuses an address that is both listed and denylisted and a mixed-case address with a bad EIP-55 checksum: correct the source address.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unprobed case. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; unprobed policy stays unverified.
- The 24 h and 365 d figures are lint defaults of the tool, not law; a regulator-set value overrides them with the source cited.

## Handover

- `to_agent`: `A28` (it deploys the policy in the signer infrastructure); `A26` when the policy is mirrored on-chain. `goal`: enforce this policy; `decisions`: accepted warnings with rationale; `artifacts`: policy file with digest; `blockers`: `human-approval` for a live change; `verification`: partial when a probe was skipped.
- End with `shared_handover_validate`, then `shared_handover_write`.
