---
name: "a27-contract-audit--upgradeability-review"
description: "Use when a proxy or upgradeable contract (Transparent, UUPS, Beacon, Diamond) needs its initializer, storage layout, upgrade authority and timelock reviewed."
disable-model-invocation: true
---

## When to Use

- Any in-scope contract behind a proxy, or a `code.patch` that changes an implementation, a storage variable or an initializer.
- Who holds ordinary admin roles is `skill://a27-contract-audit--access-control-review`; this skill covers who can replace code and how safely.
- Review is L2. Executing an upgrade on any network is never yours: mainnet upgrades are an L4 human step, the plan is `contract.deploy.plan` from A26.

## Procedure

1. Identify the pattern with `grep -n`: `TransparentUpgradeableProxy`, `UUPSUpgradeable` / `_authorizeUpgrade` (ERC-1822, ERC-1967), `BeaconProxy`, diamond `diamondCut` (ERC-2535), or a hand-written `delegatecall`. Read the ERC-1967 slots in use: implementation `0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc`, admin `0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103`. A custom slot instead of these needs a written reason.
2. Initializers, checked with `grep -n` and `read`: the implementation constructor must call `_disableInitializers()`; `initialize` carries `initializer` (or `reinitializer(n)` with n increasing); every parent `__X_init` is called; no state is set in a constructor of the implementation. Record each miss with its location.
3. Storage layout against the previous version: `forge inspect <Contract> storage-layout --json` at the old and the new commit (`bash`), then diff slot, offset, type, label with `read`. Variables inserted, reordered, or retyped are findings; appended ones are fine; an inherited base gaining variables without shrinking `__gap` is a finding. If the project uses OpenZeppelin Upgrades `validateUpgrade`, run it and cite its output; it does not replace the diff.
4. UUPS, checked with `grep -n`: `_authorizeUpgrade` must be access-controlled and must exist in every new implementation (an implementation without it bricks upgrades). The implementation must not contain `selfdestruct` or unguarded `delegatecall` (signals `unprotected-selfdestruct`, `delegatecall-untrusted`).
5. Call `audit_classify_vulnerability` with `observations` = `[{id, signal, location}]` for the hits of steps 2 to 4 (an unprotected `initialize` maps to the closest registry class `default-visibility`; say so in the finding text). Take `counts` and `swc_ids`; `total` equals the observations sent.
6. Upgrade authority: call `audit_role_centralization` with `roles` = `[{name, holder, powers: ["upgrade_implementation"], multisig_threshold, multisig_signers, timelock_delay_s, bypasses_timelock}]` from the deploy plan. The base severity is critical; each mitigation (multisig of at least 3 of 5 with a majority, timelock of at least 172800 s that cannot be bypassed, or governance) lowers it one step. Read `highest_severity` and `critical_exposures`; an EOA holder is critical.
7. Score each confirmed finding with `audit_score_severity` (`loss_usd`, `tvl_usd`, `attacker_privilege`, `preconditions`, `irrecoverable`: true for bricked upgrade paths or selfdestructed implementations). Cross-check: the severity of the authority finding must not be below the `audit_role_centralization` effective severity without an `audit_score_severity` basis; a difference is reported, not resolved (L3).
8. A proposed override of any severity is BLOCKED: call `shared_escalate` with `task_id`, `reason_code` `policy`, `summary`, `needs` `["human-approval"]` and return its `result_patch`. `write` `audit/upgradeability-review.md` and digest it with `shared_artifact_digest`.

## Outputs

- Upgradeability part of `audit.findings`: initializer, storage, authority and selfdestruct/delegatecall findings with locations and scored severity; the layout diff as appendix evidence.

## Failure Handling

- INVALID_INPUT (multisig threshold above signers, missing `multisig_*` for a multisig): fix from the deploy plan or BLOCKED `needs=input`.
- No previous commit or layout to diff: say "layout not verified", finding severity at most what the code shows; never assume compatibility.
- DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing`.
- The tools do not read chain state; actual proxy admin and timelock values come from the deploy plan and are labeled "planned, not on-chain verified".

## Handover

- `to_agent`: `A26` (fixes and the deploy plan). `artifacts`: review file and layout diff with digests. `decisions`: findings and the authority verdict. `blockers`: pending L3. `verification`: partial when layout was not diffed.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/upgradeability-review.md; edit the source and run scripts/build_agents.py -->
