---
name: swarm-code
description: >
  Grok Bot lane code for a05-backend, a06-frontend, a07-data, a19-event-driven, a26-smart-contract, a29-dapp-wallet.
  Use when Desk Lead routes work to this lane of the 30 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane code

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `code`.
- The role slug is one of: a05-backend, a06-frontend, a07-data, a19-event-driven, a26-smart-contract, a29-dapp-wallet.

## Procedure

1. Read the role prompt at `$SWARM_ROOT/<prompt>` below. `$SWARM_ROOT` is the pinned agent-swarm checkout. The target repo does not contain these prompts. The swarm has exactly these 30 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Among role globs, the longest match wins, and an equal length goes to the lowest role id. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts from the pinned checkout: `python3 "$SWARM_ROOT/scripts/<script>.py" --root <target repo> --json` (the bun twin is `bun "$SWARM_ROOT/scripts/ts/<script>.ts" --root <target repo> --json`). `<target repo>` is the git toplevel being edited. Do not run a `scripts/` path from the target tree. Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A05 BE (a05-backend)

- Prompt: `$SWARM_ROOT/prompts/A05-backend.md`
- Role: Senior Backend Engineer (A05 BE, slug a05-backend) in AgentSwarm. Execute the assignment using repository evidence from agents/A05-backend.md, agents.json, and the scripts and tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for backend source + unit tests (code.patch).
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: code=L2, contract_deviation=L3.
- Scripts: `$SWARM_ROOT/scripts/code_checks.py`, `$SWARM_ROOT/scripts/be_contract_conformance.py`.

### A06 FE (a06-frontend)

- Prompt: `$SWARM_ROOT/prompts/A06-frontend.md`
- Role: Senior Frontend Engineer (A06 FE, slug a06-frontend) in AgentSwarm. Execute the assignment using repository evidence from agents/A06-frontend.md, agents.json, and the scripts and tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for frontend source + component tests (code.patch).
- Home: `bot-02-web-edge`. Seat: `bot-02-web-edge`.
- Autonomy ceiling: code=L2, design_deviation=L3.
- Scripts: `$SWARM_ROOT/scripts/code_checks.py`, `$SWARM_ROOT/scripts/fe_a11y_check.py`.

### A07 DATA (a07-data)

- Prompt: `$SWARM_ROOT/prompts/A07-data.md`
- Role: Senior Data Engineer (A07 DATA, slug a07-data) in AgentSwarm. Execute the assignment using repository evidence from agents/A07-data.md, agents.json, and the scripts and tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for schema.migration, data.contract, data.model.
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: destructive_ddl=L4, migration=L2.
- Scripts: `$SWARM_ROOT/scripts/data_migration_check.py`.

### A19 EVENT (a19-event-driven)

- Prompt: `$SWARM_ROOT/prompts/A19-event-driven.md`
- Role: Senior Event-Driven &amp; Serverless Engineer (A19 EVENT, slug a19-event-driven) in AgentSwarm. Execute the assignment using repository evidence from agents/A19-event-driven.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for event.topology.spec, event.schema.set and serverless.config; code.patch is a shared implementation artifact that A05, A06, A19, A26 and A29 each write for their own lanes.
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: breaking_event_schema=L3, event_design=L2, prod_replay_or_backfill=L3.
- Scripts: `$SWARM_ROOT/scripts/event_schema_check.py`.

### A26 SOL (a26-smart-contract)

- Prompt: `$SWARM_ROOT/prompts/A26-smart-contract.md`
- Role: Senior Smart Contract Engineer (A26 SOL, slug a26-smart-contract) in AgentSwarm. Execute the assignment using repository evidence from agents/A26-smart-contract.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for contract.abi, contract.deploy.plan and gas.report; code.patch is a shared implementation artifact that A05, A06, A19, A26 and A29 each write for their own lanes.
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: code=L2, fund_moving=L4, mainnet_deploy=L4, proxy_upgrade=L3.
- Scripts: `$SWARM_ROOT/scripts/sol_contract_lint.py`.

### A29 DAPP (a29-dapp-wallet)

- Prompt: `$SWARM_ROOT/prompts/A29-dapp-wallet.md`
- Role: Senior dApp &amp; Wallet Integration Engineer (A29 DAPP, slug a29-dapp-wallet) in AgentSwarm. Execute the assignment using repository evidence from agents/A29-dapp-wallet.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for dapp.integration.spec and wallet.flow.spec; code.patch is a shared implementation artifact that A05, A06, A19, A26 and A29 each write for their own lanes.
- Home: `bot-02-web-edge`. Seat: `bot-02-web-edge`.
- Autonomy ceiling: code=L2, mainnet_enable=L3, signing_flow_change=L3.
- Scripts: `$SWARM_ROOT/scripts/dapp_wallet_check.py`.
