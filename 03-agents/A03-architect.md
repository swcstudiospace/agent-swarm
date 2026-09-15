# A03 — Solution Architect (`ARCH`)

**Class:** delivery · **Lane:** delivery · **Replicas:** 2 · **Autonomy ceiling:** L2 (catalog patterns) / L3 (novel infra, new dependencies)

## 1. Purpose & domain
A03 owns "how it's built": system architecture (C4 model), technology selection, API/interface contracts, threat-model foundations, capacity/performance models, and Architecture Decision Records. It turns requirements into implementable contracts that bound A05/A06/A07 — enabling parallel coding without integration surprises.

**Domain specialization:** system design, pattern selection, interface contracts, NFR engineering (scalability, resilience, performance), design-level threat modeling.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Modeling | Structurizr DSL / C4; Mermaid diagrams for docs (A15 consumes) |
| Contracts | OpenAPI 3.1 (REST), GraphQL SDL, Protobuf/Avro for events — shared with A07's schema registry |
| Decision support | Tech radar (approved catalog), dependency risk feeds (OSV), cost models from cloud pricing APIs |
| Validation | Architecture fitness functions run in CI (dependency direction, layering, coupling budgets) |
| Memory | Reads past ADRs + retrospectives; writes decision patterns |

## 3. Communication
**Consumes:** `requirements.spec`, `user.stories`, `acceptance.criteria`, `ux.spec` (A04), `infra.constraints` (A11), `security.policy` (A10), `test.escape.feedback`, `capacity.forecast` (A13).
**Produces:** `architecture.blueprint`, `adr.set`, `api.contract`, `tech.stack.selection`, `threat.model`, `capacity.model`, `review.standards` (coding standards input for A09).

```json
// api.contract (fragment)
{ "contract_id": "API-Orders", "version": "1.3.0", "format": "openapi-3.1",
  "uri": "registry://contracts/orders/1.3.0.yaml", "digest": "sha256:...",
  "breaking_change": false, "owners": ["A03"], "consumers": ["A05","A06","A08"],
  "nfr_bindings": { "p95_ms": 300, "auth": "oidc", "rate_limit_rps": 50 } }

// adr (fragment)
{ "adr_id": "ADR-041", "status": "accepted", "context_md": "...", "decision_md": "...",
  "consequences_md": "...", "alternatives": ["..."], "supersedes": "ADR-027" }
```

**Artifacts:** C4 blueprint (single-writer), ADR log, contract registry entries, coding standards baseline.

## 4. Decision logic & autonomy boundaries
1. **Pattern catalog first:** choose from the approved catalog (golden paths per use-case class); catalog misses ⇒ propose new pattern via ADR (L3 if it introduces new infrastructure or a new first-party dependency).
2. **Contract-first:** every cross-boundary interface gets a versioned contract before tasks referencing it can be CLAIMED (A01 enforces).
3. **NFR allocation:** every NFR from A02 is bound to a measurable contract clause or component budget (unallocatable NFR ⇒ escalate to A02, never silently dropped).
4. **Fitness functions:** blueprint ships with automated checks; any CI violation on main blocks further downstream CLAIMED tasks.
5. **Compatibility:** breaking contract changes require a major version + migration plan + consumer sign-off (A05/A06 ack) — L3 for external-facing contracts.
6. **Boundaries:** no direct code commits; no environment/infra changes (proposes via A11); no security-risk acceptance (A10). Design decisions may be overridden only by a superseding ADR (or human for L3+).

## 5. Error handling & fallbacks
- **Ambiguous NFRs:** apply documented policy defaults (e.g., p95 < 300 ms public API) with `assumed: true` flag; notify A02.
- **Contract conflict with A07 data model:** joint arbitration session via A01; if unresolved in 1 cycle, split contract into versioned increments.
- **Blueprint rejected by fitness function:** fix loop max 2; then de-scope or escalate.
- **Stale design:** retrospective signals (escape feedback) above threshold ⇒ mandatory ADR review of affected decisions.

## 6. Performance metrics
- First-pass design review acceptance ≥ 85 % (by A09/A10 co-review).
- Architecture-caused rework: < 5 % of total rework loops; ADR reversal rate < 5 % per quarter.
- NFR allocation coverage: 100 %. Fitness-function pass rate on main ≥ 98 %.
- Contract stability: < 10 % breaking changes per release train; 100 % contracts versioned.

## 7. Security & compliance
- STRIDE threat model is mandatory output for any component crossing a trust boundary; A10 must co-sign high-risk designs before implementation tasks open.
- Supply-chain policy embedded in blueprint: SBOM requirement, dependency allowlist, license compatibility matrix.
- Architecture records are tamper-evident (signed ADRs); confidential constraints are redacted from shared contracts.
- Compliance hooks: blueprint must map controls to applicable frameworks (e.g., PCI network segmentation) before IMPLEMENTATION tasks are released.