# AgentSwarm — Testing & Validation Protocols

**Version:** 1.0.0 · Purpose: prove each agent performs its intended functions reliably, and that the 30-agent swarm operates cohesively to deliver production-ready software.

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
| A16 | twelve-factor audit scores each factor; composite availability (series/parallel) against the target; DR strategy versus RTO/RPO; data class × region residency violations; landing-zone lint (guardrails, audit account, break-glass); blast radius names single points of failure; contract conflict goes to `conflict.report`, never an A03 edit |
| A17 | catalog lint (owner, lifecycle, system, tier); composition schema diff separates breaking from additive; claim validated against the composition schema; maturity scorecard level; catalog graph reports cycles, dangling and orphans; no infrastructure applied, no pipeline edited |
| A18 | Dockerfile lint (latest tag, root user, unpinned base); SBOM validation against the NTIA minimum elements; SBOM diff (downgrade, license change); SLSA level from provenance; signature policy by issuer and identity; VEX → effective exposure; verdict `fail` exactly when `blocking_count` > 0; no signing key, no gate verdict |
| A19 | schema compatibility (backward/forward/full); CloudEvents attribute check; partition plan from throughput and key skew; retry schedule fits the visibility timeout; DLQ coverage per consumer; idempotency window versus redelivery window; replay needs the approval gate (production replay is L3) |
| A20 | budget burn projects the overrun instant; rightsize from p95/p99 with headroom; commitment coverage and break-even; MAD spend-anomaly detection; allocation-tag coverage; showback exact to the cent; location-based carbon estimate; commitment purchase stays L4 |
| A21 | `k8s_deprecated_api_scan` removes 1.25 PSP and CronJob v1beta1; version skew (kubelet up to three minors behind the API server, two below 1.25); drain feasibility under PodDisruptionBudgets; ordered upgrade plan with blockers; maintenance-window blackout; production change L3, cluster delete L4 |
| A22 | kustomize overlay integrity; sync waves put CRDs before custom resources; prune risk (PVC, Namespace, CRD) with the autonomy level; canary verdict Successful/Failed/Inconclusive; promotion gate (stage order, soak, change freeze); drift classified benign/drift/dangerous; plaintext Secret scan; opens PRs, never merges |
| A23 | authorization lint (default-deny, wildcard principals); effective mTLS mode (workload > namespace > mesh > PERMISSIVE); NetworkPolicy reachability under additive policies; retry amplification along a call chain; timeout hierarchy; Gateway API ReferenceGrant validity; CIDR overlap; ingress TLS hygiene |
| A24 | Pod Security baseline/restricted; RBAC risk (wildcards, escalate/bind/impersonate, cluster-admin); effective access through aggregated ClusterRoles; admission lint flags fail-open webhooks; CIS score; exception-register expiry and staleness; tenant isolation |
| A25 | PDB check (blocking, unsatisfiable, missing); topology spread versus zone count; probe sanity; backup coverage with restore tests; restore time versus RTO; chaos spec needs steady state, blast radius and abort conditions; chaos go/no-go against the error budget; FMEA RPN ranking; production DR failover stays L4 |
| A26 | keccak selector and topic check; breaking ABI diff; storage-layout upgrade compatibility; upgrade safety (initializer, selfdestruct, delegatecall, UUPS authorization); access matrix; gas regression; deploy-plan check (chain-id guard, simulation, no RPC URL with a credential); test gate (coverage, fuzz, invariant floors); no key material, mainnet deployment L4 |
| A27 | SWC class and default severity; severity matrix with the L3 override flag; reentrancy checks-effects-interactions order; oracle TWAP attack cost versus profit; invariant coverage; finding dedupe; report completeness; fix verification; never edits contract source, never accepts risk (L4) |
| A28 | client diversity; RPC failover plan; confirmation depth; L2 withdrawal window; indexer lag and backfill plan; network guard (testnet by default); validator slashing risk; mainnet node change L3, validator key operations L4 |
| A29 | EIP-712 typed-data and permit checks (domain, chain id, nonce, deadline, spender); SIWE message; chain-config check; unlimited-approval risk; transaction state machine; ERC-4337 UserOperation coherence; signing-request phishing scan; no key requested, stored or logged; no transaction flow without simulation and user confirmation |
| A30 | multisig m-of-n and signer diversity; timelock delay versus value at risk; key-ceremony checklist completeness; shard distribution independence; transaction policy evaluates to allow/hold/deny and never signs; sanctions-screening freshness; Travel Rule field completeness; audit-trail hash chain; secret scan reports locations and lengths, never the value; key ceremony and fund movement L4 |

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
7. **Domain happy paths** — the `cloud-native`, `cluster` and `web3` flows of [04-integration-plan.md §3.4–3.6](04-integration-plan.md) (assert: `events` ∥ `plat` ∥ `cost`; `net` ∥ `pol`; `dapp` ∥ `aud` ∥ `chain`; A10 waits for `img`/`pol`/`aud` respectively; cluster and web3 implementer rows carry all four gates).

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