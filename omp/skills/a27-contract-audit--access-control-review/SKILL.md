---
name: "a27-contract-audit--access-control-review"
description: "Use when privileged roles, authorization checks or signature-based approvals (permit, meta-tx) must be reviewed for centralization risk and replay."
disable-model-invocation: true
---

## When to Use

- Any contract with `onlyOwner`, `onlyRole`, `AccessControl`, `auth`-style modifiers, or functions that accept a signature (EIP-2612 permit, EIP-712 orders, meta-transactions).
- Proxy upgrade authority and storage safety is `skill://a27-contract-audit--upgradeability-review`. Reentrancy ordering is `skill://a27-contract-audit--reentrancy-review`.
- Review is L2. A change to a computed severity is L3; accepting a centralization risk is L4 and not yours.

## Procedure

1. `grep -n` privileged entry points (`onlyOwner|onlyRole|requiresAuth|msg.sender ==|_checkRole|hasRole`) and every external state-changing function with none. List each with the state it changes. A state-changing function with no guard and no reason is `default-visibility` or `unprotected-ether-withdrawal` class.
2. Build the role table with `read` from code plus the deploy plan: name, holder (`eoa`, `multisig`, `governance`, `contract`, `renounced`), powers, `multisig_threshold`, `multisig_signers`, `timelock_delay_s`, `bypasses_timelock`. Map each power to the tool's vocabulary (`drain_funds`, `freeze_user_funds`, `mint_unbounded`, `pause_deposits`, `set_fee_capped`, `set_fee_uncapped`, `set_oracle`, `set_parameters_unbounded`, `upgrade_implementation`).
3. Call `audit_role_centralization` with `roles`. Base severities: drain, upgrade and unbounded mint critical; freeze, set oracle and uncapped fee high; unbounded parameters medium; pause and capped fee low. Each mitigation lowers one step: a multisig of at least 3 signers needed out of at least 5 and over half, governance, and a timelock of at least 172800 s that is not bypassed. Read `critical_exposures`, `highest_severity`, and the per-role `ROLE-<name>-<power>` findings.
4. Cross-check with `audit_role_centralization`: the number of powers you listed must equal `powers_assessed` (renounced roles are skipped, so subtract them); a difference means a role was dropped from the input.
5. For each signature consumer call `audit_signature_replay` with `scheme` (`eip712_typed`, `eip191_personal`, `raw_hash`), `nonce_consumed`, `chain_id_bound`, `contract_bound`, `deadline_in_message`, `deadline_enforced`, `zero_address_check`, `malleability_protected`, `domain_separator_cached`, `chainid_recomputed`, `multichain_deployment`. Read `replay_protected` (needs nonce, chain id and contract binding) and `finding_count`. Missing nonce, contract binding, unenforced deadline or an unchecked `ecrecover` zero address are high; a missing chain id is high on a multichain deployment, otherwise medium. Contradictory inputs (deadline enforced but not signed) are INVALID_INPUT.
6. Call `audit_classify_vulnerability` for the pattern hits (`tx.origin` as `tx-origin-auth`, `signature-replay`, `missing-signature-verification`, `signature-malleability`, `default-visibility`). Cross-check with step 5: a `signature-replay` signal must correspond to `replay_protected` false, otherwise one of the two inputs is wrong.
7. Score each finding with `audit_score_severity` (`attacker_privilege` `privileged` for role abuse, `permissionless` for an unguarded function; `preconditions`; `loss_usd` against `tvl_usd`). For role findings, if the scored severity is lower than `highest_severity`, keep the higher value in the report and list the difference as an open question; do not edit.
8. Recommendations only: OpenZeppelin `AccessControlDefaultAdminRules` or two-step ownership (`Ownable2Step`), a timelock of at least 48 h, multisig threshold, per-chain domain separators, `ECDSA.recover` from OpenZeppelin (rejects high-s). `write` `audit/access-control-review.md` and digest it with `shared_artifact_digest`.

## Outputs

- Access-control and signature part of `audit.findings`; the role table and the `audit_role_centralization` results as evidence.

## Failure Handling

- INVALID_INPUT: fix the named field (`roles` threshold above signers, a multisig without counts, a deadline enforced but not signed) from the deploy plan, or BLOCKED `needs=input`.
- A holder you cannot determine from code or plan is `unknown`: BLOCKED `needs=input`, never `renounced` by assumption.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Deploy-plan roles are intent, not on-chain state: label results "planned"; no RPC call is made by this skill.

## Handover

- `to_agent`: `A26`; `A10` when a gate waits. `artifacts`: review file with digest. `open_questions`: undetermined holders, severity differences. `blockers`: L3/L4 items. `verification`: verified when the step 4 count matched.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/access-control-review.md; edit the source and run scripts/build_agents.py -->
