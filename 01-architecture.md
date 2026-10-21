# AgentSwarm — Architecture Overview

**Version:** 1.0.0 · **Status:** Stable · **Audience:** Swarm operators, agent developers, platform engineers

AgentSwarm is a distributed swarm of 30 specialized software engineering agents that
collectively cover the full SDLC — requirements, design, coding, testing, deployment,
monitoring, and maintenance — through the 15 core agents A01–A15 and three domain lanes of
five agents each: cloud-native (A16–A20), cluster (A21–A25) and Web3 (A26–A30). The design goals are:

- **Parallel execution** — independent agents work concurrently on a shared task DAG.
- **Dynamic workload balancing** — tasks flow to the least-loaded capable agent class.
- **Self-organization** — leader failover, replica auto-scaling, and re-planning on failure
  happen without human intervention.
- **Complementary roles** — every lifecycle phase is owned by exactly one accountable agent;
  other agents participate only as consumers/producers of that agent's artifacts.
- **Production readiness** — every unit of work passes quality, security, and release gates
  before reaching production.

---

## 1. The 30 Agents (at a glance)

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
| A16 | `CLOUD` | Cloud-Native Architect | Design | `cloud.architecture`, `resilience.design`, `landing.zone.spec`, `wellarch.review` |
| A17 | `PLAT` | Platform Engineer | Deployment | `service.catalog.entry`, `golden.path.template`, `platform.claim`, `platform.scorecard` |
| A18 | `SUPPLY` | Supply-Chain Security Engineer | Testing / Security | `supply.attestation.bundle`, `sbom.bundle`, `image.policy.report`, `vex.statements` |
| A19 | `EVENT` | Event-Driven & Serverless Engineer | Coding | `event.topology.spec`, `event.schema.set`, `serverless.config`; event and function source code (`code.patch`) |
| A20 | `FINOPS` | FinOps & GreenOps Analyst | Maintenance | `cost.report`, `budget.policy`, `rightsizing.plan`, `carbon.report` |
| A21 | `K8S` | Kubernetes Cluster Operator | Deployment | `cluster.capacity.report`, `cluster.upgrade.plan`, `cluster.fleet.status` |
| A22 | `GITOPS` | GitOps & Progressive Delivery Engineer | Deployment | `gitops.manifests`, `rollout.plan`, `promotion.record`, `drift.report` |
| A23 | `MESH` | Service Mesh & Networking Engineer | Deployment | `mesh.policy.set`, `traffic.plan`, `netpol.coverage.report` |
| A24 | `KSEC` | Cluster Security & Policy Engineer | Testing / Security | `cluster.policy.set`, `cluster.security.findings`, `policy.exception.register` |
| A25 | `RESIL` | Resilience & Chaos Engineer | Deployment | `resilience.report`, `chaos.plan`, `backup.coverage.report`, `dr.runbook` |
| A26 | `SOL` | Smart Contract Engineer | Coding | `contract.abi`, `contract.deploy.plan`, `gas.report`; contract source code (`code.patch`) |
| A27 | `AUDIT` | Smart Contract Auditor | Testing / Security | `audit.findings`, `audit.report`, `invariant.suite` |
| A28 | `CHAIN` | Blockchain Infrastructure Engineer | Deployment | `chain.infra.plan`, `rpc.policy`, `indexer.spec` |
| A29 | `DAPP` | dApp & Wallet Integration Engineer | Coding | `dapp.integration.spec`, `wallet.flow.spec`; wallet and chain integration source code (`code.patch`) |
| A30 | `CUSTODY` | Web3 Custody & Compliance Engineer | Deployment / Compliance | `custody.policy.set`, `custody.evidence.pack`, `key.ceremony.plan` |

**Accountability rule:** for every artifact type above there is exactly one single-writer
agent. All other agents read those artifacts or request changes via messages — never by
writing directly. This eliminates write conflicts by construction (see [04-integration-plan.md](04-integration-plan.md)).
Source code (`code.patch`) is the one type several agents write — A05, A06, A19, A26 and A29, each in its own path set —
and `gate.verdict` is written only by the four gate owners A08, A09, A10 and A12. A16–A30 own no gate: their work
flows through those four.

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
              ┌─ Domain lanes (A16–A30): same offer/assign, same gates ─┐
              │ Cloud-Native  CLOUD PLAT SUPPLY EVENT FINOPS            │
              │ Cluster       K8S GITOPS MESH KSEC RESIL                │
              │ Web3          SOL AUDIT CHAIN DAPP CUSTODY              │
              └────────────────────┬────────────────────────────────────┘
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
| Delivery | A02, A03, A04, A07, A16 | Requirements → design → data contracts; cloud architecture |
| Build | A05, A06, A19, A26, A29 | Implementation (parallel, contract-bounded): backend, frontend, events and functions, contracts, wallet integration |
| Verify | A08, A09, A10, A18, A24, A27 | Quality, review, security gates; supply-chain, cluster-policy and contract-audit evidence |
| Operate | A11, A12, A13, A17, A21, A22, A23, A25, A28, A30 | Environments, releases, production monitoring; platform, cluster, GitOps, mesh, resilience, chain infrastructure, custody evidence |
| Sustain | A14, A15, A20 | Maintenance, knowledge capture, cost and carbon |

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
2. **Decompose** — A02 (requirements) then A03 (architecture/data/UX contracts) refine the brief into a typed task DAG. Each task carries: capability tag, input artifacts, acceptance criteria, budget, risk class. The `cloud-native`, `cluster` and `web3` patterns add the A16–A30 steps ([04-integration-plan.md §3.4–3.6](04-integration-plan.md)); cluster and web3 implementer tasks carry risk class high.
3. **Offer/claim** — A01 publishes `task.offer` to the capability subject. Matching agent classes bid with load scores; A01 awards the lowest-load/best-fit claimant (or pushes directly for P0 work).
4. **Execute** — agents work in parallel wherever DAG dependencies allow. Long tasks checkpoint progress into the Task Store (resumable, idempotent).
5. **Gates** — outputs pass QA (A08), review (A09), and security (A10) verdicts, and the release gate (A12) for high risk. Gate failures loop back to the producing agent with structured feedback (bounded rework cycles; see §6).
6. **Deliver** — A11 provisions/deploys, A12 promotes via canary with guardrails, A13 watches SLOs, A14 patches, A15 documents. In the domain lanes A22 prepares the GitOps rollout and promotion PRs, A28 plans the blockchain infrastructure and A30 supplies custody evidence; A12 still decides what ships.

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
| [agents/](agents/) | 30 agent specifications (A01–A30) |
| [04-integration-plan.md](04-integration-plan.md) | Interaction matrix, event flows, conflict resolution |
| [05-deployment-guide.md](05-deployment-guide.md) | Deployment, configuration, bootstrap, operations |
| [06-testing-protocols.md](06-testing-protocols.md) | Unit/contract/integration/chaos/E2E test protocols |
| [07-scalability.md](07-scalability.md) | Adding agents, versioning, capacity planning |