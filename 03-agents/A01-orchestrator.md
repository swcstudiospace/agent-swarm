# A01 — Swarm Orchestrator (`ORCH`)

**Class:** control · **Lane:** control · **Replicas:** 3 (Raft quorum, 1 leader) · **Autonomy ceiling:** L2 (scheduling) / L3 (budget, kill-switch)

## 1. Purpose & domain
The A01 Orchestrator is the swarm's control plane. It decomposes approved work into a typed task DAG, schedules and balances it across agent classes, enforces budgets and gates, arbitrates conflicts, manages the task lifecycle state machine, and is the single escalation point toward humans. It owns no domain artifacts (never writes code/docs/IaC) — it owns the *plan*.

**Domain specialization:** workflow decomposition, DAG scheduling, load balancing, arbitration, fleet health.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Runtime | Go (control loop) — deterministic, low-GC scheduling |
| Workflow durability | Temporal (durable workflows, retry policies) |
| Task Store | PostgreSQL (`swarm.task.v1`, single-writer for task state) |
| Leader election / locking | etcd (Raft lease 10 s) |
| Bus | NATS JetStream — publishes `task.offer`/`task.assign`, consumes everything on `evt.>` and `req.>` |
| Telemetry | OTel spans per task transition; Prometheus scheduler metrics |
| Calendar/budget | Integration hooks to PM tools (Jira/Linear read-only) and cost meters |

## 3. Communication
**Consumes:** `project.brief`, `task.bid`, `task.status`, `task.result`, `gate.verdict`, `agent.heartbeat`, `conflict.report`, `escalation.response`, `memory.write`, `deploy.telemetry` (A13-derived alerts as context).
**Produces:** `task.offer`, `task.assign` (signed), `task.status.rejected`, `plan.updated`, `conflict.arbitration`, `escalation.request`, `swarm.status`, `ctl.<agent>.<cmd>`.

Key payloads:

```json
// task.offer  { task_id, capability, inputs[], acceptance[], budget, risk_class, deadline?, ttl_s }
// task.bid    { task_id, agent_id, load, eta_s, confidence, degraded_mode? }
// task.assign { task_id, agent_id, lease_s, inputs[], acceptance[], budget }        // signed
// conflict.arbitration { subject, winner_claim_id, rationale_md, evidence[], appealable_until }
```

**Artifacts:** owns Task Store rows, `plan.updated` snapshots, arbitration records (append-only audit).

## 4. Decision logic & autonomy boundaries
1. **Decomposition:** brief → DAG using stored decomposition patterns (memory); a task is splittable if it has >1 independent acceptance criterion and estimated effort > budget.quantum (default 30 min).
2. **Assignment:** award to lowest `load + λ·eta_norm` valid bid (λ=0.5); require `confidence ≥ 0.5`; P0 tasks are pushed directly (no bidding).
3. **Gate enforcement:** task cannot reach `APPROVED` without `pass` (or human `waive`) verdicts from every gate required by its `risk_class` (low: review; medium: review+quality; high: review+quality+security+release sign-off).
4. **Rework loop:** `CHANGES_REQUESTED` back to producer, max 2 loops; 3rd failure ⇒ arbitration or `ESCALATED`.
5. **Budget enforcement:** hard-stop at 100 % budget (`E-TIMEOUT`), checkpoint, requeue once with annotated budget; second breach ⇒ ESCALATED.
6. **Autonomy:** L2 for scheduling, re-planning, degraded-mode switching, arbitration of intra-sprint conflicts. L3: total project budget overrun >10 %, cancelling human-approved work, raising any agent's autonomy (never allowed), killing a release. L4: accepting security risk, approving high-risk releases (proposes to humans).

## 5. Error handling & fallbacks
- **Leader crash:** Raft promotes follower in ≤10 s; in-flight `task.assign` leases are validated against Task Store (idempotent re-award).
- **Agent death:** 3 missed heartbeats ⇒ requeue its in-flight tasks (checkpointed, idempotent).
- **No bids:** retry offer ×2 (60 s), then split task, then substitute capability (from manifest aliases), then ESCALATED.
- **Bus/TaskStore outage:** leader enters **static-plan mode** — continues from last persisted plan, queues transitions locally; on recovery, replays with causal checks.
- **Poison task:** N validation failures ⇒ quarantine topic + ESCALATED with diagnostic bundle.

## 6. Performance metrics
- Scheduling latency: P95 < 2 s from task PLANNED to first `task.offer`.
- Assignment overhead: < 5 % of task wall time.
- Deadline adherence ≥ 95 % of planned DAGs; deadlock/starvation count = 0 per week.
- Scheduling fairness (Jain index over class utilization) ≥ 0.9.
- Escalation rate < 5 % of tasks; arbitration overturn rate < 10 %.
- Failover time ≤ 10 s; zero lost task transitions.

## 7. Security & compliance
- Signed `task.assign` (ed25519 key in Vault); agents reject unsigned/invalid assignments.
- RBAC on Task Store; append-only, tamper-evident audit log of all transitions and arbitrations (WORM storage, 400-day retention).
- No direct access to code registries, clouds, or production; operates only through other agents.
- Compliance: decision logs must satisfy SOX-style traceability (who/what/why/when per transition); GDPR — task payloads minimized, no PII beyond stakeholder identifiers from REQ.