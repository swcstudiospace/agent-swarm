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
python3 scripts/orch_plan.py --brief brief.md --pattern feature     # brief → task DAG
python3 scripts/swarm_run.py --repo /path/to/codebase --runtime auto  # claude or grok -p --agent <slug>
python3 scripts/swarm_run.py --dry-run --runtime grok                 # simulate the whole DAG offline
bun scripts/ts/req_lint.ts --json                                     # TypeScript twin of any scripts/*.py
python3 scripts/build_agents.py --install-workspace /path/to/workspace  # Claude + Grok agents, skills, hook
python3 scripts/orch_status.py                                      # status, gates, escalations
python3 -m pytest -q                                                # runtime + orchestration tests
```

Or, inside Claude Code, ask for the `a01-orchestrator` subagent: it plans, then delegates each ready task to
`a02-requirements` … `a15-docs` via the Agent tool. See [CLAUDE.md](CLAUDE.md) for the full layout and rules.

## Suggested reading order

Operators: 01 → 04 → 05 → 06 · Agent developers: 02 → your agent spec in 03/ → 07 · Auditors/security: agent §7 sections + 06 §S · Integration work: 02 → 04.
