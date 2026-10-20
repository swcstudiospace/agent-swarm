---
name: swarm-sustain
description: >
  Grok Bot lane sustain for a14-maintenance, a15-docs, a20-finops.
  Use when Desk Lead routes work to this lane of the 30 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane sustain

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `sustain`.
- The role slug is one of: a14-maintenance, a15-docs, a20-finops.

## Procedure

1. Read the role prompt at `$SWARM_ROOT/<prompt>` below. `$SWARM_ROOT` is the pinned agent-swarm checkout. The target repo does not contain these prompts. The swarm has exactly these 30 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Among role globs, the longest match wins, and an equal length goes to the lowest role id. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts from the pinned checkout: `python3 "$SWARM_ROOT/scripts/<script>.py" --root <target repo> --json` (the bun twin is `bun "$SWARM_ROOT/scripts/ts/<script>.ts" --root <target repo> --json`). `<target repo>` is the git toplevel being edited. Do not run a `scripts/` path from the target tree. Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A14 MAINT (a14-maintenance)

- Prompt: `$SWARM_ROOT/prompts/A14-maintenance.md`
- Role: Senior Maintenance Engineer (A14 MAINT, slug a14-maintenance) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A14-maintenance.md, agents.json, and the scripts and tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for patch.task, debt.register, rca.report, dependency.bump.
- Home: `routine`. Seat: `bot-01-systems-backend`.
- Autonomy ceiling: major_bump=L3, patch_task=L2.
- Scripts: `$SWARM_ROOT/scripts/maint_deps.py`.

### A15 DOC (a15-docs)

- Prompt: `$SWARM_ROOT/prompts/A15-docs.md`
- Role: Senior Documentation Engineer (A15 DOC, slug a15-docs) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A15-docs.md, agents.json, and the scripts and tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for docs.bundle, api.reference, runbook, changelog.
- Home: `executor`. Seat: `bot-06-quality-security`.
- Autonomy ceiling: docs=L2, public_docs=L3.
- Scripts: `$SWARM_ROOT/scripts/docs_bundle.py`.

### A20 FINOPS (a20-finops)

- Prompt: `$SWARM_ROOT/prompts/A20-finops.md`
- Role: Senior FinOps &amp; GreenOps Analyst (A20 FINOPS, slug a20-finops) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A20-finops.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for cost.report, budget.policy, rightsizing.plan, carbon.report.
- Home: `routine`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: analysis=L2, budget_change=L3, commitment_purchase=L4.
- Scripts: `$SWARM_ROOT/scripts/finops_cost_scan.py`.
