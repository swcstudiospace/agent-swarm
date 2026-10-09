---
name: swarm-code
description: >
  Grok Bot lane code for a05-backend, a06-frontend, a07-data.
  Use when Desk Lead routes work to this lane of the 15 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane code

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `code`.
- The role slug is one of: a05-backend, a06-frontend, a07-data.

## Procedure

1. Read the role prompt named below. The swarm has exactly these 15 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts with `python3 scripts/<script>.py --json` (the bun twin is `scripts/ts/<script>.ts`). Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A05 BE (a05-backend)

- Prompt: `prompts/A05-backend.md`
- Role: Senior Backend Engineer (A05 BE, slug a05-backend) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A05-backend.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for backend source + unit tests (code.patch).
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: code=L2, contract_deviation=L3.
- Scripts: `scripts/code_checks.py`, `scripts/be_contract_conformance.py`.

### A06 FE (a06-frontend)

- Prompt: `prompts/A06-frontend.md`
- Role: Senior Frontend Engineer (A06 FE, slug a06-frontend) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A06-frontend.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for frontend source + component tests (code.patch).
- Home: `bot-02-web-edge`. Seat: `bot-02-web-edge`.
- Autonomy ceiling: code=L2, design_deviation=L3.
- Scripts: `scripts/code_checks.py`, `scripts/fe_a11y_check.py`.

### A07 DATA (a07-data)

- Prompt: `prompts/A07-data.md`
- Role: Senior Data Engineer (A07 DATA, slug a07-data) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A07-data.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for schema.migration, data.contract, data.model.
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: destructive_ddl=L4, migration=L2.
- Scripts: `scripts/data_migration_check.py`.
