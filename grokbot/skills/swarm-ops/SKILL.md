---
name: swarm-ops
description: >
  Grok Bot lane ops for a11-devops, a12-release, a13-observability.
  Use when Desk Lead routes work to this lane of the 15 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane ops

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `ops`.
- The role slug is one of: a11-devops, a12-release, a13-observability.

## Procedure

1. Read the role prompt at `$SWARM_ROOT/<prompt>` below. `$SWARM_ROOT` is the pinned agent-swarm checkout. The target repo does not contain these prompts. The swarm has exactly these 15 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Among role globs, the longest match wins, and an equal length goes to the lowest role id. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts from the pinned checkout: `python3 "$SWARM_ROOT/scripts/<script>.py" --root <target repo> --json` (the bun twin is `bun "$SWARM_ROOT/scripts/ts/<script>.ts" --root <target repo> --json`). `<target repo>` is the git toplevel being edited. Do not run a `scripts/` path from the target tree. Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A11 DEVOPS (a11-devops)

- Prompt: `$SWARM_ROOT/prompts/A11-devops.md`
- Role: Senior DevOps / Platform Engineer (A11 DEVOPS, slug a11-devops) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A11-devops.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for iac.change, ci.pipeline, build.artifact, environment.record.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: non_prod=L2, prod_infra=L3.
- Scripts: `$SWARM_ROOT/scripts/devops_ci_check.py`, `$SWARM_ROOT/scripts/devops_build_record.py`.

### A12 REL (a12-release)

- Prompt: `$SWARM_ROOT/prompts/A12-release.md`
- Role: Senior Release Manager (A12 REL, slug a12-release) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A12-release.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for release.plan, promote/rollback commands, release gate.verdict.
- Home: `cloud`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: canary=L2, prod_high_risk=L4, rollback=L2.
- Scripts: `$SWARM_ROOT/scripts/rel_plan.py`.

### A13 OBS (a13-observability)

- Prompt: `$SWARM_ROOT/prompts/A13-observability.md`
- Role: Senior Observability / SRE (A13 OBS, slug a13-observability) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A13-observability.md, agents.json, and the scripts listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for slo.manifest, incident.alert, deploy.telemetry.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: alerting=L2, incident_declare=L2, page_humans=L2.
- Scripts: `$SWARM_ROOT/scripts/obs_slo.py`.
