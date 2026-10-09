---
name: swarm-sustain
description: >
  Grok Bot lane sustain for a14-maintenance, a15-docs.
  Use when Desk Lead routes work to this lane of the 15 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane sustain

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `sustain`.
- The role slug is one of: a14-maintenance, a15-docs.

## Procedure

1. Read the role prompt named below. The swarm has exactly these 15 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts with `python3 scripts/<script>.py --json` (the bun twin is `scripts/ts/<script>.ts`). Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A14 MAINT (a14-maintenance)

- Prompt: `prompts/A14-maintenance.md`
- Role: Senior Maintenance Engineer (A14 MAINT, slug a14-maintenance) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A14-maintenance.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for patch.task, debt.register, rca.report, dependency.bump.
- Home: `routine`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: major_bump=L3, patch_task=L2.
- Scripts: `scripts/maint_deps.py`.

### A15 DOC (a15-docs)

- Prompt: `prompts/A15-docs.md`
- Role: Senior Documentation Engineer (A15 DOC, slug a15-docs) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A15-docs.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for docs.bundle, api.reference, runbook, changelog.
- Home: `executor`. Seat: `bot-06-quality-security`.
- Autonomy ceiling: docs=L2, public_docs=L3.
- Scripts: `scripts/docs_bundle.py`.
