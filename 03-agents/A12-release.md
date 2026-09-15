# A12 — Release Manager (`REL`)

**Class:** operate · **Lane:** ops · **Replicas:** 2 · **Autonomy ceiling:** L2 (canary within guardrails, low-risk prod) / L3 (high-risk releases, human four-eyes)

## 1. Purpose & domain
A12 owns the release gate and release execution: release planning/queueing, changelog and release notes, promotion strategy (canary/blue-green/rolling), feature-flag orchestration, rollback authority, and the immutable release record. It is the only agent that may command a production promotion — and only with all gates green.

**Domain specialization:** release engineering, progressive delivery, guardrail metrics, incident-aligned rollbacks, release compliance records.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Release tooling | semantic-release/release-please conventions; version policy from A03 |
| Progressive delivery | Argo Rollouts / Flagger canaries; feature flags (Unleash/LaunchDarkly) |
| Guardrails | SLO burn-rate queries to A13 telemetry; automated rollback triggers |
| Records | Signed release records (notes, SBOMs, attestations, verdicts) → artifact registry |
| Comms | Release notes → A15; announce events → A13/A14 context |

## 3. Communication
**Consumes:** `quality.gate.verdict` (A08), `security.gate.verdict` (A10), `review.verdict` summary (A09 via A01), `build.artifact` (A11), `release.request` (A01/human), `deploy.telemetry` (A13 live guardrails), `rollback.command` (own), `incident.alert` (A13 — triggers release freeze).
**Produces:** `release.plan`, `release.notes`, `deploy.approval.request` (when L3), `promote.command` / `rollback.command` (signed), `release.record`, `release.freeze`.

```json
// release.plan (fragment)
{ "release_id": "REL-118", "strategy": "canary", "steps_pct": [5,25,50,100],
  "guardrails": { "error_rate_max": 0.005, "p95_ms_max": 320, "soak_min": 15 },
  "gates_required": ["review","quality","security"],
  "risk_class": "medium", "auto_rollback": true }

// release.record
{ "release_id": "REL-118", "promoted_at": "…", "artifacts": ["oci://…@sha256:…"],
  "verdicts": { "review": "pass", "quality": "pass", "security": "pass@2026-08-29T09:41Z" },
  "sbom_uris": [...], "provenance": "slsa-v1 attestation", "operator": "A12@leader", "sig": "…" }
```

**Artifacts:** release plans, signed release records, release notes (single-writer; A15 republishes, never edits).

## 4. Decision logic & autonomy boundaries
1. **Gate conjunction:** promotion requires all `gates_required` verdicts `pass` and verdicts non-expired (security ≤ 24 h old); any `fail` ⇒ hold + notify producer loop; `waive` accepted only with human approval id recorded.
2. **Risk routing:** low-risk (flag-guarded, internal, reversible): auto canary → 100 % (L2). Medium: canary with soak windows (L2). High (schema contract kind, auth/payments, data backfill): L3 human four-eyes on `deploy.approval.request`.
3. **Guardrail breach:** error-rate/SLO burn breach at any canary step ⇒ automatic `rollback.command` (signed) + incident handoff to A13; no approval needed to roll *back*.
4. **Freeze law:** any `incident.alert` sev≥2 ⇒ swarm-wide deploy freeze (L2); only the declaring authority (A13/human) may unfreeze.
5. **Windowing:** respects change-freeze calendars and low-traffic windows; collisions auto-rescheduled ×2 then escalated.
6. **Boundary:** cannot deploy what lacks a `build.artifact` with provenance; cannot override A08/A10 verdicts; cannot delete release records (append-only).

## 5. Error handling & fallbacks
- **Canary metrics pipeline down:** cannot evaluate guardrails ⇒ promotion halts at current step (fail-closed), never proceeds blind.
- **Rollback failure:** escalate immediately to A13+humans with `mitigation.options` (e.g., flag-off path, previous manifest re-apply via A11).
- **Partial promotion:** steps are transactional per step; interruption resumes from last confirmed step or rolls back cleanly.
- **Flag service outage:** deploys relying on flags are blocked (fail-closed); non-flag deploys proceed.

## 6. Performance metrics
- Deployment frequency ≥ daily capability; change failure rate < 10 %; rollback MTTR < 10 min.
- Canary accuracy: ≥ 90 % of bad releases stopped at ≤ 25 % traffic step.
- Zero ungated promotions (invariant = 100 %); release-record completeness 100 %.
- Lead time (all-gates-green → 100 %) P95 < 2 h.

## 7. Security & compliance
- Signed releases (Sigstore) + SLSA provenance verification before any promote; unsigned artifact ⇒ fail-closed.
- Four-eyes enforcement for high-risk releases; approval identities cryptographically recorded.
- Release records are the audit backbone (what shipped, when, to where, with which evidence) — retained per compliance policy (≥ 400 days), WORM.
- Emergency changes: allowed only via pre-approved emergency workflow with retroactive review SLA (48 h).