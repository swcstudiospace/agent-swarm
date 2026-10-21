---
name: "a28-chain-infra--testnet-mainnet-guard"
description: "Use when a chain task names a target chain id and environment and needs the network guard decision and required autonomy level before planning or mainnet."
disable-model-invocation: true
---

# Testnet and mainnet guard

## When to Use

- Start of every `chain.*` task and again whenever the target chain id, environment or operation changes. Every plan A28 writes carries the resulting `network_guard` block (testnet by default).
- The guard run is L0 to L2. Mainnet node changes are L3, validator key operations L4 (key policy is A30's). This agent never broadcasts a mainnet transaction; deploying contracts to mainnet is A26's plan and a human's L4 step.
- This skill decides whether a target may be touched at all. Provider order is `skill://a28-chain-infra--rpc-provider-failover`; a fork rollout is `skill://a28-chain-infra--hardfork-preparation`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` for the `contract.deploy.plan` (A26), the `architecture.blueprint` (A03) and the `custody.policy.set` (A30) and collect: target `environment` (`local`, `testnet`, `staging`, `production`), `target_chain_id`, and every planned `operation`: `read`, `node_change` or `validator_key_op`. No chain id in the sources is BLOCKED needs=input; never default to 1.
2. Run `python3 scripts/chain_config_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <config dir> --json`. Collect the chain ids it finds; each must equal `target_chain_id`. Findings `chain-id-name-mismatch` (a testnet name, including the deprecated Mumbai, Ropsten, Rinkeby and Kovan, on a mainnet or unknown id), `chain-id-conflict`, `rpc-host-chain-mismatch` (a test-network host on a mainnet or unknown id) and `mainnet-without-opt-in` all stop the task, as does `scan-incomplete` (major): a file the script could not analyse is not clean. Exit 1 is a finding, exit 2 a bad `--path`.
3. Call `chain_network_guard` per operation with `environment`, `operation`, `target_chain_id`, `rpc_chain_id` (the `eth_chainId` value the task recorded; omit when none and say so), `mainnet_approved` and `approved_level`. Set `mainnet_approved` true only when the task quotes a named human approval; set `approved_level` to what the task granted, at most `L2` for this agent's own authority.
4. Read the result. `network_class` is `mainnet`, `testnet`, `local` or `unknown`; unknown chain ids count as mainnet-grade. `required_level` is L0 for `read`, L2 for a testnet `node_change`, L3 for a mainnet-grade one and L4 for any `validator_key_op`. `decision`: `allow` goes ahead; `needs_approval` means only approvals are missing (queue it, BLOCKED needs=human-approval); `block` means the target is wrong (`environment-mismatch`: a non-production environment points at mainnet; `production-on-testnet`; `rpc-chain-mismatch`: signatures would bind to another chain through EIP-155).
5. Call `shared_open_findings` with `{task_id}` when the target is mainnet-grade and read the failing gate findings: any `blocking` finding from the audit (A27 `audit.report`) or the security gate means the mainnet step must not be queued; list them by `id`.
6. Cross-check the script against the guard: a recorded approval (`mainnet_approved` true) with the script still reporting `mainnet-without-opt-in` means the config lacks its opt-in key (for example `allowMainnet`): report E-CONTRACT and ask the owner for the config change, never edit it. The reverse (opt-in key set, no recorded approval) is also E-CONTRACT; the guard input wins.
7. `write` the `network_guard` block into `chain/plans/<task_id>.md`: `{environment, target_chain_id, network_class, chain_name, required_level, decision, approvals, script counts}`; call `shared_artifact_digest` with that path.
8. For `needs_approval` or `block` call `shared_escalate` with `reason_code: "policy"`, `summary`, `needs: ["human-approval"]` and use its `result_patch` as the BLOCKED result. Never raise `approved_level` yourself to turn a block into an allow.

## Outputs

- A `network_guard` block inside `chain.infra.plan` (decision per operation, required level, evidence of approvals, script counts).

## Failure Handling

- INVALID_INPUT (`target_chain_id` outside 1 to 2^53-1, `approved_level` not L0 to L4): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the operations not checked. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; an unchecked guard is never `allow`.
- The chain table in the tool is a reference list; a chain it does not know is mainnet-grade until a human classifies it, and the script's testnet-name and host checks treat it the same way.
- Approvals are non-idempotent records: never reuse one for another chain id or operation.

## Handover

- `to_agent`: the assigner (A01 unless the task names another) when blocked, else `A11` or `A26` per consumer of the plan. `goal`: proceed with the listed operations or supply approvals; `decisions`: guard decisions; `artifacts`: plan with digest; `blockers`: `human-approval`; `verification`: verified when script and guard agree.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/testnet-mainnet-guard.md; edit the source and run scripts/build_agents.py -->
