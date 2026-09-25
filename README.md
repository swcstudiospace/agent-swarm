# AgentSwarm — 15-Agent Software Engineering Swarm

A complete design specification for a distributed swarm of 15 specialized AI agents that plan, build, verify, ship, and maintain production software collaboratively — with parallel execution, dynamic workload balancing, and self-organization.

## Document map

| # | Document | Contents |
|---|----------|----------|
| 1 | [01-architecture.md](01-architecture.md) | Swarm topology, 15-agent roster, task model, load balancing, self-organization, rework/escalation |
| 2 | [02-message-protocol.md](02-message-protocol.md) | Envelope `swarm.v1`, subjects & delivery semantics, task lifecycle state machine, shared error taxonomy, autonomy levels L0–L4 |
| 3 | [03-agents/](03-agents/) | Full specifications A01–A15 (7 sections each: purpose, stack, I/O formats, decision logic & autonomy, error handling, metrics, security) |
| 4 | [04-integration-plan.md](04-integration-plan.md) | Interaction matrix, data-sharing substrate, reference event flows, contract governance, conflict-resolution ladder, autonomy interlocks |
| 5 | [05-deployment-guide.md](05-deployment-guide.md) | Packaging, agent manifests, bootstrap sequence, config matrix, profiles, rollout, swarm observability, security baseline, sizing |
| 6 | [06-testing-protocols.md](06-testing-protocols.md) | 7-level validation: golden tasks → contracts → integration flows → chaos → E2E benchmark → security red-team → prod invariants |
| 7 | [07-scalability.md](07-scalability.md) | Manifest schema, new-agent onboarding (shadow→probation→full), traffic shaping, contract evolution, multi-swarm |
| 8 | [CLAUDE.md](CLAUDE.md) · [prompts/](prompts/) · [scripts/](scripts/) · [swarm/](swarm/) | Runnable implementation: subagent prompts, per-agent Python tools, orchestration runtime |

## The 15 agents

| ID | Code | Role | Phase |
|----|------|------|-------|
| A01 | `ORCH` | Swarm Orchestrator — planning, scheduling, arbitration | Control |
| A02 | `REQ` | Requirements Engineer — stories, acceptance criteria | Requirements |
| A03 | `ARCH` | Solution Architect — blueprint, contracts, ADRs | Design |
| A04 | `UXD` | UX Designer — design system, UX specs, a11y | Design |
| A05 | `BE` | Backend Engineer — service implementation | Coding |
| A06 | `FE` | Frontend Engineer — UI implementation | Coding |
| A07 | `DATA` | Data Engineer — models, migrations, data contracts | Coding / Data |
| A08 | `QA` | Test Engineer — quality gate, test automation | Testing |
| A09 | `REV` | Code Reviewer — review gate, standards | Quality |
| A10 | `SEC` | Security Auditor — security gate, supply chain | Cross-cutting |
| A11 | `DEVOPS` | DevOps / Platform — IaC, CI, environments | Deployment |
| A12 | `REL` | Release Manager — progressive delivery, rollback | Deployment |
| A13 | `OBS` | Observability / SRE — SLOs, alerts, incidents | Monitoring |
| A14 | `MAINT` | Maintenance Engineer — patches, debt, EOL | Maintenance |
| A15 | `DOC` | Documentation Engineer — docs, runbooks, references | Cross-cutting |

**Complementarity guarantee:** every SDLC phase has exactly one accountable (single-writer) agent per artifact class; overlap is limited to consumer/producer relationships, and every artifact row has ≥ 1 consumer (see interaction matrix).

## Core invariants (quick reference)

1. Single-writer artifact ownership; all other agents propose via messages.
2. Fail-closed gates — no quality/review/security verdict ⇒ no approval; no all-green verdicts ⇒ no promotion.
3. Bounded rework (max 2 auto loops) → arbitration → human escalation with evidence.
4. Everything correlated: one business request = one `correlation_id` from brief to release record.
5. Autonomy ceilings L0–L4 per action class; runtime policy can lower, never raise.

## Runnable swarm (Claude Code subagents)

The spec is executable. Each agent is a Claude Code subagent in [.claude/agents/](.claude/agents/) whose
XML-tagged system prompt lives in [prompts/](prompts/) and whose tools are Python scripts in [scripts/](scripts/),
built on the stdlib-only runtime in [swarm/](swarm/) (signed `swarm.v1` envelopes, SQLite Task Store with the
lifecycle state machine, fail-closed gates, manifest registry). [agents.json](agents.json) is the manifest.

```bash
python3 scripts/orch_plan.py --repo /path/to/codebase --brief brief.md --pattern feature   # brief → task DAG in <repo>/.swarm
python3 scripts/swarm_run.py --repo /path/to/codebase --runtime auto  # claude or grok -p --agent <slug>; --runtime omp [--omp-bin omp] for headless omp -p
python3 scripts/swarm_run.py --repo /path/to/codebase --dry-run --runtime grok   # simulate the whole DAG offline
bun scripts/ts/req_lint.ts --json                                     # TypeScript twin of any scripts/*.py
python3 scripts/build_agents.py                                      # regenerate .claude/agents, .grok/agents, omp/agents, omp/skills (--check)
python3 scripts/build_agents.py --install-workspace /path/to/workspace  # Claude + Grok agents, skills, hook, plus the omp package (see below)
python3 scripts/orch_status.py --repo /path/to/codebase           # status, gates, escalations (same --repo as the plan)
python3 -m pytest -q                                                # runtime + orchestration tests
```

Or, inside Claude Code, ask for the `a01-orchestrator` subagent: it plans, then delegates each ready task to
`a02-requirements` … `a15-docs` via the Agent tool. See [CLAUDE.md](CLAUDE.md) for the full layout and rules.

## Install into a workspace

`build_agents.py --install-workspace` regenerates the agents, copies the Claude/Grok agents, skills and
UserPromptSubmit hook into the workspace, then installs the omp targets (`omp/agents/`, `omp/skills/` and the
`omp/` extension package). It never writes into this repo or `~/.omp`.

```bash
python3 scripts/build_agents.py --install-workspace /path/to/ws                  # omp link mode (default)
python3 scripts/build_agents.py --install-workspace /path/to/ws --omp-mode copy  # omp agents + skills only
python3 scripts/build_agents.py --install-workspace /path/to/ws --dry-run        # print the plan and config diff, write nothing
```

- **Link** (default) adds this checkout's `omp/` realpath to `extensions:` in `<ws>/.omp/config.yml`, so that file
  holds a host path by design. omp reads it from the cwd only: start omp at `<ws>`. When the file has no
  `extensions` key, the installer carries over your inherited user list, because a project list replaces it.
- **Copy** (`--omp-mode copy`) copies the agents and skills into `<ws>/.omp/agents` and `<ws>/.omp/skills`:
  no tools, no guard, no `/swarm` command and no context hook. Re-run it after every regeneration.
- Both modes print `WARNING shadow:` for each agent or skill with the same frontmatter `name` that omp would load
  first: project `.omp/agents|skills` in `<ws>` or its ancestors, `~/.omp/agent/agents|skills`, earlier
  `extensions:` entries and `skills.customDirectories`. `.claude/*` and `.agents/skills` do not shadow.
- Start a fresh omp session after every install or `build_agents.py` regeneration: extensions and agents load at
  session start.
- `task.maxRecursionDepth`: omp's default of 2 is enough (main session → `a01-orchestrator` → specialists, which
  never get `task`). Set 3 only when A01 is itself spawned by another subagent; otherwise A01 stops with
  `BLOCKED needs: depth`. Never set a negative (unlimited) value.

  ```yaml
  # <ws>/.omp/config.yml or ~/.omp/agent/config.yml
  task:
    maxRecursionDepth: 3
  ```

A relocated `omp/` package needs `SWARM_ROOT` pointing at an agent-swarm checkout, since its tools run the
Python scripts there.

## Suggested reading order

Operators: 01 → 04 → 05 → 06 · Agent developers: 02 → your agent spec in 03/ → 07 · Auditors/security: agent §7 sections + 06 §S · Integration work: 02 → 04.

## Trae SOLO Agents

The [Trae registration kit](.trae/README.md) contains all 15
[XML-tagged prompts](.trae/agents/), each below 10,000 characters, a
[registration checklist](.trae/registration.json), and the
[/swarm command](.trae/commands/swarm.md).

A01 coordinates the task flow; the built-in SOLO agent invokes the registered
specialists and returns their results to A01 for gating and the next batch.
The files do not automatically register agents: enable them in Trae's custom-agent
UI and SOLO's callable-agent settings using the setup guide.

```bash
python3 scripts/build_trae_agents.py          # regenerate the Trae kit only
python3 scripts/build_trae_agents.py --check  # read-only drift and size check
python3 -m pytest tests/test_trae_agents.py -q
```

This native-session adapter does not invoke Claude/Grok hooks, the headless runner,
or the signed Task Store protocol. It preserves approval gates and single-writer
ownership without claiming that XML alone enforces runtime permissions.
