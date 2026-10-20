---
name: "a28-chain-infra--node-sync-and-snapshot"
description: "Use when a chain.node task sizes or rebuilds an Ethereum-style node, choosing sync mode, snapshot or checkpoint source, disk, memory and client mix."
disable-model-invocation: true
---

# Node sync and snapshot

## When to Use

- A `chain.node` task provisions, resyncs or resizes a full or archive node (execution plus consensus client) and wants a `chain.infra.plan`.
- A testnet node is L2. Touching a mainnet node (resync, client swap, volume resize) is L3: BLOCKED needs=human-approval with this plan attached. Generic VM, disk or Kubernetes work stays with A11.
- Staying in sync before a fork is `skill://a28-chain-infra--hardfork-preparation`; a node that has fallen behind right now is `skill://a28-chain-infra--node-incident-response`.

## Procedure

1. `read` the task for `node_type` (`full` or `archive`), `sync_mode` (`snap` or `full`), `validating` (the host also runs validators), `disk_kind` (`nvme`, `ssd`, `network_ssd`, `hdd`), the measured `disk_used_gb`, `disk_provisioned_gb`, `growth_gb_per_day`, `memory_gb`, `cpu_cores`, `uplink_mbps`, `peak_iops`, `provisioned_iops`. Measured values only; an unmeasured field is BLOCKED needs=input.
2. Call `chain_network_guard` with `environment`, `operation: "node_change"`, `target_chain_id`, `mainnet_approved`, `approved_level: "L2"`. `required_level` is L2 for a testnet and L3 for mainnet-grade or unknown ids; `decision: needs_approval` means the plan may be written but not executed.
3. Call `chain_node_sizing` with the step 1 values and `horizon_days` (180 unless the task says otherwise). Archive with `snap` is rejected (INVALID_INPUT on `sync_mode`: an archive node syncs in full mode from genesis), so fix `sync_mode`, never the verdict.
4. Read the output: `required_disk_gb` is what to provision (projected use at the horizon is 80% of it, which keeps room for compaction, pruning and resync); `disk-horizon` (major) means projected use passes 80% of the current volume within `horizon_days`, and `days_until_80pct` says when; `disk_fill_pct` above 90 is `disk-nearly-full` (blocker); `iops_utilization_pct` above 70 is `iops-saturation` (the 80%, 90% and 70% limits are this tool's policy, not protocol values). `hdd` is a blocker, `network_ssd` a minor finding. The floors in the findings are the minimum requirements of the ethereum.org node guide (2 cores, 16 GB, 2 TB NVMe, 25 Mbit/s, compared with `uplink_mbps`); 8 cores and 64 GB for a validating host are its recommendation (EIP-7870). The guide describes an Ethereum full node: an archive node or another chain needs its own client documentation.
5. Cross-check the `chain_node_sizing` arithmetic: `projected_disk_gb` must equal `disk_used_gb + growth_gb_per_day * horizon_days` and `required_disk_gb` must equal `projected_disk_gb / 0.8` (to rounding). A difference is E-CONTRACT.
6. Choose the sync source in the plan: execution client snap sync (the default in Geth) for a full node, consensus client checkpoint sync from a finalized state published by a source named in the task (verify the finalized root against a second source before trusting it). Engine API (default port 8551) between the clients is authenticated by a JWT secret file: cite its path, never print it. A volume snapshot is acceptable only when its chain id and client version are recorded.
7. Call `chain_client_diversity` once per layer (`layer: "execution"`, then `"consensus"`) with `clients: [{name, nodes}]` for the whole fleet including this node. More than 1/3 on one client is minor, more than 1/2 major, 2/3 or more a blocker (`supermajority:<client>`); prefer a minority client for new nodes when a share is above 1/3.
8. `write` `chain/plans/<task_id>.md` with sizing, sync source, client choice, guard result, rollback (keep the old volume until the new node is within 2 blocks of head for 24 hours) and the L3 step list. Call `shared_artifact_digest` with that path.

## Outputs

- `chain.infra.plan` for the node (sizing, sync source, diversity, `network_guard` block, rollback).

## Failure Handling

- INVALID_INPUT: fix the named field once (for example `disk_provisioned_gb` below `disk_used_gb`), else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction` and the layer or metric not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Client flags and database sizes change between releases: where a figure is not in tool output, defer to the client's current documentation and say so; never invent a chain size.
- Resyncs and volume deletes are not retried and never executed here; delete is L4.

## Handover

- `to_agent`: `A11` (provisions the host) and `A30` when validator keys share the host (key policy). `goal`: provision per plan on testnet first; `artifacts`: plan with digest; `blockers`: `human-approval` on mainnet; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/node-sync-and-snapshot.md; edit the source and run scripts/build_agents.py -->
