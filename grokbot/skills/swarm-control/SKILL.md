---
name: swarm-control
description: >
  Grok Bot lane control for a01-orchestrator.
  Use when Desk Lead routes work to this lane of the 15 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane control

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `control`.
- The role slug is one of: a01-orchestrator.

## Procedure

1. Read the role prompt at `$SWARM_ROOT/<prompt>` below. `$SWARM_ROOT` is the pinned agent-swarm checkout. The target repo does not contain these prompts. The swarm has exactly these 15 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Among role globs, the longest match wins, and an equal length goes to the lowest role id. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts from the pinned checkout: `python3 "$SWARM_ROOT/scripts/<script>.py" --root <target repo> --json` (the bun twin is `bun "$SWARM_ROOT/scripts/ts/<script>.ts" --root <target repo> --json`). `<target repo>` is the git toplevel being edited. Do not run a `scripts/` path from the target tree. Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A01 ORCH (a01-orchestrator)

- Prompt: `$SWARM_ROOT/prompts/A01-orchestrator.md`
- Role: Senior Swarm Orchestrator (A01 ORCH, slug a01-orchestrator) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A01-orchestrator.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for the plan / Task Store / signed task.assign.
- Home: `desk-lead`. Seat: `bot-00-programming-lead`.
- Autonomy ceiling: accept_risk=L4, budget=L3, kill_release=L3, schedule=L2.
- Scripts: `$SWARM_ROOT/scripts/orch_plan.py`, `$SWARM_ROOT/scripts/orch_status.py`.
