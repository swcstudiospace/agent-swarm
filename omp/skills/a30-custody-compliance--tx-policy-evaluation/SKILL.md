---
name: "a30-custody-compliance--tx-policy-evaluation"
description: "Use when a custody.compliance task asks if one transaction descriptor may proceed under custody policy; returns allow, hold or deny and never signs."
disable-model-invocation: true
---

# Transaction policy evaluation

## When to Use

- A task hands you one transaction descriptor (chain, destination, value, operation, 4-byte selector, approvals so far, recent spend) and asks for a policy decision.
- The output is a decision record only. A decision of `allow` means policy permits asking for signatures; collecting them and broadcasting is L4 and human or signer-service work. Never accept a signed transaction, calldata containing secrets, a key or a credentialed RPC URL.
- Authoring the policy is `skill://a30-custody-compliance--signer-policy-authoring`; screening records are `skill://a30-custody-compliance--sanctions-screening-process`.

## Procedure

1. Collect the nine descriptor fields from the task: `chain_id`, `to` (42-character address; a mixed-case address must carry a valid EIP-55 checksum), `value_usd`, `operation` (`call` or `delegatecall`), `selector` (empty or `0x` plus 8 hex digits), `approvals`, `destination_listed_seconds`, `spent_24h_usd`, `tx_count_1h`, and the `policy` object from the `custody.policy.set`. A missing field is BLOCKED needs=input; never default a USD value or a count.
2. Confirm the policy itself is sound: call `custody_signer_policy_lint` with the ten lint fields taken from the same policy (`per_tx_limit_usd`, `daily_limit_usd`, `balance_usd`, `velocity_max_tx_per_hour`, `allowlist_enforced`, `allowlist_cooloff_seconds`, `allowlist_reattest_days`, `signing_threshold`, `policy_change_approvals`, `approval_expiry_seconds`). Their limits must equal the `policy` object's; a major finding is reported next to the decision, because a decision under a failing policy is weak evidence.
3. Call `custody_tx_policy_eval` with the nine fields plus `policy: {allowed_chain_ids, allowlist, denylist, per_tx_limit_usd, daily_limit_usd, hold_above_usd, required_approvals, cooloff_seconds, max_tx_per_hour, allow_delegatecall}`. Rules: `deny` (blocker findings) for `chain-not-allowed`, `destination-zero-address`, `destination-denylisted`, `delegatecall-blocked`, `per-tx-limit-exceeded` (value above the limit) and `daily-limit-exceeded` (`spent_24h_usd + value_usd` above the limit); `hold` (major) for `destination-not-allowlisted`, `destination-cooloff`, `manual-review-threshold` (value at or above `hold_above_usd`), `risky-selector`, `insufficient-approvals`, `velocity-exceeded`; else `allow`.
4. Cross-check the result: `decision` is `deny` exactly when `deny_count` is above 0; `hold` when `hold_count` is above 0 and `deny_count` is 0; `deny_count + hold_count` equals the number of `findings`. Read `selector_signature`: the tool names `approve`, `increaseAllowance`, `permit`, `setApprovalForAll`, `transferOwnership`, `acceptOwnership`, `renounceOwnership`, `grantRole`, `revokeRole`, `upgradeTo`, `upgradeToAndCall` and the Safe management calls `addOwnerWithThreshold`, `removeOwner`, `swapOwner`, `changeThreshold`, `enableModule`, `disableModule`, `setGuard`, `setFallbackHandler` (all risky: `hold`; a Safe signer-set or module change is an L3 signer change) and `transfer`, `transferFrom` (plain); the table is fixed and sees only the 4-byte selector, never calldata. `unknown` is not a finding, so ask A26 for the ABI and treat it as hold-for-review until named.
5. Probe the boundary with `custody_tx_policy_eval` once more: `value_usd` equal to `per_tx_limit_usd` (must not deny on that rule) and then a cent more (must deny). A different result means the tool and the policy disagree: E-CONTRACT.
6. For `allow` or `hold` to an external party, call `custody_sanctions_screening` with the screening record of the destination (`as_of`, `max_list_age_hours`, `max_screening_age_hours`, `screenings`). Anything but a fresh `clear` with action `none` or `released` downgrades `allow` to `hold`; a confirmed match is `deny` and a compliance escalation.
7. `write` the decision record `custody/decisions/<tx_ref>.json` (descriptor, policy digest, `decision`, finding ids, screening result; no calldata beyond the selector) and call `shared_artifact_digest` on it. A `deny` has no override here: a policy exception is an L3 signer-policy change; for `hold` call `shared_escalate` with `{task_id, reason_code: "policy", summary: "transaction held for human review", needs: ["human-approval"]}`.

## Outputs

- `custody.evidence.pack` entry: one decision record with `decision`, reasons and screening result.

## Failure Handling

- INVALID_INPUT (address both listed and denylisted, bad checksum, malformed `selector`): fix that field once, or BLOCKED needs=input; never "correct" an address yourself.
- DEPENDENCY_UNAVAILABLE on any tool: retry once, then `skipped:tool-missing`; the decision is then `hold`, never `allow`. PARTIAL_SUCCESS: use `completed_fraction`.
- Chain ids and USD values come from the task; do not fetch prices or call a node.

## Handover

- `to_agent`: the assigning agent (`A28` or `A29`, else `A01`); `A10` when a `deny` suggests abuse. Fill `goal`, `completed`, `decisions` (decision and finding ids), `artifacts` (record with digest), `blockers` (`human-approval` on hold), `verification`.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/tx-policy-evaluation.md; edit the source and run scripts/build_agents.py -->
