# AgentSwarm — Deployment & Implementation Guide

**Version:** 1.0.0 · Target platform: Kubernetes ≥ 1.29 (reference), Docker Compose for dev.

---

## 1. Deployment model

| Unit | Packaging | Scaling | State |
|------|-----------|---------|-------|
| Agent class A01 | Container `swarm/orch` ×3 (leader election) | fixed quorum | Task Store (external) |
| Agent classes A02–A30 | One container image per class, N replicas | KEDA on bus lag + queue depth | stateless + checkpoints in Task Store |
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
3. Start verify lane (A08 A09 A10 A18 A24 A27)  → heartbeat check
4. Start ops lane (A11 A12 A13 A17 A21 A22 A23 A25 A28 A30)  → heartbeat check
5. Start delivery+build lanes (A02 A03 A04 A05 A06 A07 A16 A19 A26 A29)
6. Start sustain lane (A14 A15 A20)
7. Run swarm self-test suite (06-testing-protocols.md §6)  → green = accept projects
```

Health gates between steps: proceed only when the previous lane reports ≥ 1 ready replica per class; otherwise bootstrap halts (fail-closed).

## 4. Configuration matrix (per class)

| Setting | A02–A07, A14–A16, A19, A20, A26, A29 | A08–A10, A18, A24, A27 | A11–A13, A17, A21–A23, A25, A28, A30 | A01 |
|---|---|---|---|---|
| `max_parallel` (`agents.json`) | 3–6 | 4–8 | 2–4 | 8 |
| `task_budget_default` | tokens 200k, wall 2 h | wall 45 m | wall 4 h | n/a |
| `autonomy_ceiling` | per spec (§5 of each agent doc) | fail-closed gates | L2/L3 split; L4 for cluster delete, production prune, key ceremony, fund movement | L2 sched / L3 budget |
| `degraded_mode` | defined per agent | smoke/rules-only | probe-only | static-plan |
| Tool credentials | repo write (scoped), no cloud | read-only repo + scanners | cloud via OIDC | none |

A18, A24 and A27 are verify-class evidence producers: they never own a gate and never sign a verdict. A26–A30 (the Web3 lane) sign and broadcast nothing; the runtime guard enforces it in every swarm session (§8).

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
- Runtime guard (`omp/src/guard.ts` `RULES`, applied to every swarm session; a row's `agents` field is messaging only). Cluster rows, capability `prod_infra`: `argocd-change` (`app sync|rollback|delete|set|patch` with a production marker or `--prune`/`--force`/`--replace`; `--dry-run` passes), `flux-change` (`delete`, `uninstall`, `bootstrap`; `suspend`/`resume` with a production marker), `mesh-lifecycle` (`istioctl|linkerd|cilium install|uninstall|upgrade`), `velero-change` (`install`, `uninstall`, `restore create`, deletes), `managed-cluster-lifecycle` (`eksctl` create/delete/upgrade/scale/update, `gcloud container clusters|node-pools` and `az aks` create/upgrade/scale/update), `kubeadm-lifecycle` (`init`, `join`, `reset`, `upgrade` except `plan|diff`). `gcloud container clusters delete` and `az aks delete` stay in `cloud-destructive`. Web3 rows: `chain-broadcast` (capability `chain_broadcast`: `cast send|publish`, `forge script --broadcast|--resume`, `forge create`, hardhat/truffle/brownie against a non-local `--network`, and the `solana`/`anchor`/`spl-token`/`near`/`starkli`/`sui`/`aptos` broadcasts) and `chain-key-material` (capability `chain_key_material`: any command carrying `--private-key`/`--mnemonic`/`--mnemonic-path`/`--secret-key`, `cast wallet new|import|sign` and related verbs, `solana-keygen new|recover`, `geth account new|import`). Read-only forms of the cluster and chain-broadcast rows (`get`, `diff`, `cast call`, `forge test`, a simulating `forge script`) pass.

## 9. Sizing guidance (starting point)

| Class | `max_parallel` | Replicas (prod baseline) | Notes |
|---|---|---|---|
| A01 | 8 | 3 | quorum, fixed |
| A02/A03/A04/A07/A12/A15 | 2–4 | 1–2 | low parallel ceiling by design |
| A05/A06 | 6 | 4–16 | main scaling surface (KEDA) |
| A08/A09/A10 | 8 / 8 / 6 | 2–8 / 2–6 / 2–4 | scale with PR rate |
| A11/A13/A14 | 4 | 2–4 | bounded by change-safe operations |
| A16/A20 | 3 | 1–2 | design and cost analysis; low ceiling like A03/A15 |
| A17/A21/A23/A25/A28 | 3 | 1–2 | platform, cluster, mesh, resilience, chain operations: change-safe, one change window at a time |
| A22/A30 | 4 / 2 | 2–4 / 1–2 | rollout PRs like A11/A13; custody evidence like A12 |
| A18/A24/A27 | 4 | 2–4 | evidence per change; scale with PR rate |
| A19/A26/A29 | 4 | 2–8 | second build-lane scaling surface, below A05/A06 |

Replica baselines for A16–A30 are set from `max_parallel` by analogy with the nearest existing class (same lane and ceiling). They are starting points for the staging soak, not measurements.

Capacity validation runs during staging soak (§6.6 of testing protocols) tune these numbers per installation.