---
name: "a26-smart-contract--access-control-roles"
description: "Use when a contract needs an owner or role model (Ownable2Step, AccessControl) with every state-changing function guarded, evented and tied to a signer policy."
disable-model-invocation: true
---

## When to Use

- New privileged functions, a role redesign, or `audit.findings` / `review.verdict` naming an unprotected writer. Code is L2.
- Pause-specific roles are `skill://a26-smart-contract--pausable-circuit-breaker`; upgrade authority is `skill://a26-smart-contract--upgradeable-proxy-design`; the review of centralization risk is A27's. Who holds the keys is the `custody.policy.set` of A30, never decided here.

## Procedure

1. `read` the blueprint, the `custody.policy.set` (A30) and, when present, `audit.findings`; call `shared_open_findings` with `{task_id}` and keep the `blocking` ones. List every state-changing function and name the least privilege that may call it. Result: a table `function -> role -> holder kind (multisig, timelock, contract)`.
2. `read` the OpenZeppelin version (`grep -n openzeppelin remappings.txt`) and pick the primitive. One administrator: `Ownable2Step` (OpenZeppelin 5: `transferOwnership` then `acceptOwnership`, so a typo cannot hand over control). Several duties: `AccessControl` with one role constant per duty (`keccak256("MINTER_ROLE")`), `DEFAULT_ADMIN_ROLE` is `0x00` and administers all roles; use `AccessControlDefaultAdminRules` (delayed, two-step admin transfer) for a powerful admin. No `tx.origin`, no role held by an EOA on a non-local network, no role granted in a constructor to `msg.sender` unless the deploy plan hands it to the multisig in the same script.
3. `edit` the guards (`onlyOwner`, `onlyRole(X)`, custom modifiers); sensitive setters emit an event with old and new value; `renounceRole`/`renounceOwnership` is deliberately exposed or overridden to revert, and the choice is recorded.
4. Call `sol_access_matrix` with `source` and `public_by_design` (only names the blueprint calls public, for example `transfer`, `deposit`). Read `roles` (each role with its `functions`), `state_changing`, `guarded`, `unprotected`; `verdict` must be `pass`. Finding ids: `ACCESS-UNPROTECTED-PRIVILEGED` (blocker: names with mint, upgrade, pause, ownership, role grant/revoke, sweep, rescue, drain, authorize, emergency), `ACCESS-UNPROTECTED-ADMIN` (major: set, update, add, remove, change, enable, disable, configure, withdraw, execute, migrate, register, whitelist, freeze verbs), `ACCESS-UNPROTECTED-WRITER` (minor: list in `public_by_design` or guard it), `ACCESS-INITIALIZER-OPEN` (minor: deploy and initialise atomically), `ACCESS-UNVERIFIED-CALL-BEFORE-GUARD` (major: an unqualified call before the first guard to a name this source does not define). `fallback` and `receive` are entry points: one that writes an owner, admin or role variable with no guard is a blocker. A `require` or `assert` is a guard only when its condition is a `msg.sender` check against a principal (`msg.sender == X`, `hasRole(X, msg.sender)`, an allow-list lookup such as `allowed[msg.sender]`) or an OR chain of such checks; any non-sender term in the OR (`|| publicSale`) is not a guard. `if (msg.sender != X) revert` is a guard. `msg.sender != address(0)`, `tx.origin` (not a principal), `nonReentrant` and `whenNotPaused` are not access control.
5. Cross-check step 1 against the `sol_access_matrix` output of step 4: every function in your table must appear under the intended role in `roles`; a function the matrix calls guarded by a different role, or one missing from the matrix, is E-CONTRACT (an imported modifier is read by its name; a modifier defined in this source counts only when its body checks the caller, so a custom guard the reader cannot recognise needs `public_by_design` plus a test).
6. Call `sol_event_coverage` with `source`, `min_coverage_pct` 100 for privileged functions (use `exempt` only for names in the blueprint) and read `coverage_pct`, `missing`, `delegated`. `EVENT-MISSING` on a role or owner change blocks; indexers and monitors depend on `RoleGranted`, `RoleRevoked`, `OwnershipTransferStarted`.
7. Write negative tests first in `forge`: each guarded function reverts for a random address (`vm.prank`), `acceptOwnership` from a third party reverts, the last admin cannot be dropped by accident. Run `python3 scripts/sol_contract_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <contracts dir> --json` and fix `SOL-TX-ORIGIN` and `SOL-DELEGATECALL` hits.
8. Call `sol_deploy_plan_check` for the role hand-over: `owner_kind` `multisig` (or `timelock`), `owner_address` EIP-55 checksummed, `multisig_threshold` and `multisig_signers` from the policy (a 1-of-N multisig on mainnet is `DEPLOY-MULTISIG-THRESHOLD`, major), `network_kind` `testnet`, `simulated` true, `broadcast` false. `DEPLOY-OWNER-EOA` blocks mainnet.
9. Call `shared_artifact_digest` for the patch, the role table and the matrix output.

## Outputs

- `code.patch` (guards, events, tests) and the role table in the `contract.deploy.plan` owner section; evidence: matrix, event and plan verdicts.

## Failure Handling

- INVALID_INPUT: fix the field (`min_coverage_pct` outside 0 to 100, threshold above signers) or BLOCKED needs=input. No `custody.policy.set`: BLOCKED needs=input for holder kinds; never invent signers.
- PARTIAL_SUCCESS: `completed_fraction`, name the contracts not analysed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Granting or revoking a role on a live network moves control, not funds, but is irreversible in effect: BLOCKED needs=human-approval (L3 on a proxy admin, L4 on mainnet). The tools do not read chain state.

## Handover

- `to_agent`: `A09`, with `A10` and `A27` as consumers of `code.patch`. `decisions`: primitive and role table; `open_questions`: holder addresses from A30; `verification`: matrix, event, plan verdicts.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/access-control-roles.md; edit the source and run scripts/build_agents.py -->
