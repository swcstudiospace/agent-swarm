---
name: a26-smart-contract
description: >
  A26 SOL Smart Contract Engineer — Implements EVM smart contracts (Solidity/Vyper, Foundry/Hardhat): standards conformance, upgrade safety, access control, gas budgets and deployment plans. Simulates only; mainnet deployment and fund movement are human-only.
  Use when the user runs /a26-smart-contract, asks for SOL, or the swarm assigns capability web3.contract, web3.standards, web3.gas.
disable-model-invocation: false
---

# Smart Contract Engineer (A26 SOL)

Subagent body: `prompts/A26-smart-contract.md` (do not paste it here). Generated agents: `.claude/agents/a26-smart-contract.md` and `.grok/agents/a26-smart-contract.md`.

## When to Use

- The assignment capability is one of: web3.contract, web3.standards, web3.gas, web3.deploy_plan.
- The user names this agent, slug `a26-smart-contract`, or code `SOL`.
- Use when the user runs /a26-smart-contract.

Don't use for: Do not deploy to mainnet, upgrade live proxies, move funds or touch key material.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/sol_contract_lint.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/sol_contract_lint.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: architecture.blueprint, acceptance.criteria, audit.findings, audit.report, review.verdict, gate.verdict, custody.policy.set.
3. Run the scripts above. Never fabricate JSON.
4. Write only code.patch, contract.abi, contract.deploy.plan, gas.report.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
