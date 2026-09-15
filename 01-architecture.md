# AgentSwarm — Architecture Overview

**Version:** 1.0.0 · **Status:** Stable · **Audience:** Swarm operators, agent developers, platform engineers

AgentSwarm is a distributed swarm of 15 specialized software engineering agents that
collectively cover the full SDLC — requirements, design, coding, testing, deployment,
monitoring, and maintenance. The design goals are:

- **Parallel execution** — independent agents work concurrently on a shared task DAG.
- **Dynamic workload balancing** — tasks flow to the least-loaded capable agent class.
- **Self-organization** — leader failover, replica auto-scaling, and re-planning on failure
  happen without human intervention.
- **Complementary roles** — every lifecycle phase is owned by exactly one accountable agent;
  other agents participate only as consumers/producers of that agent's artifacts.
- **Production readiness** — every unit of work passes quality, security, and release gates
  before reaching production.

---

## 1. The 15 Agents (at a glance)

| ID | Code | Agent | SDLC phase | Single-writer artifact ownership |
|----|------|-------|------------|----------------------------------|
| A01 | `ORCH` | Swarm Orchestrator | Cross-cutting | Task DAG, schedules, arbitration records |
| A02 | `REQ` | Requirements Engineer | Requirements | SRS, user stories, acceptance criteria |
| A03 | `ARCH` | Solution Architect | Design | C4 blueprint, ADRs, tech-stack selection |
| A04 | `UXD` | UX Designer | Design | Design tokens, wireframes, UX specs |
| A05 | `BE` | Backend Engineer | Coding | Backend source code |
| A06 | `FE` | Frontend Engineer | Coding | Frontend source code |
| A07 | `DATA` | Data Engineer | Coding / Data | Schemas, migrations, data contracts |
| A08 | `QA` | Test Engineer | Testing | Test suites, test plans, quality-gate verdicts |
| A09 | `REV` | Code Reviewer | Testing / Quality | Review verdicts and comments |
| A10 | `SEC` | Security Auditor | Cross-cutting | Security-gate verdicts, vulnerability reports |
| A11 | `DEVOPS` | DevOps / Platform Engineer | Deployment | IaC, CI pipelines, environments |
| A12 | `REL` | Release Manager | Deployment | Release plans, promote/rollback commands |
| A13 | `OBS` | Observability / SRE Agent | Monitoring | SLOs, dashboards, incident declarations |
| A14 | `MAINT` | Maintenance Engineer | Maintenance | Patch tasks, dependency bumps, tech-debt register |
| A15 | `DOC` | Documentation Engineer | Cross-cutting | Docs bundles, API references, runbooks |

**Accountability rule:** for every artifact type above there is exactly one single-writer
agent. All other agents read those artifacts or request changes via messages — never by
writing directly. This eliminates write conflicts by construction (see [04-integration-plan.md](04-integration-plan.md)).

---

## 2. Topology

```
                       ┌─────────────────────────────┐
   Human / PM tools →  │  Ingress Gateway (API/Git)  │
                       └──────────────┬──────────────┘
                                      │ project.brief / webhooks
                       ┌──────────────▼──────────────┐
                       │   A01 ORCH (leader + quorum) │
                       │   task DAG · scheduler · arb │
                       └───┬────────┬────────┬───────┘
        task.offer/assign  │        │        │        escalation → humans
              ┌────────────▼─┐ ┌────▼─────┐ ┌▼───────────┐
              │ Delivery lane │ │ Code lane│ │ Ops lane   │
              │ REQ ARCH UXD  │ │ BE FE DA │ │ DEVOPS REL │
              └──────────────┘ └──────────┘ └────────────┘
              │ Verify lane: QA REV SEC      │ Sustain lane: OBS MAINT DOC
              └──────────────────────────────┴────────────┘
                                   │
        ┌──────────────────────────┼───────────────────────────┐
        ▼                          ▼                           ▼
  Message Bus (NATS)        Task Store (Postgres)        Artifact Registry (Git+OCI)
  pub/sub + req/reply       durable task state           code · docs · IaC · SBOM
        │
  Observability plane (OTel → metrics/logs/traces)  ·  Memory (vector store)  ·  Vault
```

### 2.1 Planes

| Plane | Members | Role |
|-------|---------|------|
| Control | A01 | Planning, scheduling, arbitration, swarm health |
| Delivery | A02, A03, A04, A07 | Requirements → design → data contracts |
| Build | A05, A06 | Implementation (parallel, contract-bounded) |
| Verify | A08, A09, A10 | Quality, review, security gates |
| Operate | A11, A12, A13 | Environments, releases, production monitoring |
| Sustain | A14, A15 | Maintenance, knowledge capture |

### 2.2 Infrastructure services (shared, not agents)

- **Message bus** — NATS JetStream (durable streams, at-least-once, subject hierarchy `swarm.<lane>.<type>`). Kafka or Redis Streams are drop-in alternatives behind the same envelope.
- **Task Store** — PostgreSQL, source of truth for the task DAG and state machine.
- **Artifact Registry** — Git (source, docs, IaC) + OCI registry (build artifacts, SBOMs, signed bundles).
- **Memory** — vector store (project knowledge, decisions, retrospectives) + Postgres key-value for task context.
- **Secrets** — Vault/KMS; agents never hold long-lived credentials (OIDC federation).
- **Observability** — OpenTelemetry collector feeding metrics/logs/traces back to A13 (and to A01 for scheduler load signals).

---

## 3. Task model & execution

1. **Ingest** — a project brief, change request, or incident enters via the gateway; A01 validates and stores it as a root task.
2. **Decompose** — A02 (requirements) then A03 (architecture/data/UX contracts) refine the brief into a typed task DAG. Each task carries: capability tag, input artifacts, acceptance criteria, budget, risk class.
3. **Offer/claim** — A01 publishes `task.offer` to the capability subject. Matching agent classes bid with load scores; A01 awards the lowest-load/best-fit claimant (or pushes directly for P0 work).
4. **Execute** — agents work in parallel wherever DAG dependencies allow. Long tasks checkpoint progress into the Task Store (resumable, idempotent).
5. **Gates** — outputs pass QA (A08), review (A09), and security (A10) verdicts. Gate failures loop back to the producing agent with structured feedback (bounded rework cycles; see §6).
6. **Deliver** — A11 provisions/deploys, A12 promotes via canary with guardrails, A13 watches SLOs, A14 patches, A15 documents.

Task state machine (authoritative definitions in [02-message-protocol.md](02-message-protocol.md)):

```
CREATED → VALIDATED → PLANNED → CLAIMED → IN_PROGRESS → IN_REVIEW → APPROVED → DONE
                     ↘ BLOCKED ↗        ↘ FAILED → RETRY/ESCALATED      ↘ CANCELLED
```

---

## 4. Parallelism & dynamic workload balancing

### 4.1 Parallelism sources

- **DAG parallelism** — independent leaves execute simultaneously (e.g., BE, FE, DATA once contracts exist).
- **Class-level replication** — every agent class runs N replicas; the bus load-balances `task.offer` bidding.
- **Pipeline parallelism** — lanes overlap: while A08 tests iteration n, A05 starts iteration n+1 of a sibling task.

### 4.2 Capability registry

Each agent registers a manifest at boot (schema in [07-scalability.md](07-scalability.md)):
`{ id, class, capabilities[], consumes[], produces[], max_parallel, cost_hint, autonomy_ceiling }`.
A01 and the bus use this for routing. Heartbeats (`agent.heartbeat`, 10 s period, 3 missed ⇒ dead) carry live load.

### 4.3 Load score

```
load = 0.5·(queue_depth / max_parallel)
     + 0.3·(est_remaining_s of in-flight tasks / SLA_target_s)
     + 0.2·(rolling avg task duration / class baseline)
```

Bids contain `load` and `eta_s`; A01 awards the task to the lowest `load + λ·eta_norm` bid.
Tie-breaker: lower historical rework rate. Hedges: if no bid within `offer_timeout_s`, A01 retries,
then re-plans the subtree, then escalates (§5).

### 4.4 Backpressure

- Producers respect consumer queue depth (published in heartbeats); at ≥90 % saturation the producer defers or degrades output (e.g., QA switches to risk-based test selection).
- The Task Store enforces per-class WIP caps to avoid thrashing.

---

## 5. Self-organization

| Mechanism | Behavior |
|-----------|----------|
| Leader election | A01 runs a Raft-backed quorum (etcd). Split-brain is impossible: only the leader signs `task.assign`. Lease = 10 s. |
| Replica scaling | KEDA scales agent deployments on bus lag + queue depth. New replicas self-register and start bidding within 5 s. |
| Agent failure | Missed heartbeats ⇒ A01 requeues in-flight tasks (idempotent via task checkpoints). 3 crashes ⇒ task marked `ESCALATED` with diagnostic bundle. |
| Re-planning | Gate-failure loops and blocked subtrees trigger A01 re-planning: reorder, split, or re-target tasks; plan diffs are recorded. |
| Degraded modes | Every agent defines a reduced-capability mode (e.g., QA smoke-only, REV rules-only). A01 switches classes to degraded mode instead of stalling the DAG. |
| Knowledge sharing | Post-task retrospectives write to shared memory; A03/A14 consume them to improve future estimates and patterns. |

---

## 6. Rework & escalation ladder (bounded)

1. **Auto-fix loop** — gate feedback → producing agent. Max **2** automatic iterations per gate.
2. **Arbitration** — on the 3rd failure or a conflicting verdict (e.g., REV approves, SEC blocks), A01 arbitrates using the conflict ladder in [04-integration-plan.md §6](04-integration-plan.md).
3. **Human escalation** — unresolved after arbitration or when risk class = high ⇒ `escalation.request` to humans with evidence bundle; task parked as `ESCALATED` (DAG continues on other branches).

---

## 7. Non-goals

- The swarm does not replace human approval for: production rollouts of high-risk changes, contract/SLA changes, security risk acceptance, destructive data operations.
- Agents never hold long-lived credentials, never write outside their artifact ownership, and never bypass gates (fail-closed).

## 8. Document map

| Document | Content |
|----------|---------|
| [02-message-protocol.md](02-message-protocol.md) | Envelope, subjects, task lifecycle, payload schema conventions |
| [03-agents/](03-agents/) | 15 agent specifications (A01–A15) |
| [04-integration-plan.md](04-integration-plan.md) | Interaction matrix, event flows, conflict resolution |
| [05-deployment-guide.md](05-deployment-guide.md) | Deployment, configuration, bootstrap, operations |
| [06-testing-protocols.md](06-testing-protocols.md) | Unit/contract/integration/chaos/E2E test protocols |
| [07-scalability.md](07-scalability.md) | Adding agents, versioning, capacity planning |