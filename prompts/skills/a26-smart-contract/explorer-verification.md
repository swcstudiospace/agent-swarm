---
name: a26-smart-contract--explorer-verification
description: Use when a deployed contract must be verifiable on an explorer or Sourcify and a reproducible-build manifest with constructor args and settings is needed.
owner: a26-smart-contract
tools: [sol_pragma_policy, sol_deploy_plan_check, sol_abi_diff, sol_selector_check, shared_git_head, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A `contract.deploy.plan` marks `verify_source` true, or a human reports a deployed address that must be verified. Building and manifest writing are L2. Running verification against a mainnet explorer is part of the L4 human deployment step; testnet verification with the keyless Sourcify verifier may be run by this agent, with a human-supplied address.
- Not for deployment (`skill://a26-smart-contract--deploy-script-dry-run`) or for reading on-chain state (A28). An explorer API key is a credential: this agent never reads, prints or stores it; commands that need one are prepared for a human or CI that holds it as an environment variable.

## Procedure

1. `read` the `contract.deploy.plan` and call `shared_git_head` with `{scan: true}`. The commit `sha` must equal the plan's commit; a different head means the deployed bytecode and your build can differ: E-CONTRACT, stop and name both.
2. Call `sol_pragma_policy` with `pragmas` of every file in the build (`grep -n "pragma solidity"`), `min_version`, `require_pinned` true, `via_ir` and `optimizer_enabled` from `foundry.toml`. Verification needs one exact compiler, the same `optimizer_runs`, `via_ir`, `evm_version` and `bytecode_hash`/`cbor_metadata` settings that produced the deployed bytes; read them from `foundry.toml` with `read` and copy into the manifest. `verdict` `pass` required; `PRAGMA-KNOWN-BUG` is resolved before deployment, not after.
3. Reproduce the build: `bash` `forge build --force` twice, call `shared_artifact_digest` for `out/<File>.sol/<Contract>.json` after each. Different digests mean the build is not reproducible (absolute paths, unpinned dependencies, timestamps): fix before anything is verified. Record the `forge --version`.
4. Constructor arguments: take the values from the plan, encode them with `bash` `cast abi-encode "constructor(<types>)" <values>` (no function selector, no `0x` prefix when the explorer form asks for plain hex). Call `sol_deploy_plan_check` with the plan's `chain_id`, `expected_chain_id`, `network_kind`, `constructor_types`, `constructor_args`, `verify_source` true and the other plan fields; `constructor_args_ok` must be true and `DEPLOY-NOT-VERIFIED` absent. For a mainnet target the verdict still shows `DEPLOY-NO-APPROVAL` until a human approves: it is expected and left as is.
5. Compose the commands (never executed with a secret): `forge verify-contract <address> <path>:<Contract> --chain-id <id> --compiler-version <exact solc> --num-of-optimizations <runs> --constructor-args <hex> [--libraries <path>:<Lib>:<address>] --verifier sourcify` (keyless) or `--verifier etherscan` with the key supplied as the environment variable `ETHERSCAN_API_KEY` by the human, and `--watch`. A proxy needs two verifications: the implementation (its own constructor args, usually none) and the proxy contract; explorers then link them through the ERC-1967 slots. `write` them to `deploy/<task_id>-verify.md` with the settings table.
6. After the human reports the verified link, take the explorer's ABI JSON (supplied as a file) and map it to `{kind, signature, mutability, returns, indexed}`. Call `sol_abi_diff` with the local ABI as `old_abi` and the explorer ABI as `new_abi`: the verification is accepted only when `added`, `removed` and `changed` are all 0 and `verdict` is `compatible`. A mismatch means the explorer holds different source than the deployed commit.
7. Call `sol_selector_check` with `functions` from the verified ABI and, for a proxy, `other_functions` of the proxy contract: `verdict` `clean`; cross-check that `function_count` equals the ABI function count from step 6. A Sourcify result is a "full (perfect) match" only when metadata hashes agree; a "partial match" (metadata differs, bytecode equal) is reported as partial, not as verified.
8. `write` the result (`status`: `prepared`, `submitted`, `verified-full`, `verified-partial`, `failed`; the link a human supplied; digests) into the plan file and call `shared_artifact_digest` for it. Claim `verified` only with a link or `forge verify-check` output you were given.

## Outputs

- A verification manifest appended to `contract.deploy.plan` (commit, compiler settings, encoded constructor args, commands, status) and, once supplied, the verified `contract.abi`.

## Failure Handling

- INVALID_INPUT: `constructor_args` that do not parse as their `constructor_types`, non-scalar types: fix from the plan or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE (`forge`, `cast`, the explorer): retry once, then `skipped:tool-missing`; a rejected submission is `failed` with the explorer's message, resubmission is a new human decision.
- Verification submissions are not retried automatically; flag semantics (`--compiler-version` format, API versions of Etherscan) change: the explorer's current docs and the tool output win over the flags written here.
- Mainnet submissions: BLOCKED needs=human-approval (L4).

## Handover

- `to_agent`: `A12` (release evidence), with `A27`, `A28`, `A30` named. `blockers`: missing deployed address or approval; `verification`: build digests equal, ABI diff 0/0/0.
- End with `shared_handover_validate`, then `shared_handover_write`.
