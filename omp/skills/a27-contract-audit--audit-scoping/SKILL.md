---
name: "a27-contract-audit--audit-scoping"
description: "Use when an audit.static task starts and the audited commit, in-scope contracts, trust model and review order must be fixed before any finding is written."
disable-model-invocation: true
---

## When to Use

- First step of every audit task, and again when a `code.patch` changes the audited commit (then re-scope, do not patch the old manifest).
- Inputs: `contract.abi`, `contract.deploy.plan` (from A26), `architecture.blueprint` (A03), `gas.report` (A26), the repository at the commit to audit.
- It produces a scope manifest, not findings. Finding work is `skill://a27-contract-audit--static-analysis-triage`; fix re-checks are `skill://a27-contract-audit--fix-verification-reaudit`. The signed gate stays with A10.
- Everything here is L2 (read-only analysis). You never edit contract source.

## Procedure

1. Read the task inputs with `read`: the ABI, the deploy plan, the blueprint, the gas report. No deploy plan or ABI: BLOCKED `needs=input`; never infer roles or addresses.
2. Pin the commit with `bash`: `git rev-parse HEAD` (full 40 hex; a short id is rejected later by `audit_report_completeness`) and `git status --porcelain` for the source tree. A dirty tree is BLOCKED `needs=input`: an audit covers one immutable commit.
3. Inventory with `glob` (`src/**/*.sol` or the layout in `foundry.toml`/`hardhat.config.*`). List vendored libraries as out of scope with their pinned version (`bash`: `git submodule status`, or `package.json`). Count lines per file with `bash` (`wc -l`); never quote a figure you did not count.
4. Tag attack surface with `grep -n` for `delegatecall|selfdestruct|ecrecover|latestRoundData|getReserves|slot0|initializer|_authorizeUpgrade|ERC4626|flashLoan|permit|tx.origin|\.call\{`. Map each hit to a signal of `audit_classify_vulnerability` and call it with `observations` = `[{id, signal, location}]` (ids unique). Use `counts` and `swc_ids` only to order the review (critical signals first); `total` must equal the number of observations sent.
5. Call `audit_role_centralization` with `roles` = `[{name, holder, powers, multisig_threshold, multisig_signers, timelock_delay_s, bypasses_timelock}]` taken from the deploy plan. `highest_severity` high or critical, or `critical_exposures` of 1 or more, makes `skill://a27-contract-audit--access-control-review` and `skill://a27-contract-audit--upgradeability-review` priority one.
6. Cross-check ABI against source: every state-changing function in the ABI must have a `grep` hit in the in-scope files. An ABI entry with no source, or a role in the plan with no matching modifier, is E-CONTRACT: stop and report both sides.
7. Select the review lanes with `grep` results from the surface tags: oracle (`skill://a27-contract-audit--oracle-review`), vault or AMM math (`skill://a27-contract-audit--economic-attack-modeling`), signatures (`skill://a27-contract-audit--access-control-review`), loops over user-growable arrays with the gas report (`skill://a27-contract-audit--gas-dos-review`), external calls (`skill://a27-contract-audit--reentrancy-review`).
8. `write` the manifest to the path the task names, default `audit/scope.json` (a convention of this skill): `scope_commit`, `in_scope` (path, lines), `out_of_scope`, `assumptions` (e.g. admin keys honest or not), `trust_model` (the step 5 roles), `review_order`. Call `shared_artifact_digest` with `path` for it.

## Outputs

- Scope manifest with the full `scope_commit`, the in-scope list, the trust model and the review order: the input of `audit.findings`, `audit.report` and `invariant.suite`.
- Cited evidence: `audit_classify_vulnerability` `counts`, `audit_role_centralization` `highest_severity`.

## Failure Handling

- INVALID_INPUT: fix the named field (`observations` id duplicated, `roles` threshold above signers) or BLOCKED `needs=input`.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` and order the review by `grep` hits only.
- Two sources disagree (ABI vs source, plan vs modifiers): E-CONTRACT, no manifest until resolved.
- A request to widen scope after findings exist: re-scope with a new `scope_commit`; never silently extend.

## Handover

- `to_agent`: the assigner (`A01` unless the task names another). `goal`: audit scope for the commit. `completed`: steps run. `decisions`: in/out of scope and review order. `artifacts`: `audit/scope.json` with its digest. `open_questions`: missing inputs, assumptions to confirm. `verification`: verified only when the step 6 cross-check agreed.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/audit-scoping.md; edit the source and run scripts/build_agents.py -->
