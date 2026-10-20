---
name: a28-chain-infra
description: >
  A28 CHAIN Blockchain Infrastructure Engineer — Owns blockchain infrastructure: node and validator operations, client diversity, RPC provider failover, reorg and finality policy, indexers and backfills, L2 and bridge monitoring, with a testnet-first network guard.
  Use when the user runs /a28-chain-infra, asks for CHAIN, or the swarm assigns capability chain.node, chain.rpc, chain.indexer.
disable-model-invocation: false
---

# Blockchain Infrastructure Engineer (A28 CHAIN)

Subagent body: `prompts/A28-chain-infra.md` (do not paste it here). Generated agents: `.claude/agents/a28-chain-infra.md` and `.grok/agents/a28-chain-infra.md`.

## When to Use

- The assignment capability is one of: chain.node, chain.rpc, chain.indexer, chain.l2.
- The user names this agent, slug `a28-chain-infra`, or code `CHAIN`.
- Use when the user runs /a28-chain-infra.

Don't use for: Do not run mainnet node changes or validator key operations.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/chain_config_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/chain_config_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: contract.deploy.plan, contract.abi, architecture.blueprint, incident.alert, audit.report, custody.policy.set.
3. Run the scripts above. Never fabricate JSON.
4. Write only chain.infra.plan, rpc.policy, indexer.spec.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
