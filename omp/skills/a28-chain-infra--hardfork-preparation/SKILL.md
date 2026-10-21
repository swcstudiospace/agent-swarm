---
name: "a28-chain-infra--hardfork-preparation"
description: "Use when a chain.node task prepares a fleet for a scheduled hard fork: fork-ready clients, testnet rehearsal, rollout waves, activation timing, rollback limits."
disable-model-invocation: true
---

# Hardfork preparation

## When to Use

- A `chain.node` task names an upcoming fork (execution and consensus names together, for example the Prague and Electra pair known as Pectra) and the fleet must run fork-ready client releases before activation.
- Testnet rehearsal is L2; the mainnet upgrade of any node is L3 (BLOCKED needs=human-approval); validator host changes follow `skill://a28-chain-infra--validator-failover-safety` and are L4.
- Routine resizing is `skill://a28-chain-infra--node-sync-and-snapshot`; a node that fell off the chain at activation is `skill://a28-chain-infra--node-incident-response`.

## Procedure

1. `read` the fork notice and the client release notes the task cites: fork name, the minimum release per client and layer, the activation point (an epoch on the consensus layer, a timestamp on the execution layer since Shanghai) and the testnet dates. Fork names, dates and versions are never invented: a missing one is BLOCKED needs=input; ones from memory are unverified.
2. Cross-check the activation: the execution timestamp must equal `genesis_time + epoch * 32 * 12` (Ethereum: 32 slots of 12 s per epoch, so 384 s) using the `genesis_time` from the chain config. Convert with `bash` (`date -u -d @<timestamp>`). A difference is E-CONTRACT: report both values and plan nothing until the source agrees.
3. Call `chain_network_guard` with `operation: "node_change"` for the testnet target (`environment: "testnet"`, a testnet `target_chain_id`, `approved_level: "L2"`; expect `required_level: L2`, `allow`), and again for the mainnet target (`environment: "production"`, `mainnet_approved` as recorded, `approved_level` as granted; expect `required_level: L3`, usually `needs_approval`).
4. Run `python3 scripts/chain_config_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <node config dir> --json`. Status `fail`, an unrelated chain id (`chain-id-name-mismatch`) or `mainnet-without-opt-in` on a testnet file stops the rehearsal.
5. Call `chain_client_diversity` for each `layer` with the fleet's nodes per client. The largest client's `share_pct` sets the waves: no wave upgrades more than a third of the nodes of any layer, so the largest client needs at least 3 waves and the network keeps serving; a `supermajority` finding is also a reason to rehearse on the minority client first.
6. Call `chain_node_sizing` with the rehearsal node's measured values (`disk_used_gb`, `growth_gb_per_day`, `horizon_days` of 90, and the rest) after the upgrade on testnet: the new release may change disk or memory needs. `verdict: fail` blocks the mainnet step.
7. Set the gates (defaults of this skill; the task may override): testnet fork passed with every rehearsal node healthy for 24 hours; mainnet nodes upgraded and at head at least 24 hours before activation; no change inside the last hour before activation. Rollback is a downgrade to the previous release and only before activation: after the fork the old release rejects the new chain, so only forward fixes exist.
8. `write` `chain/plans/<task_id>.md` (versions, activation cross-check, waves, gates, rollback limit, guard results) and call `shared_artifact_digest` with it. For the mainnet waves call `shared_escalate` with `reason_code: "policy"`, `summary: "mainnet node upgrade needs L3 approval"`, `needs: ["human-approval"]`.

## Outputs

- `chain.infra.plan` for the fork (rollout waves, gates, rollback limit, `network_guard` blocks for testnet and mainnet).

## Failure Handling

- INVALID_INPUT: fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the layers not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Sepolia and Hoodi are the Ethereum public testnets the guard table lists; Holesky and Goerli are deprecated. Confirm the current list against the client release notes before rehearsing; defer to them when they disagree.
- A client release that does not exist yet blocks the plan: never assume one.
- Upgrades are not retried blindly: re-check head and version first.

## Handover

- `to_agent`: `A11` (rolls out) and `A30` when validator hosts are touched; write to A11, list A30 in `remaining`. `goal`: execute the waves after approval; `blockers`: `human-approval`; `decisions`: gates and wave sizes; `artifacts`: plan with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/hardfork-preparation.md; edit the source and run scripts/build_agents.py -->
