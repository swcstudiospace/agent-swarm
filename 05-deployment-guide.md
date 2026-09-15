# AgentSwarm — Deployment & Implementation Guide

**Version:** 1.0.0 · Target platform: Kubernetes ≥ 1.29 (reference), Docker Compose for dev.

---

## 1. Deployment model

| Unit | Packaging | Scaling | State |
|------|-----------|---------|-------|
| Agent class A01 | Container `swarm/orch` ×3 (leader election) | fixed quorum | Task Store (external) |
| Agent classes A02–A15 | One container image per class, N replicas | KEDA on bus lag + queue depth | stateless + checkpoints in Task Store |
| Platform services | NATS (JetStream), Postgres, etcd, Vault, OTel collector, vector store | per-SRE standards | persistent volumes / managed services |

Rules:
- **One image per agent class; one process per container.** No sidecar business logic.
- Agents are **stateless workers**: any replica can resume any task from Task Store checkpoints.
- All configuration via env + mounted policy files; secrets only via Vault-injected short-lived credentials.
- Every image is signed (Cosign) and carries an SBOM; the swarm's own CI uses A11 pipelines (dogfooding).

## 2. Agent manifest (registration contract)

Each agent image must embed `manifest.json` (schema in [07-scalability.md §2](07-scalability.md)). At boot the agent:

1. Registers manifest + capabilities in the registry (TTL-backed).
2. Validates its `consumes`/`produces` against the schema registry — boot **fails closed** on unknown/invalid contracts.
3. Opens bus subscriptions (`req.<class>.*`, `evt.>` filtered, `offer.<capability>`).
4. Reports ready via `/readyz` (all dependencies reachable); begins heartbeats.

## 3. Bootstrap sequence (first deployment)

```
0. Provision platform: NATS+streams, Postgres schema swarm.v1, etcd, Vault roles, OTel, registry
1. Start A01 quorum → wait leader  (etcd lease healthy)
2. A01 initializes Task Store, policy files (autonomy ceilings, budgets, gates matrix)
3. Start verify lane (A08 A09 A10)  → heartbeat check
4. Start ops lane (A11 A12 A13)     → heartbeat check
5. Start delivery+build lanes (A02 A03 A04 A05 A06 A07)
6. Start sustain lane (A14 A15)
7. Run swarm self-test suite (06-testing-protocols.md §6)  → green = accept projects
```

Health gates between steps: proceed only when the previous lane reports ≥ 1 ready replica per class; otherwise bootstrap halts (fail-closed).

## 4. Configuration matrix (per class)

| Setting | A02–A07, A14, A15 | A08–A10 | A11–A13 | A01 |
|---|---|---|---|---|
| `max_parallel` | 4–8 | 2–4 | 1–2 | n/a |
| `task_budget_default` | tokens 200k, wall 2 h | wall 45 m | wall 4 h | n/a |
| `autonomy_ceiling` | per spec (§5 of each agent doc) | fail-closed gates | L2/L3 split | L2 sched / L3 budget |
| `degraded_mode` | defined per agent | smoke/rules-only | probe-only | static-plan |
| Tool credentials | repo write (scoped), no cloud | read-only repo + scanners | cloud via OIDC | none |

Operator overrides: `ctl.<agent>.pause|degrade|drain|reload-policy` — policy reload is hot; autonomy ceilings can only be *lowered* at runtime.

## 5. Environment profiles

| Profile | Bus | Tools | Purpose |
|---|---|---|---|
| `sim` | in-mem NATS | fake adapters (git/scanners/cloud stubs) | harness tests, E2E rehearsal |
| `dev` | single-node NATS | real local tools (docker, local git) | agent development |
| `staging` | clustered | sandbox cloud + test repos | soak, chaos, canary of swarm itself |
| `prod` | clustered HA | real integrations | live projects |

The same image runs in all profiles — behavior differences come only from config/profile.

## 6. Rollout & upgrade

1. **Canary the swarm itself:** new agent image → 1 replica in `sim`, run golden tasks (§6 tests) → staging shadow (verdict-diff against current version, no side effects) → promote.
2. **Ordering:** verify lane first (backward-compatible consumers), then producers, then A01 last (scheduler is the most coupled).
3. **Schema migrations:** Task Store migrations are append-only; A01 quorum upgrades before agents.
4. **Rollback:** previous image tag redeploy; bus streams and Task Store are forward-compatible N-1.
5. **Drain protocol:** `ctl.<agent>.drain` finishes in-flight tasks (or checkpoints them), then exits; K8s `preStop` uses it.

## 7. Observability & operations (of the swarm itself)

- **Metrics (Prometheus):** per class — task throughput, queue depth, lead time, rework loops, budget burn, error-code histogram (`E-*`), bid latency, gate latency.
- **Dashboards:** swarm health (A01), per-lane flow, gate pass rates, cost per task.
- **Alerts:** class unavailable, heartbeat loss, escalation backlog, arbitration churn, DLQ depth, fail-closed activations.
- **Tracing:** `trace_id` propagates human request → task → PR → run → release; a single Jaeger query reconstructs any feature's history.
- **DLQ:** every class has a dead-letter stream; DLQ depth > 0 pages the operator; DLQ replay is a first-class tool.

## 8. Security baseline for the deployment

- Namespace-per-lane with default-deny network policies; explicit allowlist egress per class.
- Workload identity (SPIFFE/OIDC) for every agent; no static secrets; Vault short-lived creds per task where feasible.
- Pod security: non-root, read-only rootfs, seccomp RuntimeDefault, no privilege escalation.
- Audit: bus + Task Store + Vault + cloud control-plane logs shipped to WORM storage.
- Supply chain: signed images, digest-pinned bases, provenance verified by A12 for *swarm* upgrades too.

## 9. Sizing guidance (starting point)

| Class | Replicas (prod baseline) | Notes |
|---|---|---|
| A01 | 3 | quorum, fixed |
| A02/A03/A04/A07/A12/A15 | 1–2 | low parallel ceiling by design |
| A05/A06 | 4–16 | main scaling surface (KEDA) |
| A08/A09/A10 | 2–8 / 2–6 / 2–4 | scale with PR rate |
| A11/A13/A14 | 2–4 | bounded by change-safe operations |

Capacity validation runs during staging soak (§6.6 of testing protocols) tune these numbers per installation.