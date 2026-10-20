---
name: "a26-smart-contract--deploy-script-dry-run"
description: "Use when a Foundry deploy script must be written and pre-flighted into a contract.deploy.plan by simulation only; mainnet broadcast stays a human L4 step."
disable-model-invocation: true
---

## When to Use

- A task asks for `contract.deploy.plan` (consumers A12 release, A27, A28, A30). Writing the script and simulating is L2; broadcasting to a testnet needs the human to hold the signer; mainnet deployment is L4, a live proxy upgrade L3, fund-moving calls L4. This agent never broadcasts.
- Not for verification on a block explorer (`skill://a26-smart-contract--explorer-verification`), for the layout check of an upgrade (`skill://a26-smart-contract--storage-layout-migration`), or for choosing signers and keys (A30 `custody.policy.set`).

## Procedure

1. `read` the blueprint, the `custody.policy.set` (A30) and, for a mainnet target, call `shared_open_findings` with `{task_id}`: any `blocking` finding from an audit (`audit.report`, A27) or gate (`gate.verdict` of A08, A09, A10, A12) means the plan is written but marked "not ready". Fix: target `network_kind` (`local`, `testnet`, `mainnet`), `expected_chain_id` (from the task, never defaulted), constructor types and values, owner kind, signer kind. Call `shared_git_head` with `{scan: true}` and keep `sha`; the plan is bound to that commit.
2. Write `script/Deploy.s.sol` (`edit`/`write`): `require(block.chainid == <expected id>, "wrong chain")` first; `vm.startBroadcast()` with no key in the source (the signer comes from the CLI: `--ledger`, `--trezor` or `--account <keystore name>`, never `--private-key`); constructor arguments read from named environment variables or a checked-in config file of public values; deployment, role hand-over to the multisig or timelock and `initialize` in one script run; a final `console.log` of addresses. The RPC is an alias from `[rpc_endpoints]` reading an environment variable, never a URL with a key.
3. Call `sol_pragma_policy` with `pragmas`, `min_version`, `require_pinned` true, `via_ir`, `optimizer_enabled` from `foundry.toml`. Deployed bytecode must come from a pinned compiler; `PRAGMA-NOT-PINNED`, `PRAGMA-BELOW-MIN` and `PRAGMA-KNOWN-BUG` all have to be resolved before the plan is ready.
4. Simulate: `bash` `forge script script/Deploy.s.sol --rpc-url <alias>` without `--broadcast` (a fork simulation of the target; Anvil, `forge test` with `vm.createSelectFork` are alternatives for a local rehearsal). The simulation must show the chain id, deployed addresses, gas used and no revert. `simulated` is true only when this exact script and commit passed; changing a line resets it.
5. Call `sol_deploy_plan_check` with `chain_id`, `expected_chain_id`, `network_kind`, `script_asserts_chain_id` (true only if step 2's guard is in the script), `simulated`, `broadcast` false (this agent never broadcasts; the human broadcast is a later L4 step written into the plan), `verify_source`, `constructor_types` (scalar canonical types only, no arrays or tuples) with `constructor_args`, `owner_kind`, `owner_address`, `multisig_threshold`, `multisig_signers`, `signer_kind`, `rpc_url` only as the credential-free endpoint or omitted, `is_proxy_upgrade`, and `human_approval` set to true only when the task quotes a named approval.
6. Read `blocking`, `verdict`, `findings`. Blockers: `DEPLOY-CHAIN-MISMATCH`, `DEPLOY-NETWORK-KIND-MISMATCH`, `DEPLOY-CTOR-ARITY`, `DEPLOY-CTOR-ARG`, `DEPLOY-OWNER-ZERO`, `DEPLOY-OWNER-CHECKSUM`, `DEPLOY-OWNER-EOA` (mainnet), `DEPLOY-RAW-KEY` (non-local), `DEPLOY-RPC-CREDENTIAL`. Majors: `DEPLOY-NOT-SIMULATED` and `DEPLOY-NOT-VERIFIED` (mainnet; minor on a testnet; `DEPLOY-NOT-SIMULATED` is a blocker only when `broadcast` is true on mainnet), `DEPLOY-NO-CHAIN-GUARD`, `DEPLOY-RPC-PATH-KEY` (an API key in the path or a query value), `DEPLOY-MULTISIG-THRESHOLD` (below 2 signatures on mainnet). Fix and re-run until only `DEPLOY-NO-APPROVAL` remains for a mainnet target; that finding is the correct end state, not a defect.
7. Cross-check the `sol_deploy_plan_check` result against the simulation: `network_name` and `chain_known` must match the chain id printed by `forge script`; the owner address in the plan equals the one the simulation hands roles to; `constructor_args_ok` true. A disagreement is E-CONTRACT, never resolved by editing the plan.
8. `write` `deploy/<task_id>-plan.md`: `sha`, `chain_id`, `network_kind`, the exact `forge script` command (no secrets), constructor values, owner and signer kinds, simulation evidence, tool verdict, and the human steps ("L4: broadcast with a hardware signer after named approval"). Call `shared_artifact_digest` for the plan and the script.
9. For a mainnet or live-upgrade target call `shared_escalate` with `reason_code: "policy"`, a summary and `needs: ["human-approval"]`; use its `result_patch` as the BLOCKED result.

## Outputs

- `contract.deploy.plan` (commit, command, parameters, simulation evidence, verdict) and `code.patch` for the script.

## Failure Handling

- INVALID_INPUT: `owner_address` not 42 characters, threshold above signers, a tuple in `constructor_types`: fix the field or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`, RPC for the fork): retry once, then `skipped:tool-missing`; a plan without simulation stays "not simulated".
- Broadcasts are non-idempotent and not yours: never retry, never run `--broadcast`. A `DEPLOY-CHAIN-UNKNOWN` chain is treated as mainnet-grade until a human classifies it.

## Handover

- `to_agent`: `A12` (release), with `A27`, `A28`, `A30` named. `blockers`: `human-approval` for mainnet; `artifacts`: plan, script digests; `verification`: tool verdict plus simulation result.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/deploy-script-dry-run.md; edit the source and run scripts/build_agents.py -->
