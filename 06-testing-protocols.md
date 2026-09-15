# AgentSwarm — Testing & Validation Protocols

**Version:** 1.0.0 · Purpose: prove each agent performs its intended functions reliably, and that the 15-agent swarm operates cohesively to deliver production-ready software.

Testing pyramid (bottom → top): **U** unit/golden → **C** contract → **I** integration flows → **X** chaos/resilience → **E** full-swarm E2E benchmark → **S** security → **V** continuous validation in prod.

---

## 1. Level U — Per-agent unit & golden-task tests (CI, every commit)

Each agent ships a **golden task corpus**: input artifacts → expected output artifacts + expected messages. Deterministic, run in `sim` profile.

| Agent | Representative golden tasks |
|-------|------------------------------|
| A01 | brief→DAG decomposition; bid award (load tie-breaks); gate-conjunction approval; budget stop; failover requeue |
| A02 | brief→stories; ambiguity threshold triggers clarification; INVEST lint rejects bad story; change-request impact estimate |
| A03 | NFR binding; contract versioning (breaking change detection); fitness-function failure handling; ADR generation |
| A04 | state-coverage linter; a11y conflict auto-revision; token semver deprecation |
| A05/A06 | contract-bound implementation; self-gate (no tests → not reviewable); dependency request flow; budget stop |
| A07 | additive vs destructive classification; shadow migration + rollback rehearsal; PII classification gate |
| A08 | criteria→test traceability; risk-based selection; flake quarantine; verdict fail on major finding |
| A09 | verdict ladder; auto-approve thresholds; high-risk co-sign requirement; rules-only fallback |
| A10 | fail-closed on scanner outage; blocking matrix; risk-acceptance L4 routing; dependency request verdict |
| A11 | ephemeral env lifecycle + TTL cleanup; provenance rejection; drift freeze |
| A12 | gate conjunction hold; canary step + guardrail breach auto-rollback; freeze on incident; release record completeness |
| A13 | burn-rate alert mapping; incident declaration sev rules; monitoring.degraded cascade |
| A14 | KEV priority routing; bump auto-merge limits; patch regression loop; EOL epic generation |
| A15 | staleness detection; single-source divergence fix; runbook schema validation; redaction scan |

**Pass criteria:** 100 % corpus pass; schema-valid messages; envelope fields correctly propagated (`correlation_id`, `causation_id`). Gate: CI-blocking.

## 2. Level C — Contract tests (registry-enforced)

- Every `produces` schema has a JSON Schema + 3 exemplars; every `consumes` has consumer-driven expectations (Pact-style).
- The **contract registry refuses** registration of agent versions with incompatible un-pinned changes; CI verifies N-1 compatibility and deprecation windows.
- Round-trip property: for each message type, `deserialize(serialize(msg)) == msg` and unknown-field tolerance (additive forward-compat) is asserted.
- Signatures: unsigned `task.assign`/`promote.command`/gate verdicts MUST be rejected by consumers (negative tests).
- Gate: blocking on any agent release.

## 3. Level I — Integration flow tests (staging `sim`/`dev`)

Scripted scenarios from [04-integration-plan.md §3](04-integration-plan.md) executed against real bus + real (sandboxed) tool adapters:

1. **Greenfield happy path** — full lane traversal REQ→…→release.record with all parallel windows overlapping (assert: A03∥A04∥A07 concurrency, A05∥A06 concurrency, gates conjunction).
2. **Rework loop** — QA fail → producer fix → pass, bounded at 2 loops; 3rd failure ⇒ arbitration/escalation message present.
3. **Incident path** — inject SLO burn → A13 declares → A12 rollback command signed → A14 hotfix task → gates → promote → unfreeze.
4. **Security event** — KEV CVE injected → A10 blocks → A14 patch task with 24 h deadline → patch promoted via canary.
5. **Contract evolution** — v1→v2 API contract with deprecation window; consumers migrate; registry enforces window.
6. **Escalation path** — ambiguous brief → clarification rounds → PROVISIONAL spec flagged → human approve via `esc.human`.

**Pass criteria:** message ordering invariants hold (state machine legal transitions only, verified by replaying the bus stream against the Task Store); `correlation_id` traceable end-to-end; zero orphan tasks; DLQ empty.

## 4. Level X — Chaos & resilience (staging, scheduled weekly)

| Experiment | Expected behavior |
|---|---|
| Kill agent leader (A01) | new leader ≤ 10 s; zero lost transitions (replay audit) |
| Kill 50 % of A05 replicas mid-task | tasks requeued from checkpoints; completion without human help |
| Bus partition 60 s | static-plan mode; no duplicate assignment post-heal (idempotency proofs) |
| Duplicate & reorder messages (10 %) | all consumers idempotent; dedupe metrics increment |
| Poison message (schema-invalid) | rejected with `E-*` to DLQ; swarm throughput unaffected |
| Scanner/registry outage (A10/A11 deps) | fail-closed gates; deploys blocked; alert fires |
| Clock skew 5 min on one replica | no scheduling storms; skew alarms |
| Budget exhaustion storm | checkpoints + requeue-once; escalation path clean |

**Pass criteria:** MTTR within agent-spec limits; no data loss (Task Store audit complete); no silent gate bypasses (invariant checker).

## 5. Level E — Full-swarm E2E benchmark ("production-readiness proof")

A frozen **reference project corpus** (e.g., 3 canonical apps: CRUD+SaaS portal, event-driven service, docs-heavy content platform) is built by the complete swarm in `sim`/`staging` against the *real* reference tools.

**Acceptance gates (all required):**

| Dimension | Threshold |
|---|---|
| Delivery | reference project reaches `release.record` with 100 % gate conjunction |
| Quality | escaped defects vs known-defect corpus ≥ 90 % caught pre-release |
| Security | 0 critical/high in shipped artifacts; SBOM+provenance on 100 % artifacts |
| Performance | app-level NFRs (from its requirements) verified by A08+A13 |
| Docs | coverage report: 100 % public APIs, runbooks verified |
| Efficiency | within budget: cost/task and wall-clock within 1.5× baseline |
| Audit | replayable causal chain human→release; arbitration log complete |

E2E runs on every swarm release candidate and weekly soak (72 h in staging) — soak adds randomized project mix and load.

## 6. Level S — Security testing of the swarm

- **Red-team drills:** prompt-injection via task payloads (e.g., requirements text containing instructions) — agents must treat content as data; attempt to induce contract-bypass or secret exfiltration; must fail closed.
- **Privilege probes:** each agent attempts actions outside its artifact ownership / autonomy ceiling — every attempt must be denied and logged.
- **Sandbox escape attempts** from build/verify lanes; egress allowlist verification.
- **Secret hygiene:** seeded canary tokens across repos/tools — any appearance outside Vault fails the audit.
- **Supply chain:** signed-image enforcement, base-image CVE sweep of the swarm itself, provenance verification negative tests.

## 7. Level V — Continuous validation in production

- **Invariants (stream-checked on live bus):** no APPROVED without required verdicts; no promote without all gates; single-writer discipline; budget enforcement. Violation = P0 swarm incident.
- **Chaos-lite canaries** hourly in prod-safe seams (env create/destroy, dry-run assignments).
- **Outcome telemetry:** A13 tracks per-class KPI dashboards (the §6 metrics of each agent spec) with drift alarms (e.g., REV false-approve rate trending up ⇒ standards/rule review task).
- **Quarterly game day:** full incident + disaster-recovery rehearsal (Task Store restore, bus rebuild, rebootstrap per 05-deployment-guide §3).

## 8. Release gates for the swarm itself

A swarm version ships only when: U + C green → I green → X green (last run) → E benchmark green → S green → V invariants pass in staging soak. Gate results are published as a signed validation record by A12 using the same release machinery as product releases (dogfooding).