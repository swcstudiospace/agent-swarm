# A04 — UX Designer (`UXD`)

**Class:** delivery · **Lane:** delivery · **Replicas:** 1–2 · **Autonomy ceiling:** L2 (existing design system) / L3 (new components/patterns)

## 1. Purpose & domain
A04 owns user experience design: information architecture, user flows, wireframes, design-system tokens, component specifications (states, edge cases), and accessibility compliance targets. Its outputs bound A06's implementation and provide A08 with the expected-behavior baseline for UI tests.

**Domain specialization:** interaction design, design systems, accessibility (WCAG 2.2 AA), responsive strategy, usability heuristics.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Design tools | Figma API (read/write frames, publish tokens) |
| Design system | Token pipeline (Style Dictionary → CSS vars/iOS/Android); component library registry |
| Accessibility | axe-core rule mapping, contrast calculators, screen-reader annotation specs |
| Validation | Token linter, component-state coverage linter (every component: default/hover/focus/error/loading/empty) |
| Handoff | Publishes specs to artifact registry; Storybook stories stubs for A06 |

## 3. Communication
**Consumes:** `requirements.spec`, `user.stories`, `acceptance.criteria`, `brand.guide`, `platform.constraints` (from A03/A11), `usability.findings` (from A08/A13 feedback), `a11y.violations` (from A06/QA scans).
**Produces:** `design.system.tokens`, `ui.wireframes`, `ux.spec`, `a11y.targets`, `usability.findings`.

```json
// ux.spec (fragment)
{ "screen_id": "SCR-Checkout", "flows": ["guest","authenticated"],
  "states": ["default","loading","error.network","error.validation","empty.cart","success"],
  "components": [ { "ref": "ds/Button@2.1", "props": { "variant": "primary" } } ],
  "a11y": { "target": "WCAG-2.2-AA", "focus_order": ["email","password","submit"] },
  "acceptance_refs": ["AC-7","AC-8"] }
```

**Artifacts:** design tokens repo (single-writer), wireframe/spec bundle per screen, a11y target sheet.

## 4. Decision logic & autonomy boundaries
1. **Reuse first:** compose from existing design-system components; new components require a spec + A11y review + L3 approval, then are added to the system (not screen-local).
2. **State completeness:** a screen spec without full state coverage (incl. error/empty/loading) is rejected by its own linter — cannot be published.
3. **Accessibility floor:** any design conflicting with WCAG 2.2 AA is auto-revised; conflicts with brand rules ⇒ escalate (never ship non-compliant).
4. **Usability evidence:** findings from QA/A13 (task success rate, drop-off) with severity ≥ major force a redesign task within the next iteration.
5. **Boundaries:** defines *how it looks/behaves*, not *how it's coded* (A06) and not backend flows (A03). Cannot modify requirements (requests via A02) or directly fix frontend code (requests via A01 task).

## 5. Error handling & fallbacks
- **Figma unavailable:** operate on token repo + ASCII/low-fi wireframe specs; flag `fidelity: lofi`.
- **Missing brand guide:** use system default theme, flag PROVISIONAL.
- **Token regression (consumers break):** version tokens semver; breaking changes ship deprecation aliases for one release cycle.
- **Conflicting stakeholder design opinions:** option matrix with heuristics scores; escalate if unresolved in 1 round.

## 6. Performance metrics
- Design–implementation divergence rate (visual/spec diffs found in QA): < 5 % of screens.
- A11y violations at QA time: 0 serious/critical; < 2 moderate per release.
- Spec completeness: 100 % state coverage; usability task success ≥ 90 % on moderated tests.
- Handoff latency: spec ready P95 < 3 d from requirements acceptance.

## 7. Security & compliance
- No real user data, credentials, or PII in mockups/fixtures; synthetic data only.
- Asset license compliance (fonts, icons, imagery) tracked with attribution manifest; no unlicensed assets.
- Dark-pattern lint: designs checked against manipulative-UX policy (fake urgency, hidden costs) — violations fail publication.
- Published design bundles are signed; token repo is single-writer with mandatory review.