---
name: agent-swarm-orchestrate
description: >
  Run the AgentSwarm of 15 SDLC subagents (a01-orchestrator through a15-docs).
  Use when the user says run the swarm, AgentSwarm, implement a feature with the 15 agents,
  a01-orchestrator, SDLC swarm, or /swarm. UserPromptSubmit hook injects this skill for SDLC-shaped prompts.
disable-model-invocation: false
---

# AgentSwarm orchestration

This skill is mandatory when the UserPromptSubmit hook injected it (`hooks/user_prompt_submit.py`).
The parent session must not implement domain work.

## When to Use

- Implement / fix / refactor / release / deploy / hotfix a product with the swarm.
- Explicit `/swarm` or "run the swarm" / "AgentSwarm".
- Hook additionalContext named this skill.

Don't use for: pure questions ("what is"), `/uplift`, `/think`.

## Prerequisites

- Agent-swarm checkout at `/root/src/repos/agent-swarm` (or `$SWARM_ROOT`).
- Target application repo (`--repo` / cwd).
- python3; bun optional; claude or grok CLI for unattended `swarm_run`.

## Procedure

1. Identify the target application repo (`--repo` or cwd).
2. `python3 /root/src/repos/agent-swarm/scripts/orch_plan.py --brief-text "<user request>" --pattern feature|hotfix|dependency --json`
   (or `bun scripts/ts/orch_plan.ts` with the same flags).
3. Spawn subagent `a01-orchestrator` (Claude Agent tool / Grok `spawn_subagent` `subagent_type=a01-orchestrator`) with the plan JSON and the user brief.
4. A01 plans and spawns: a01-orchestrator, a02-requirements, a03-architect, a04-ux-designer, a05-backend, a06-frontend, a07-data, a08-qa, a09-reviewer, a10-security, a11-devops, a12-release, a13-observability, a14-maintenance, a15-docs.
5. Parent MUST NOT write application code, docs, or IaC.
6. Unattended alternative: `python3 scripts/swarm_run.py --repo <app> --runtime auto --json` (dry-run with `--dry-run`).

## Hook

`hooks/user_prompt_submit.py` is fail-open (always exit 0). On SDLC-shaped prompts it injects additionalContext telling the parent to load this skill and spawn a01-orchestrator.

## Verification

- A plan JSON exists under `.swarm/plans/`.
- Child slugs match agents.json.
- Parent transcript contains no product-code patches from the parent itself.
