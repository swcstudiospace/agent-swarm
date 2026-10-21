---
name: "a26-smart-contract--storage-layout-migration"
description: "Use when an implementation behind a deployed proxy changes (new variables, new version) and old and new storage layouts must be proven compatible."
disable-model-invocation: true
---

## When to Use

- A `code.patch` touches state variables, base contracts, `__gap`, or an initializer of a contract that is live behind a proxy (or about to be). Analysis and code are L2; running the upgrade on a live proxy is L3 (L4 on mainnet), a human step; this agent only simulates on a fork or local node.
- First design of an upgradeable contract is `skill://a26-smart-contract--upgradeable-proxy-design`; an independent upgrade review is A27 `skill://a27-contract-audit--upgradeability-review`.

## Procedure

1. Fix the two commits: the deployed one (tag, release record, or the `contract.deploy.plan` that produced it) and the new head. Call `shared_git_head` with `{scan: true}` for `sha`. The old layout must come from the deployed source, never from the working tree: `bash` `git worktree add <scratch dir> <old sha>`, then `forge inspect <Contract> storage-layout --json` there. A missing deployed commit is BLOCKED needs=input.
2. Produce the same JSON for the new head. Map each `storage[]` entry to `{label, slot, offset, type, bytes, contract}`: `slot` as an integer (forge prints a string), `type` the solc id such as `t_uint256`, `bytes` the `numberOfBytes` from the `types` table, `contract` the declaring contract (distinguishes shadowed labels). Libraries with ERC-7201 namespaced structs do not appear in this list: their struct fields are compared by reading the struct definitions with `grep -n` and `read`.
3. Call `sol_storage_layout` with `old_layout` and `new_layout`. Read `old_variables`, `new_variables`, `appended`, `breaking`, `verdict` (`compatible` or `incompatible`). Blockers: `STORAGE-MOVED`, `STORAGE-REPLACED`, `STORAGE-COLLISION`, `STORAGE-GAP-GREW`, `STORAGE-GAP-SHRUNK` (a gap may not shrink), a new variable that straddles the old layout end (starts inside it, ends past it), and `STORAGE-TYPE-CHANGED` when the size changes. Majors: `STORAGE-REMOVED`, `STORAGE-GAP-REMOVED`, `STORAGE-APPENDED-AFTER-GAP` (shifts every inheriting contract unless the new variable's `contract` differs from the gap's), same-size retypes. Allowed: `STORAGE-APPENDED` (info), `STORAGE-GAP-CONSUMED` (info: new variables take slots from `__gap` and its end stays put, so the slots are `gap_size - used`), `STORAGE-RENAMED` (minor: confirm the meaning is unchanged). A gap is an array labelled `__gap` or `__gap_*`; `address` and contract types are one storage type, `address payable` is not. Cross-check: `old_variables` equals the entry count of the old JSON and `new_variables - old_variables - appended` accounts for the replaced ones.
4. Call `sol_upgrade_safety` with the new `source` and `pattern` (`uups`, `transparent` or `beacon`, the deployed one). `verdict` `safe` required: a new constructor that sets state, an inline initial value, `selfdestruct`, or an `_authorizeUpgrade` without access control each block. Cross-check with step 3: a variable the layout tool shows as newly appended with an inline initial value (`UPG-INLINE-INIT`) will be zero in the proxy: write a `reinitializer(n)` function, where `n` is greater than every earlier version, and test it.
5. Call `sol_abi_diff` with the released and new ABI; `verdict` `breaking` needs `skill://a26-smart-contract--abi-stability-gate` first. Selector clashes with the proxy come from `skill://a26-smart-contract--upgradeable-proxy-design` step 5.
6. Write the upgrade test in `forge`: deploy V1 behind the proxy, set state, upgrade to V2 (`upgradeToAndCall` with the `reinitializer` calldata), assert every V1 getter returns the old values, new variables hold their migrated values, a non-admin cannot upgrade, and `reinitializer` cannot run twice. Run it on a fork of the target network only through an alias of `[rpc_endpoints]` that reads an environment variable.
7. Call `sol_deploy_plan_check` with `is_proxy_upgrade` true, `network_kind`, `chain_id`, `expected_chain_id`, `simulated` true, `broadcast` as intended, `owner_kind` `multisig` or `timelock`, `signer_kind` not `raw_private_key`, `constructor_types` `[]`, and `human_approval` only as quoted by the task. On mainnet `DEPLOY-NO-APPROVAL` stays a blocker until a human grants it; mark the plan L3 (L4 on mainnet) and do not retry.
8. `write` `reports/<task_id>-layout.md` (both commits, the tool outputs, test evidence, migration steps in order) and call `shared_artifact_digest` for it, the new layout JSON and the patch.

## Outputs

- `code.patch` (V2, `reinitializer`, upgrade test), the layout report as evidence and the upgrade section of `contract.deploy.plan`.

## Failure Handling

- INVALID_INPUT: `slot` above 4294967296, `offset` above 31, an entry missing `bytes`: fix the mapping or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing` and the result is "layout not verified", never "compatible".
- The tools compare what you pass; they do not read the chain. State that the deployed layout is "from commit <sha>, not on-chain verified". Disagreement between layout tool and `forge` output is E-CONTRACT.
- Do not retry a live upgrade; BLOCKED needs=human-approval.

## Handover

- `to_agent`: `A27` (upgrade review) with `A09`, `A10`, `A11` as `code.patch` consumers. `blockers`: L3/L4 approval; `decisions`: gap use, `reinitializer` version; `verification`: layout, safety, ABI, test results.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/storage-layout-migration.md; edit the source and run scripts/build_agents.py -->
