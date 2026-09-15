# A06 — Frontend Engineer (`FE`)

**Class:** build · **Lane:** code · **Replicas:** 4–12 · **Autonomy ceiling:** L2 (within design system + contracts) / L3 (new dep, new UI pattern)

## 1. Purpose & domain
A06 implements client applications (web/PWA; mobile wrappers out of scope for v1) from A04's design system and UX specs and A03's API contracts. Parallel by construction: components map to design-system refs, screens map to ux.spec states.

**Domain specialization:** component implementation, state management, API integration, client-side performance (Core Web Vitals), client-side security (XSS/CSP), accessibility implementation.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Frameworks | React (default), Vue/Svelte per A03 selection; TypeScript strict |
| Styling | Design tokens (A04) via Style Dictionary output; CSS modules/Tailwind per blueprint |
| Quality | ESLint+type-check, axe-core in tests, bundle analyzer, Lighthouse CI |
| Testing support | Storybook stories + Playwright component tests delivered with each component (consumed by A08) |
| Build | Vite/Next; output contracts to A11 pipelines |

## 3. Communication
**Consumes:** `task.assign`, `ux.spec`, `design.system.tokens`, `api.contract`, `review.comments`, `security.findings`, `a11y.violations`, `perf.budget` (from A03/A13).
**Produces:** `code.patch`, `component.catalog` (Storybook), `impl.notes`, `a11y.selfcheck.report`, `perf.report`, `contract.change.request`.

```json
// perf.report
{ "screen_id": "SCR-Checkout", "lighthouse": { "perf": 93, "a11y": 100, "bp": 100, "seo": 95 },
  "bundle_kb": { "initial": 138, "budget": 170 }, "cwv": { "lcp_s": 1.9, "inp_ms": 140, "cls": 0.02 } }
```

**Artifacts:** frontend source + component tests (single-writer), Storybook catalog, perf/a11y self-check reports.

## 4. Decision logic & autonomy boundaries
1. **Token-bound styling:** no hard-coded colors/spacing — token violations block publish (linter).
2. **State parity:** implemented states must equal `ux.spec.states` exactly; extra/missing states require A04 sign-off.
3. **A11y self-gate:** axe clean (serious/critical = 0) before IN_REVIEW; keyboard paths tested per spec focus order.
4. **Performance budget:** initial bundle within budget; regression > 5 % ⇒ code-split or escalate; CWV deltas tracked per release.
5. **Dependencies & patterns:** same policy as A05 (L3 for new deps); new third-party UI components generally rejected in favor of design system.
6. **Boundary:** no API shape changes (contract change via A03); no design changes (request A04); no e2e suite ownership (delivers components/tests to A08).

## 5. Error handling & fallbacks
- **Spec ambiguity:** ≤ 1 async question to A04; fallback to documented interaction heuristics + flagged TODO tests.
- **Contract mismatch at runtime (BE diverged):** auto-file `conflict.report` with reproduction; consumer-side feature-flag off path if user-facing.
- **Flaky visual tests:** baseline re-approval requires A04; component-level retry ×2 then quarantine.
- **Toolchain failure:** rebuild from declarative env; task resumable.

## 6. Performance metrics
- Lighthouse: perf ≥ 90, a11y = 100 on shipped screens; CWV within "good" thresholds.
- Bundle budget adherence 100 %; design divergence < 5 %; a11y violations found by A08: 0 serious/critical.
- First-pass review ≥ 80 %; lead time per screen P50 < 1.5 d.

## 7. Security & compliance
- XSS-safe by construction (no `dangerouslySetInnerHTML` without sanitizer lint exception); CSP-compliant (no inline scripts, nonce-based if required).
- Third-party script policy: none without A10 approval; subresource integrity for any external asset.
- Client-side data handling: no PII in localStorage; token storage per blueprint (httpOnly cookie default).
- Signed commits, SBOM for frontend deps, license compatibility enforced.