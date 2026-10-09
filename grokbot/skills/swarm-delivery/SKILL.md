---
name: swarm-delivery
description: >
  Grok Bot lane delivery for a02-requirements, a03-architect, a04-ux-designer.
  Use when Desk Lead routes work to this lane of the 15 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane delivery

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `delivery`.
- The role slug is one of: a02-requirements, a03-architect, a04-ux-designer.

## Procedure

1. Read the role prompt named below. The swarm has exactly these 15 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts with `python3 scripts/<script>.py --json` (the bun twin is `scripts/ts/<script>.ts`). Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A02 REQ (a02-requirements)

- Prompt: `prompts/A02-requirements.md`
- Role: Senior Requirements Engineer (A02 REQ, slug a02-requirements) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A02-requirements.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for requirements.spec, user.stories, acceptance.criteria.
- Home: `executor`. Seat: `bot-00-programming-lead`.
- Autonomy ceiling: scope_change=L3, spec=L2.
- Scripts: `scripts/req_lint.py`.

### A03 ARCH (a03-architect)

- Prompt: `prompts/A03-architect.md`
- Role: Senior Solution Architect (A03 ARCH, slug a03-architect) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A03-architect.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for architecture.blueprint, api.contract, adr.set, tech.stack.
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: adr=L2, breaking_contract=L3.
- Scripts: `scripts/arch_adr.py`, `scripts/arch_contract_check.py`.

### A04 UXD (a04-ux-designer)

- Prompt: `prompts/A04-ux-designer.md`
- Role: Senior UX Designer (A04 UXD, slug a04-ux-designer) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A04-ux-designer.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for design.system.tokens, ux.spec, a11y.requirements.
- Home: `bot-01-systems-backend`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: brand_change=L3, spec=L2.
- Scripts: `scripts/ux_tokens.py`.
