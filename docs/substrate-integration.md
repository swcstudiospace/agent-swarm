# AgentSwarm on Agent Substrate — platform pointer

**Status:** pointer / cross-link only. This document records *where* the swarm's shared platform lives
and how its vocabulary maps onto the existing AgentSwarm spec. It changes no runtime behaviour: nothing
in `swarm/`, `scripts/`, `omp/` or `hooks/` reads or depends on it.

- **Platform repo:** [swcstudiospace/agent-substrate](https://github.com/swcstudiospace/agent-substrate)
- **Primary ADR** (lands in that repo, not here): `docs/adr/*-swarm-substrate-integration.md`
- **Tracking:** Linear [SPE-5050](https://linear.app/swcstudio/issue/SPE-5050) (platform) ·
  [SPE-5051](https://linear.app/swcstudio/issue/SPE-5051) (swarm ↔ substrate integration)

## Why this exists

[01-architecture.md §2.2](../01-architecture.md) lists the swarm's infrastructure services (message bus,
Task Store, artifact registry, memory, secrets, observability) as *shared, not agents*, and
[04-integration-plan.md §2](../04-integration-plan.md) lists the substrates those services expose.
**Agent Substrate is the platform that provides them.** AgentSwarm is one tenant of it, not its owner:
the swarm keeps its agent roster, gates and task DAG; the substrate supplies the transport, the governed
memory and the coordination primitives underneath.

The local `.swarm/` store (SQLite Task Store, `events.jsonl`, signed verdicts) remains the offline,
single-host implementation of the same contracts — see
[05-deployment-guide.md](../05-deployment-guide.md). Substrate is the hosted form of the same seams,
not a replacement for them.

## Vocabulary map

| Substrate capability | AgentSwarm concept | Where it is already specified |
|---|---|---|
| Events over **substrate-mcp** | Message bus, `swarm.v1` envelope, `evt./req./offer./ctl./esc.` subjects | [02-message-protocol.md](../02-message-protocol.md) · [04 §2](../04-integration-plan.md) |
| **Governed memory** | Memory plane (vector + KV): decisions, patterns, retrospectives, estimates, written via `memory.write` | [01 §2.2](../01-architecture.md) · [04 §2](../04-integration-plan.md) |
| **Leases / handoffs** | `task.offer` → claim → `task.assign`, single-writer artifact ownership, A01-only state transitions | [01 §3](../01-architecture.md) · [02 lifecycle](../02-message-protocol.md) |
| **Graph ID** | `correlation_id` — one business request, one id, carried by every artifact, message, PR, run and release | [04 §2 correlation invariant](../04-integration-plan.md) |

The Graph ID equivalence is the load-bearing one: because the swarm already guarantees a single
`correlation_id` per business request (invariant 4 in the [README](../README.md)), a swarm run maps onto
exactly one substrate graph, and substrate-side audit joins the swarm's own run log without a translation
table.

## What this doc is not

- Not an integration design. The ADR in `agent-substrate` owns the decision, the interfaces and the
  migration sequencing; this page only points at it.
- Not a change to [04-integration-plan.md](../04-integration-plan.md). That document's substrate table
  stays authoritative for what flows where; §2.1 there links back here.
- Not a dependency. Governance of gates, verdicts and autonomy ceilings stays with the swarm
  ([02-message-protocol.md](../02-message-protocol.md) autonomy levels L0–L4); substrate never raises a
  ceiling.
