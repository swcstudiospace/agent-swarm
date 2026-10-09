# AgentSwarm on Agent Substrate — platform pointer

**Status:** index of the swarm's substrate runtime, plus the vocabulary map below. When
`SUBSTRATE_URL` is set, the swarm calls Agent Substrate for leases, handoffs, governed memory
and workspace tokens. The contracts are the four documents in the next section.

## Runtime docs

- [substrate-leases.md](substrate-leases.md) — task leases, so two replicas never work one node (`swarm/substrate_lease.py`, `scripts/swarm_run.py`)
- [substrate-handoffs.md](substrate-handoffs.md) — signed handoffs when work crosses from one agent to another (`swarm/substrate_handoff.py`)
- [substrate-memory.md](substrate-memory.md) — governed memory through the substrate write door (`swarm/memory.py`)
- [substrate-workspace.md](substrate-workspace.md) — `substrate-mcp` wiring and per-agent tokens (`swarm/workspace.py`, `scripts/_install_substrate.py`)

Those four share transport, timeouts and the event tee in [substrate-tee.md](substrate-tee.md).

- **Platform repo:** [swcstudiospace/agent-substrate](https://github.com/swcstudiospace/agent-substrate)
- **Primary ADR** (lands in that repo, not here): `docs/adr/*-swarm-substrate-integration.md`
- **Tracking:** Linear [SPE-5050](https://linear.app/swcstudio/issue/SPE-5050) (platform) ·
  [SPE-5051](https://linear.app/swcstudio/issue/SPE-5051) (swarm ↔ substrate integration)

## Why this exists

[01-architecture.md §2.2](../01-architecture.md) lists the swarm's infrastructure services (message bus,
Task Store, artifact registry, memory, secrets, observability) as *shared, not agents*, and
[04-integration-plan.md §2](../04-integration-plan.md) lists the substrates those services expose.
**Agent Substrate is the platform providing the four capabilities in the map below** — events,
governed memory, leases/handoffs and the Graph ID. AgentSwarm is one tenant of it, not its owner: the
swarm keeps its agent roster, gates and task DAG; the substrate supplies that transport, that governed
memory and those coordination primitives underneath.

It does not follow that substrate provides every service in §2.2. The artifact registry (Git + OCI) and
secrets (Vault/KMS) stay externally provisioned and are out of scope for this pointer, and the OTel
telemetry plane stays OTel — see the map's last row. Which side provisions what, and the trace-to-graph
correlation across the two platforms, is for the ADR to settle; do not read this page as a provisioning
list.

The local `.swarm/` store (SQLite Task Store, `events.jsonl`, signed verdicts) remains the offline,
single-host implementation of the same contracts — see the runnable-swarm sections of
[CLAUDE.md](../CLAUDE.md) and the [README](../README.md), not
[05-deployment-guide.md](../05-deployment-guide.md), which covers the containerized
NATS/Postgres/etcd/Vault deployment instead. Substrate is the hosted form of the same seams, not a
replacement for them.

## Vocabulary map

| Substrate capability | AgentSwarm concept | Where it is already specified |
|---|---|---|
| Events over **substrate-mcp** | Message bus, `swarm.v1` envelope, `evt./req./offer./ctl./esc.` subjects | [substrate-tee.md](substrate-tee.md) · [02-message-protocol.md](../02-message-protocol.md) · [04 §2](../04-integration-plan.md) |
| **Governed memory** | Memory plane (vector + KV): decisions, patterns, retrospectives, estimates, written via `memory.write` | [substrate-memory.md](substrate-memory.md) · [01 §2.2](../01-architecture.md) · [04 §2](../04-integration-plan.md) |
| **Leases / handoffs** | `task.offer` → claim → `task.assign`, single-writer artifact ownership, A01-only state transitions | [substrate-leases.md](substrate-leases.md) · [substrate-handoffs.md](substrate-handoffs.md) · [01 §3](../01-architecture.md) · [02 lifecycle](../02-message-protocol.md) |
| **Graph ID** | `correlation_id` — one business request, one id, carried by every artifact, message, PR, run and release | [substrate-workspace.md](substrate-workspace.md) · [04 §2 correlation invariant](../04-integration-plan.md) |
| Telemetry (*not claimed by substrate here*) | OTel telemetry plane: traces/metrics/logs correlated by `trace_id`, A13 operates, all agents emit | [01 §2.2](../01-architecture.md) · [04 §2](../04-integration-plan.md) |

The Graph ID row is the load-bearing one, and it is a **correspondence, not string equality**: because the
swarm already guarantees a single `correlation_id` per business request (invariant 4 in the
[README](../README.md)), one swarm run corresponds to exactly one substrate graph. The identifiers
themselves are shaped differently — the swarm mints a UUID (`str(uuid.uuid4())`, see
[`scripts/orch_plan.py`](../scripts/orch_plan.py) and [`swarm/envelope.py`](../swarm/envelope.py)) — so
joining substrate-side audit to the swarm's own run log needs an explicit `correlation_id` ↔ Graph ID
mapping. Defining it (and how `trace_id` correlation crosses the two platforms) belongs to the ADR and
its adapter stage, not to this page.

## What this doc is not

- Not an integration design. The ADR in `agent-substrate` owns the decision, the interfaces and the
  migration sequencing; this page only points at it.
- Not a change to [04-integration-plan.md](../04-integration-plan.md). That document's substrate table
  stays authoritative for what flows where; §2.1 there links back here.
- Not a transfer of governance. Gates, verdicts and autonomy ceilings stay with the swarm
  ([02-message-protocol.md](../02-message-protocol.md) autonomy levels L0–L4); substrate never raises a
  ceiling. The runtime that does call the substrate is specified in the four documents linked above.
