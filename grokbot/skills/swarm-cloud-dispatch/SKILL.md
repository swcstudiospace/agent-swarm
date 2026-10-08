---
name: swarm-cloud-dispatch
description: "Lead hands a repository to a Cursor cloud agent that runs the 15 AgentSwarm agents as subagents. Install the agents with the agent-swarm Cursor installer, then brief the cloud agent to run a01-orchestrator. Results return as a draft pull request."
---

# Swarm cloud dispatch

Use this when Lead hands repository work to a Cursor cloud agent that runs the 15 AgentSwarm agents as subagents.

## Procedure

1. Pin an agent-swarm checkout and set `SWARM_ROOT` to that absolute path. The target repo does not vendor the swarm runtime. `swcstudiospace/programming-desk` is the install target a follow-up pull request uses; it already has its own `scripts/` directory, which is not the swarm tools.
2. Open a pull request on the target repo that installs the agents. From the pinned checkout, dry-run first, then copy:

```bash
python3 scripts/_install_cursor.py --target /path/to/target-repo --dry-run
python3 scripts/_install_cursor.py --target /path/to/target-repo
```

`build_agents.py --install-cursor /path/to/target-repo` is the same copy. It writes only under `.cursor/` (the 15 agents, `.cursor/rules/agent-swarm.mdc`, and a provenance stamp). It does not write MCP config or env files, and it does not wire substrate.
3. Brief a Cursor cloud agent on that repo to run `a01-orchestrator` with the brief. Nesting is two levels: the cloud session may spawn `a01-orchestrator`; A01 may spawn `a02-requirements` through `a15-docs`; those specialists must not spawn further.
4. The cloud agent returns a draft pull request. Treat gate output as advisory. No signing key is in the cloud session, gate scripts record nothing, and nothing counts as APPROVED. The merge gate is Greptile, run by Desk Quality.

Do not start an unattended headless runner. Do not merge the draft pull request from this skill.
