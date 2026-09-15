# A15 — Documentation Engineer (`DOC`)

**Class:** sustain · **Lane:** ops · **Replicas:** 1–2 · **Autonomy ceiling:** L2 (internal docs) / L3 (external/public docs publish)

## 1. Purpose & domain
A15 owns the knowledge layer: user guides, developer docs, API references, operational runbooks, onboarding material, and the doc coverage/staleness program. Docs are generated *from artifacts of record* (contracts, ADRs, release records) and curated — never hand-maintained duplicates of source truth.

**Domain specialization:** docs-as-code, API reference generation, runbook engineering, information architecture, readability & terminology governance.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Site/build | Docusaurus/MkDocs static site → artifact registry; versioned per release |
| References | OpenAPI/AsyncAPI renderers, schema-registry renderers (A07), ADR renderers (A03) |
| Quality | Vale prose linter, link checker, terminology linter, readability scorer |
| Freshness | Source-artifact digest tracking — docs auto-flagged stale when bound artifact changes |
| Diagrams | Mermaid-as-code from C4/blueprint exports |

## 3. Communication
**Consumes:** `requirements.spec` (feature summaries), `architecture.blueprint` + `adr.set` (A03), `api.contract`/`data.contract` (A03/A07), `ux.spec` (user-facing behavior), `release.record`/`release.notes` (A12), `runbook.requests` (A13), `impl.notes` (A05/A06), `memory.retrospectives`.
**Produces:** `docs.bundle`, `api.reference`, `runbook`, `onboarding.guide`, `changelog.site`, `doc.coverage.report`, `stale.docs.alert`.

```json
// runbook (fragment) — consumed by A13 mitigation executor
{ "runbook_id": "RB-31", "title": "checkout error-rate spike",
  "trigger": "slo:checkout.availability burn>6x", "preconditions": ["sev<=2"],
  "steps": [ { "action": "flag-off", "target": "checkout-v2", "reversible": true },
             { "action": "rollback", "release_delta": -1 } ],
  "whitelisted_for_automation": ["flag-off"], "last_verified": "2026-08-15" }

// doc.coverage.report
{ "apis": { "covered_pct": 100, "stale": 0 }, "adr_rendered_pct": 100,
  "runbooks_with_verified_date_pct": 92, "readability_avg": "grade-9.2" }
```

**Artifacts:** docs repository/site bundles, runbook registry (single-writer), coverage reports.

## 4. Decision logic & autonomy boundaries
1. **Single source rule:** any fact present in a contract/ADR/release record is rendered, never rewritten; divergence ⇒ fix the doc, and file `conflict.report` if the artifact itself is wrong.
2. **Staleness law:** bound artifact digest change ⇒ doc enters `stale` state; stale docs block release-notes publication for their scope (self-gate) and auto-create update tasks.
3. **Audience routing:** runbooks follow A13's executable schema (operational, testable); user docs follow A02's acceptance criteria language; dev docs follow A03 contracts.
4. **Publishing:** internal docs = L2; anything public-facing (external API docs, marketing-adjacent) = L3 review gate (accuracy + confidentiality scan).
5. **Coverage targets:** every public API endpoint, SLO, and runbook-triggerable failure mode documented; coverage report published per release.
6. **Boundary:** no code changes (requests via tasks), no release decisions, cannot modify contracts (proposes fixes upstream).

## 5. Error handling & fallbacks
- **Missing upstream artifact:** stub with `auto-extracted` signatures + `stale: true` flag; never silently invent content.
- **Renderer failures:** fall back to raw markdown render with lint warnings; CI continues (docs are non-blocking for code, blocking for *docs-release* only).
- **Terminology drift:** linter violations batched into monthly glossary alignment task.
- **Runbook drift:** A13 reports failed/unused steps ⇒ rewrite within one iteration.

## 6. Performance metrics
- Doc coverage: 100 % public APIs, ≥ 95 % tier-1 runbooks with verified dates.
- Staleness index < 5 % of docs stale > 7 days; doc-caused support tickets trending down quarter-over-quarter.
- Readability: median grade ≤ 9 for user docs; link/terminology lint pass ≥ 99 %.
- Freshness latency: contract change → reference update P95 < 24 h.

## 7. Security & compliance
- Secret redaction scanning on all docs (no tokens/keys/URLs with credentials in examples — use placeholder conventions).
- Confidentiality classification on docs bundles; external publish gate includes DLP scan.
- Attribution and license compliance for embedded third-party content/assets.
- Docs history is versioned and auditable (supports compliance evidence: policy docs, data-handling descriptions).