---
name: a26-smart-contract--upgradeable-proxy-design
description: Use when a new contract must be upgradeable and a proxy pattern (UUPS, Transparent, Beacon) has to be chosen, written and checked before any deployment.
owner: a26-smart-contract
tools: [sol_upgrade_safety, sol_selector_check, sol_storage_layout, sol_access_matrix, sol_deploy_plan_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- The blueprint requires upgradeability for a contract that does not yet exist on any chain. Design and code are L2.
- Changing a contract that is already deployed behind a proxy is `skill://a26-smart-contract--storage-layout-migration`; executing any upgrade on a live network is L3 (testnet-only simulation here) and mainnet is L4, human only. Review of the result is A27's.

## Procedure

1. `read` the `architecture.blueprint`; choose the pattern and write the reason into the plan. Transparent proxy: admin calls never reach the implementation, OpenZeppelin 5 deploys a dedicated `ProxyAdmin` per proxy; UUPS (ERC-1822 over ERC-1967): upgrade logic and `_authorizeUpgrade` live in the implementation, cheaper per call, but an implementation without it can never be upgraded again; Beacon: many proxies share one implementation behind one `UpgradeableBeacon`. Prefer UUPS for a single contract, Beacon only when more than one proxy needs the same upgrade, Transparent when the upgrade path must not depend on implementation code.
2. Write the implementation with `edit`/`write`: no state initialised in the constructor and no inline initial values (`UPG-CONSTRUCTOR-STATE`, `UPG-INLINE-INIT`); the constructor only calls `_disableInitializers()` (annotate `/// @custom:oz-upgrades-unsafe-allow constructor`); one `initialize` with the `initializer` modifier calling every parent `__X_init`; no `selfdestruct`, no unguarded `delegatecall` in Solidity or assembly. Prefer ERC-7201 namespaced storage (a struct at `keccak256(abi.encode(uint256(keccak256("<ns>")) - 1)) & ~bytes32(uint256(0xff))`) over a new `uint256[50] __gap`.
3. Call `sol_upgrade_safety` with `source` (the file text, at most 100000 characters, one `contract` per call for large files) and `pattern` set to `uups`, `transparent` or `beacon`. `verdict` must be `safe`; read `findings` worst first: `UPG-INIT-UNGUARDED`, `UPG-SELFDESTRUCT` (since EIP-6780 it only deletes code created in the same transaction, the OpenZeppelin plugin still rejects it) and `UPG-UUPS-UNPROTECTED` are blockers; `UPG-DELEGATECALL`, `UPG-CONSTRUCTOR-STATE`, `UPG-INLINE-INIT` and, for `uups`, `UPG-NO-DISABLE-INITIALIZERS` are majors (minor for the other patterns); `UPG-IMMUTABLE` is major until the declaration is annotated `@custom:oz-upgrades-unsafe-allow state-variable-immutable`, then info (immutables live in implementation code, never in proxy storage).
4. Call `sol_access_matrix` with the same `source`. For UUPS the matrix must list `_authorizeUpgrade` under a named role (never `public_by_design`); `unprotected` must be 0. Cross-check against step 3: a `UPG-UUPS-UNPROTECTED` finding with a clean matrix (or the reverse) is E-CONTRACT; show both outputs.
5. Call `sol_selector_check` with `functions` = the implementation's public signatures and `other_functions` = the proxy-side ones (`upgradeToAndCall(address,bytes)` 0x4f1ef286, `admin()`, `implementation()` for a hand-written proxy, `initialize()` 0x8129fc1c). `SEL-PROXY-CLASH` is a blocker (the proxy would answer a call meant for the implementation), `SEL-PROXY-SHADOW` a major; `verdict` must be `clean`.
6. Record the baseline layout for later upgrades: `bash` `forge inspect <Contract> storage-layout --json`, saved as `storage/<Contract>.v1.json`. Call `sol_storage_layout` with `old_layout` and `new_layout` both set to it once, to prove the tooling accepts the layout (`verdict` `compatible`, `breaking` 0).
7. Write the test plan: proxy deployment via `ERC1967Proxy`/`TransparentUpgradeableProxy` in a `forge` test, second `initialize` reverts, bare implementation `initialize` reverts, an upgrade to a `V2` mock preserves state and rejects a non-authorised caller.
8. Call `sol_deploy_plan_check` with `is_proxy_upgrade` false, `network_kind` `testnet`, `simulated` true, `broadcast` false, `owner_kind` `multisig` or `timelock`, `signer_kind` not `raw_private_key`, plus `chain_id` and `expected_chain_id`. The proxy admin or upgrade authority is a multisig or timelock; `DEPLOY-OWNER-EOA` is a blocker on mainnet. A mainnet plan returns `DEPLOY-NO-APPROVAL` until a human approves (L4); never set `human_approval` yourself.
9. Call `shared_artifact_digest` for the patch, the v1 layout and the test report.

## Outputs

- `code.patch` (implementation, tests, v1 layout) and an upgrade-authority section for `contract.deploy.plan`; evidence: verdicts of steps 3 to 8.

## Failure Handling

- INVALID_INPUT: fix the named field (`pattern` outside the enum, `source` over 100000 characters, `multisig_threshold` above `multisig_signers`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing` and say "layout not verified".
- `sol_upgrade_safety` reads text, resolves no imports: inherited hazards from library bases are not seen; say so, and name the OpenZeppelin Upgrades `validateUpgrade` check as a human or CI step.
- Live proxy upgrades and mainnet deployment: BLOCKED needs=human-approval (L3/L4). Never retry a non-idempotent broadcast.

## Handover

- `to_agent`: `A09`; `A27` and `A11` as `open_questions` readers. `decisions`: pattern and reason, authority holder; `artifacts`: patch, layout, test report with digests; `verification`: step 3 to 8 verdicts.
- End with `shared_handover_validate`, then `shared_handover_write`.
