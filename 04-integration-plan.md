# AgentSwarm — Integration Plan

**Version:** 1.0.0 · Complements [01-architecture.md](01-architecture.md) and [02-message-protocol.md](02-message-protocol.md)

This plan defines how the 15 agents interact, share data, and resolve conflicts during project execution.

---

## 1. Interaction matrix (producer → consumers)

| Artifact / event (single writer) | A02 | A03 | A04 | A05 | A06 | A07 | A08 | A09 | A10 | A11 | A12 | A13 | A14 | A15 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `requirements.spec` (A02) | — | C | C | C | C | C | C | | C | | | | | C |
| `acceptance.criteria` (A02) | — | C | C | C | C | C | **C** | C | | | C | | | C |
| `architecture.blueprint` (A03) | C | — | C | C | C | C | C | C | C | C | | C | C | C |
| `api.contract` (A03) | C | — | C | **C** | **C** | C | C | C | C | | C | | | C |
| `design.system.tokens` / `ux.spec` (A04) | | C | — | | **C** | | C | | | | | | | C |
| backend/frontend code (A05/A06) | | | | — | — | | **C** | **C** | C | C | | | | C |
| `schema.migration` / `data.contract` (A07) | | C | | **C** | | — | C | C | C | C | C | | | C |
| `test.results` / `gate.verdict:quality` (A08) | C | | C | **C** | **C** | C | — | C | | | C | | | |
| `review.verdict` (A09) | | C | | **C** | **C** | **C** | | — | | | | | | |
| `security.gate.verdict` (A10) | | C | | C | C | C | | C | — | **C** | **C** | | C | |
| `build.artifact` / environments (A11) | | | | | | | C | | C | — | **C** | C | | |
| `release.plan` / `release.record` (A12) | | | | | | | | | C | C | — | C | C | **C** |
| `incident.alert` / SLO data (A13) | C | C | C | C | C | C | C | | C | C | **C** | — | **C** | C |
| `patch.task` / debt register (A14) | C | C | | | | C | | C | C | | C | C | — | C |
| `docs.bundle` / runbooks (A15) | | | C | | C | | | | | | C | **C** | | — |

**C** = consumes. Bold = primary consumer. Every row has exactly one writer; every writer has ≥1 consumer — no orphan artifacts, no orphan agents.

## 2. Data-sharing substrate

| Substrate | What flows through it | Owners |
|-----------|----------------------|--------|
| Message bus (`evt./req./offer./ctl./esc.`) | Control + verdicts + telemetry signals (envelope v1) | platform |
| Task Store (Postgres) | Task DAG, state, budgets, arbitration records | A01 writes; all read own scope |
| Artifact registry (Git + OCI) | Code, contracts, IaC, docs, SBOMs, signed release records | per-artifact single writer |
| Memory (vector + KV) | Decisions, patterns, retrospectives, estimates | all write via `memory.write`; read-all |
| Telemetry plane (OTel) | Traces/metrics/logs with `trace_id` correlation | A13 operates; all emit |

**Correlation invariant:** one business request = one `correlation_id`; every artifact, message, PR, run, and release carries it. This is what makes the whole system auditable end-to-end.

## 3. Reference event flows

### 3.1 Greenfield feature (happy path)
```
human → A02: project.brief
A02 → A01: requirements.spec + acceptance.criteria          [VALIDATED]
A01 → A03/A04/A07: parallel design tasks
A03 → all: blueprint + api.contract + adr.set
A04 → A06: design tokens + ux.spec        A07 → A05/A08: schema.migration + data.contract
A01 → A05/A06/A07: parallel implementation tasks (contract-bound)
A05/A06/A07 → A08/A09/A10: code.patch (PRs)
A08/A09/A10 → A01: gate verdicts (conjunction enforced)
A01 → A11: environment+pipeline tasks → build.artifact
A01 → A12: release.request (all gates green)
A12 → A11: promote.command (canary) → A13: deploy.telemetry guardrails → promote → release.record
A13 → A02/A03/A14: escape/feedback signals          A15 → registry: docs.bundle
```
Parallelism windows: design (A03 ∥ A04 ∥ A07), implementation (A05 ∥ A06), verification (A08 ∥ A09 ∥ A10).

### 3.2 Incident → hotfix
```
A13: incident.alert(sev2, suspect REL-118) → A12: freeze + rollback.command
                                           → A14: hotfix.task (root-cause)
                                           → humans: notification (L2)
A14 → A05: patch (minimal diff) → A08/A09/A10 (priority-ordered gates)
A12: promote with guardrails → A13: confirm stabilization → unfreeze → retro → memory
```

### 3.3 Dependency risk event
```
A10: CVE (KEV) → A14: patch.task (24h deadline) → A05/A07: bumps via gates
A12: scheduled canary promotion → A13: regression watch → A14: patch ledger update
```

## 4. Contract governance (schema & artifact versioning)

- **Semver everywhere:** contracts, tokens, SLO manifests, runbooks, message schemas.
- **Compatibility policy:** consumers pin digests; producers maintain N-1 compatibility (parallel subjects for v1/v2) during a 30-day deprecation window.
- **Change routing:** any agent may `request` a contract change; only the single-writer owner publishes it; breaking changes require consumer acks (A05/A06/A08) recorded before A01 releases dependent tasks.

## 5. Conflict resolution ladder (applied in order)

1. **Evidence rules win.** Objective outputs (test results, scans, EXPLAIN plans, SLO burn) outrank opinions in every arbitration.
2. **Ownership rule.** Disputes over an artifact are settled by its single writer; disputes *about* an artifact's content go to step 3.
3. **Gate conjunction.** Verdict conflicts (REV approves, SEC fails): most restrictive verdict wins automatically; producer gets merged feedback; A01 records rationale.
4. **Arbitration by A01.** Cross-cutting conflicts (scope vs budget vs deadline): A01 arbitrates using policy weights; produces signed `conflict.arbitration` with appeal window.
5. **Human escalation.** Risk acceptance, scope commitment, budget overrun > 10 %, gate waivers, autonomy changes — L4, with an evidence bundle and option matrix.

**Deadlock guard:** any conflict older than 1 planning cycle is auto-escalated (never silently parked).

## 6. Autonomy interlocks (cross-agent safety)

| Trigger | Automatic effect |
|---------|------------------|
| `incident.alert` sev ≥ 2 (A13) | A12 deploy freeze; A14 hotfix path primed; A01 pauses non-critical scheduling |
| Security `fail` on main dependency (A10) | A12 blocks releases; A14 opens patch tasks; A03 alerted if architectural |
| Fitness-function failure on main (A03 checks) | A01 holds CLAIMED for dependent downstream tasks |
| A13 `monitoring.degraded` | A12 halts canaries (fail-closed); alerting falls back to probes |
| Budget breach (A01) | Task checkpoint → requeue once → ESCALATED |

## 7. Integration test seams (built-in from day one)

- Every agent exposes `/healthz`, `/readyz`, and a **deterministic dry-run mode** (accepts `task.assign`, returns canned valid outputs) — the basis of the swarm test harness (see [06-testing-protocols.md](06-testing-protocols.md)).
- All agents run in a `sim` profile (bus + fake tool adapters, no real clouds/repos) for full-swarm E2E rehearsal.
- Consumer-driven contract tests (Pact-style) are generated from each agent's `consumes`/`produces` manifest — the registry refuses incompatible pairs.