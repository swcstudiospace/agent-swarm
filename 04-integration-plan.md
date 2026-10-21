# AgentSwarm — Integration Plan

**Version:** 1.0.0 · Complements [01-architecture.md](01-architecture.md) and [02-message-protocol.md](02-message-protocol.md)

This plan defines how the 30 agents (A01–A30) interact, share data, and resolve conflicts during project execution.

---

## 1. Interaction matrix (producer → consumers)

| Artifact / event (single writer) | A02 | A03 | A04 | A05 | A06 | A07 | A08 | A09 | A10 | A11 | A12 | A13 | A14 | A15 | A16 | A17 | A18 | A19 | A20 | A21 | A22 | A23 | A24 | A25 | A26 | A27 | A28 | A29 | A30 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `requirements.spec` (A02) | — | C | C |  |  |  |  |  |  |  |  |  |  | C | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `acceptance.criteria` (A02) | — | C | C | C | C | C | C | C |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |
| `architecture.blueprint` (A03) |  | — | C |  |  | C |  |  | C | C |  | C |  | C | C |  |  | C |  |  |  | C |  |  | C | C | C |  | C |
| `api.contract` (A03) |  | — |  | C | C | C |  | C |  |  |  |  |  | C |  |  |  | C |  |  |  | C |  |  |  |  |  | C |  |
| `design.system.tokens` / `ux.spec` (A04) |  |  | — |  | C |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |
| `code.patch` (A05/A06/A19/A26/A29) |  |  |  | — | — |  | C | C | C | C |  |  |  | C |  |  | C | — |  |  |  |  |  |  | — | C |  | — |  |
| `schema.migration` / `data.contract` (A07) |  |  |  | C |  | — | C | C | C |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |
| `test.results` / `gate.verdict:quality` (A08) | C |  |  | C | C |  | — |  |  |  | C |  | C |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |
| `review.verdict` / `gate.verdict:review` (A09) |  |  |  | C | C | C |  | — |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |
| `security.gate.verdict` / `vulnerability.report` / `gate.verdict:security` (A10) |  |  |  | C | C |  |  |  | — | C | C |  | C |  |  |  | C |  |  |  |  |  | C |  | C |  |  |  |  |
| `build.artifact` / `iac.change` / `environment.record` / `ci.pipeline` (A11) |  |  |  |  |  |  | C |  | C | — | C | C |  |  |  | C | C |  | C | C | C |  | C |  |  |  |  |  |  |
| `release.plan` / `release.record` / `promote.command` / `gate.verdict:release` (A12) |  |  |  | C | C |  |  |  |  | C | — | C | C | C |  |  |  |  | C |  | C |  |  |  | C |  |  |  | C |
| `incident.alert` / `slo.manifest` / `deploy.telemetry` (A13) | C | C |  |  |  |  |  |  |  |  | C | — | C | C | C |  |  |  | C | C |  |  |  | C |  |  | C |  |  |
| `patch.task` (A14) |  | C |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `docs.bundle` (A15) |  |  |  |  |  |  |  |  |  |  |  | C |  | — |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `cloud.architecture` (A16) |  | C |  |  |  |  |  |  |  |  |  |  |  |  | — | C |  | C | C | C |  | C |  |  |  |  |  |  |  |
| `resilience.design` (A16) |  |  |  |  |  |  |  |  |  |  |  | C |  |  | — |  |  |  |  |  |  |  |  | C |  |  |  |  |  |
| `landing.zone.spec` (A16) |  |  |  |  |  |  |  |  |  | C |  |  |  |  | — | C |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `wellarch.review` (A16) |  | C |  |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `service.catalog.entry` (A17) |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  | — |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `golden.path.template` (A17) |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  | — |  | C |  |  |  |  |  |  |  |  |  |  |  |
| `platform.claim` (A17) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  | — |  |  |  | C |  |  |  |  |  |  |  |  |  |
| `platform.scorecard` (A17) |  |  |  |  |  |  |  |  |  |  |  | C | C |  |  | — |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `supply.attestation.bundle` (A18) |  |  |  |  |  |  |  |  | C |  | C |  |  |  |  |  | — |  |  |  | C |  | C |  |  |  |  |  |  |
| `sbom.bundle` (A18) |  |  |  |  |  |  |  |  | C |  |  |  | C |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |  |
| `image.policy.report` (A18) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |  |
| `vex.statements` (A18) |  |  |  |  |  |  |  |  | C |  |  |  | C |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |  |
| `event.topology.spec` (A19) |  |  |  | C |  |  |  |  |  |  |  | C |  |  |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |
| `event.schema.set` (A19) |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  | — |  |  |  |  |  |  |  |  |  |  |  |
| `serverless.config` (A19) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  | — |  |  | C |  |  |  |  |  |  |  |  |
| `cost.report` (A20) |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C | C |  |  | — |  |  |  |  |  |  |  |  |  |  |
| `budget.policy` (A20) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |  |  |
| `rightsizing.plan` (A20) |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — | C |  |  |  |  |  |  |  |  |  |
| `carbon.report` (A20) |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  | — |  |  |  |  |  |  |  |  |  |  |
| `cluster.capacity.report` (A21) |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C | — |  | C |  | C |  |  |  |  |  |
| `cluster.upgrade.plan` (A21) |  |  |  |  |  |  |  |  |  |  | C |  | C |  |  |  |  |  |  | — | C |  |  |  |  |  |  |  |  |
| `cluster.fleet.status` (A21) |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  | — |  |  | C |  |  |  |  |  |  |
| `gitops.manifests` (A22) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  | — | C | C |  |  |  |  |  |  |
| `rollout.plan` (A22) |  |  |  |  |  |  |  |  |  |  | C | C |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |
| `promotion.record` (A22) |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |
| `drift.report` (A22) |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |  |
| `mesh.policy.set` (A23) |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C | — | C |  |  |  |  |  |  |
| `traffic.plan` (A23) |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |
| `netpol.coverage.report` (A23) |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |  |
| `cluster.policy.set` (A24) |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  | — |  |  |  |  |  |  |
| `cluster.security.findings` (A24) |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |
| `policy.exception.register` (A24) |  |  |  |  |  |  |  |  | C |  |  |  | C |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |  |
| `resilience.report` (A25) |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |
| `chaos.plan` (A25) |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |
| `backup.coverage.report` (A25) |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |
| `dr.runbook` (A25) |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  | — |  |  |  |  |  |
| `contract.abi` (A26) |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — | C | C | C |  |
| `contract.deploy.plan` (A26) |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  | — | C | C |  | C |
| `gas.report` (A26) |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — | C |  |  |  |
| `audit.findings` (A27) |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C | — |  |  |  |
| `audit.report` (A27) |  |  |  |  |  |  |  |  |  |  | C |  |  | C |  |  |  |  |  |  |  |  |  |  | C | — | C |  | C |
| `invariant.suite` (A27) |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — |  |  |  |
| `chain.infra.plan` (A28) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — |  | C |
| `rpc.policy` (A28) |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — | C |  |
| `indexer.spec` (A28) |  |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — | C |  |
| `dapp.integration.spec` (A29) |  |  |  |  | C |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  | — |  |
| `wallet.flow.spec` (A29) |  |  | C |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — |  |
| `custody.policy.set` (A30) |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | C |  | C |  | — |
| `custody.evidence.pack` (A30) |  |  |  |  |  |  |  |  | C |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — |
| `key.ceremony.plan` (A30) |  |  |  |  |  |  |  |  |  |  | C |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  | — |

**C** = the row's artifact is in the column agent's `consumes`; **—** = the column agent writes the row. The matrix is derived mechanically from `agents.json` (`produces` gives the rows, `consumes` the cells; the A16–A30 rows are one per produced artifact type) and must be kept in step with the manifests. A01 has no column: it sends `task.offer`/`task.assign` to every agent and consumes `task.status`, `task.result`, `gate.verdict`, `agent.heartbeat` and `conflict.report` ([02-message-protocol.md §4](02-message-protocol.md)).

Every row has exactly one writer, with two shared-ownership exceptions: `code.patch` is written by A05, A06, A19, A26 and A29, each on its own path set, and `gate.verdict` is written only by the four gate owners — A08 (quality), A09 (review), A10 (security), A12 (release) — each on its own gate row (A12 also consumes the other three verdicts). Agents A16–A30 never write a gate verdict; their work reaches production through A08/A09/A10/A12. Every writer has ≥1 consumer — no orphan artifacts, no orphan agents.

## 2. Data-sharing substrate

| Substrate | What flows through it | Owners |
|-----------|----------------------|--------|
| Message bus (`evt./req./offer./ctl./esc.`) | Control + verdicts + telemetry signals (envelope v1) | platform |
| Task Store (Postgres) | Task DAG, state, budgets, arbitration records | A01 writes; all read own scope |
| Artifact registry (Git + OCI) | Code, contracts, IaC, docs, SBOMs, signed release records | per-artifact single writer |
| Memory (vector + KV) | Decisions, patterns, retrospectives, estimates | all write via `memory.write`; read-all |
| Telemetry plane (OTel) | Traces/metrics/logs with `trace_id` correlation | A13 operates; all emit |

**Correlation invariant:** one business request = one `correlation_id`; every artifact, message, PR, run, and release carries it. This is what makes the whole system auditable end-to-end.

### 2.1 Platform substrate

**[Agent Substrate](https://github.com/swcstudiospace/agent-substrate)** is the shared platform AgentSwarm runs on. It provides four of the substrates above: events over substrate-mcp (message bus), governed memory (the memory plane), leases and handoffs (offer/claim and single-writer ownership), and the Graph ID that a `correlation_id` maps onto. The artifact registry and secrets stay externally provisioned, and the telemetry plane stays OTel. The table above stays authoritative for what flows where; the integration decision itself — including the id mapping and cross-platform trace correlation — is an ADR in that repo (`docs/adr/*-swarm-substrate-integration.md`), tracked as Linear [SPE-5050](https://linear.app/swcstudio/issue/SPE-5050) / [SPE-5051](https://linear.app/swcstudio/issue/SPE-5051). See [docs/substrate-integration.md](docs/substrate-integration.md) for the vocabulary map. No swarm runtime behaviour depends on it today.

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

### 3.4 Cloud-native service (`orch_plan.py --pattern cloud-native`)
```
human → A02: project.brief → requirements.spec + acceptance.criteria         [req]
A02 → A03: blueprint + api.contract + adr.set                                [arch]
A03 → A16: cloud.architecture + resilience.design + landing.zone.spec        [cloud]
A03/A16 → A19: event.topology.spec + event.schema.set + serverless.config    [events]
A03/A16/A19 → A05: backend implementation (contract-bound)                   [be]
A16 → A17: golden.path.template + service.catalog.entry + platform.claim     [plat]
A16 → A20: cost.report + budget.policy + carbon.report                       [cost]
A05 → A18: image.policy.report + sbom.bundle + supply.attestation.bundle     [img]
A05/A19 → A08 ∥ A09: quality + review verdicts   A05/A19/A18 → A10: security verdict   [qa rev sec]
A08/A09/A10 → A11: build.artifact                                            [build]
A11/A17/A20 → A12: release gate + canary plan → A13: SLOs ∥ A15: docs.bundle [rel obs docs]
```
Parallelism windows: after `cloud`, `events` ∥ `plat` ∥ `cost`; `be` waits for `events`; `img` waits for `be`; verification A08 ∥ A09 ∥ A10 (A10 also waits for `img`). The `events` and `be` rows take their gates from the plan risk class; the four gate rows target `be` and `events`.

### 3.5 Cluster workload (`orch_plan.py --pattern cluster`)
```
A21: cluster.capacity.report (capacity, version skew, deprecated APIs)       [cap]
A21 → A23: traffic.plan + mesh.policy.set + netpol.coverage.report           [net]
A21 → A24: cluster.policy.set (Pod Security, RBAC, admission)                [pol]
A21/A23/A24 → A22: gitops.manifests (risk high → all four gates)             [gitops]
A22 → A25: resilience.report + chaos.plan + backup.coverage.report           [res]
A22 → A08 ∥ A09 ∥ A10: quality, review, security verdicts (A10 also waits for `pol`)   [qa rev sec]
A08/A09/A10 + A25 → A22: rollout.plan + promotion.record                     [roll]
A22 → A12: release gate + canary plan → A13: SLOs ∥ A15: runbook + changelog [rel obs docs]
```
Parallelism windows: `net` ∥ `pol` after `cap`; verification A08 ∥ A09 ∥ A10 beside `res`; `roll` joins the three verdicts and `res`. The `gitops` row is pinned `risk_class: high`, so review, quality, security and release gates all apply. A22 opens promotion PRs and never merges; production promotion is L3 and production prune is L4.

### 3.6 Smart-contract protocol (`orch_plan.py --pattern web3`)
```
human → A02: project.brief → requirements.spec + acceptance.criteria         [req]
A02 → A03: protocol architecture + interfaces + adr.set                      [arch]
A03 → A30: custody.policy.set (signer, multisig, timelock, key ceremony)     [cust]
A03/A30 → A26: code.patch + contract.abi + gas.report + contract.deploy.plan [sol]
A03/A26 → A29: code.patch + dapp.integration.spec + wallet.flow.spec         [dapp]
A26 → A27: audit.findings + audit.report + invariant.suite                   [aud]
A26/A30 → A28: chain.infra.plan + rpc.policy + indexer.spec                  [chain]
A26/A29 → A08 ∥ A09: quality + review verdicts   A26/A29/A27 → A10: security verdict   [qa rev sec]
A08/A09/A10 → A11: build.artifact                                            [build]
A11/A28 → A12: release gate (mainnet deployment stays human-approved)        [rel]
A12 → A13: SLOs + chain/indexer monitoring ∥ A15: docs.bundle                [obs docs]
```
Parallelism windows: `dapp` ∥ `aud` ∥ `chain` after `sol`; verification A08 ∥ A09 ∥ A10 (A10 also waits for `aud`). The `sol` and `dapp` rows are pinned `risk_class: high`, so all four gates apply. A27 findings loop back to A26 as fixes. Contract work is simulation only: A26 proxy upgrade is L3, A26 mainnet deployment and fund movement are L4, A30 key ceremony and fund movement are L4.

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