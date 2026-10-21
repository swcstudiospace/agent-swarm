---
name: "a28-chain-infra--node-incident-response"
description: "Use when an incident.alert reports a chain node, RPC provider or indexer behind or failing; classify the cause and set remediation with its approval level."
disable-model-invocation: true
---

# Node incident response

## When to Use

- A13 hands over an `incident.alert` that names a chain symptom: head not advancing, node far behind, 5xx or timeouts from a provider, indexer lag over its freshness SLO.
- Diagnosis from supplied measurements is L2. Restarting or reconfiguring a mainnet node is L3 (BLOCKED needs=human-approval). Wiping a data directory or any validator key action is L4. Declaring or closing the incident is A13's (`skill://a13-observability--incident-declare`).
- A planned rollout that broke is `skill://a28-chain-infra--hardfork-preparation`; a bridge that is insolvent or paused is `skill://a28-chain-infra--l2-bridge-monitoring`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` to take the `incident.alert`: incident id, symptom, start time, chain id, affected node or provider labels. Missing chain id or start time is BLOCKED needs=input; never invent an incident id.
2. Call `chain_network_guard` with the affected `environment`, `operation: "node_change"` (the remediation you will propose), `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`. `required_level` L3 means every action below is a proposal for a human; `rpc-chain-mismatch` is itself the diagnosis (the endpoint serves another chain).
3. Separate chain from node with `chain_indexer_lag`, using samples of a reference head (a second, independent source named in the task) as `head_block` and the affected node's own head as `indexed_block`, with `block_time_s` and `freshness_slo_s`. `head-stalled` (major) means the reference itself is not advancing: chain halt or source problem, not this node. `indexer-stalled` (blocker) with a moving reference means the node is stuck. `lag-growing` means it syncs slower than the chain grows. Re-run with a third source when two disagree.
4. Rule out resources with `chain_node_sizing` using measured `disk_used_gb`, `disk_provisioned_gb`, `peak_iops`, `provisioned_iops`, `memory_gb`, `cpu_cores` and the other required fields: `disk-nearly-full` (over 90%), `iops-saturation` (over 70%) and `hdd` explain a node that cannot keep up.
5. For provider symptoms call `chain_rpc_failover_plan` with the live measurements: a provider listed in `ineligible` (`error-rate`, `block-lag`) is the cause; consumers move to the next of `order`, and `survives_single_loss` false means a second outage is a user outage. Cross-check with step 3: a provider far behind the reference must show `block-lag`.
6. When several nodes fail together call `chain_client_diversity` for `layer` `execution` and `consensus` with failing and healthy nodes labelled by client and version (`name` like `client@version`). All failures in one label points to a client bug, and a bug in a client above 1/3 of the network is a chain-level event: `shared_escalate` immediately with `reason_code: "other"`.
7. Choose the remediation per cause from the `chain_node_sizing`, `chain_rpc_failover_plan` and `chain_client_diversity` results and mark its level: disk full, restart or config change (L3 on mainnet, L2 on testnet); switch consumers to the next provider (L3 where it changes a production gateway); resync from snapshot after the old volume is kept aside (L3); wiping a data directory or touching validator keys (L4, with `skill://a28-chain-infra--validator-failover-safety` first). State what evidence would change the cause.
8. `write` `chain/incidents/<incident_id>.md` (timeline, the three-source comparison, cause, levels, rollback) and call `shared_artifact_digest`. Then `shared_escalate` with `reason_code: "policy"`, `summary`, `needs: ["human-approval"]` for every L3 or L4 action.

## Outputs

- `chain.infra.plan` section for the incident (cause, evidence per tool, proposed actions with level) and a recommendation for A13's timeline.

## Failure Handling

- INVALID_INPUT (samples out of order, `indexed_block` above `head_block`: your node cannot be ahead of the reference, so swap or re-sample): fix the field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name the sources missing. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Two sources that disagree on the head: E-CONTRACT with both values; the cause stays "undetermined".
- Restarts, resyncs and key actions are non-idempotent: never retry them and never run them.

## Handover

- `to_agent`: `A11` (executes the approved remediation); the assigner `A13` gets the evidence in `completed`. `goal`: remediate per plan; `decisions`: cause and rejected causes; `blockers`: `human-approval`; `artifacts`: report with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/node-incident-response.md; edit the source and run scripts/build_agents.py -->
