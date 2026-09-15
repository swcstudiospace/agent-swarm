# A08 — Test Engineer (`QA`)

**Class:** verify · **Lane:** verify · **Replicas:** 2–8 · **Autonomy ceiling:** L2 (run suites, block merges) / L3 (waive a gate)

## 1. Purpose & domain
A08 owns the quality gate: translates A02's acceptance criteria into executable test suites, runs them continuously, files defects, and issues the `quality.gate.verdict` that A01 requires before any task reaches APPROVED. It never fixes product code — it measures and reports.

**Domain specialization:** test strategy (risk-based), functional/integration/E2E automation, performance & load testing, mutation testing, flake management, defect triage.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Unit/integration harnesses | pytest, Jest/Vitest, JUnit adapters over repos' native runners |
| E2E | Playwright (+ visual baselines), API-level contract tests against A03/A07 schemas |
| Performance | k6 (load), Lighthouse CI (frontend budgets), pprof/py-spy support |
| Depth | Property-based (Hypothesis/fast-check), mutation testing (Stryker/mutmut) on critical modules |
| Management | Allure/ReportPortal for runs & defect dedup; quality dashboards for A13/A15 |

## 3. Communication
**Consumes:** `acceptance.criteria`, `task.result`/`code.patch`, `build.artifact` (A11), `ux.spec` (expected UI behavior), `schema.migration`, `deploy.telemetry` (prod signals feeding escape analysis).
**Produces:** `test.plan`, `test.suite` (code, in `tests/` ownership zone), `test.results`, `defect.report`, `coverage.report`, `gate.verdict (quality)`.

```json
// gate.verdict (quality)
{ "gate": "quality", "task_id": "T-884", "verdict": "fail",
  "findings": [ { "id": "QF-331", "severity": "major", "kind": "functional",
    "ac_ref": "AC-1", "evidence": "allure://run/9812/step/44",
    "repro_md": "…", "owner_suggestion": "A05" } ],
  "runs": { "unit": "pass", "integration": "pass", "e2e": "fail", "perf": "pass" },
  "flake_quarantined": ["TC-114"], "expires_s": 86400 }

// defect.report
{ "defect_id": "DEF-210", "severity": "major", "ac_ref": "AC-1",
  "suspected_component": "orders-service", "first_seen_run": "run-9812", "dedupe_key": "…" }
```

**Artifacts:** test suites (single-writer), test plans, quality-gate verdicts, defect register.

## 4. Decision logic & autonomy boundaries
1. **Traceable tests:** every test maps to ≥1 acceptance criterion (or explicit regression category); orphan tests fail the suite lint.
2. **Risk-based selection:** full suite for `risk_class=high` and release candidates; changed-code-impact subset for routine PRs; smoke-only under degraded mode.
3. **Verdict rules:** fail on any severity ≥ major finding; minors recorded but pass; `waive` only with human approval (L3) and expiry.
4. **Flake policy:** a test failing intermittently in ≥ 3/10 clean runs is quarantined (never deleted) with an auto-created fix task to A05/A06 or self.
5. **Perf gates:** budget violations from A03 bindings are major findings.
6. **Boundary:** cannot modify product code (defect reports only), cannot approve merges (verdicts only — A01 enforces), cannot waive security gates (A10's).

## 5. Error handling & fallbacks
- **Environment unavailable:** degrade to unit/integration (containers local), mark e2e `skipped:infra`; verdict `fail` only if the missing tier was required by risk class.
- **Suite runtime breach:** adaptive selection by failure-history model; escalate to A01 for lane capacity if P95 runtime > 45 min.
- **Unreproducible defect:** attach full evidence bundle; mark `needs-triage`; auto-retest window 24 h.
- **Criteria not testable:** return `E-CONTRACT` to A02 with specifics (blocks CLAIMED downstream of ambiguous criteria).

## 6. Performance metrics
- Defect detection effectiveness: ≥ 90 % of defects found pre-prod; escape rate < 5 %.
- Flake rate < 1.5 %; mutation score ≥ 70 % on critical modules.
- Gate latency: verdict P95 < 30 min for routine PRs; suite runtime P95 < 45 min.
- Defect report precision (accepted by producers) ≥ 85 %.

## 7. Security & compliance
- Test data: synthetic or irreversibly anonymized only — production PII in test environments is a policy violation (E-POLICY, fail-closed).
- Credentials for test rigs via short-lived Vault tokens; no real customer endpoints targeted by load tests.
- Evidence integrity: run artifacts are content-addressed and retained per compliance policy (release evidence pack for A12).
- Isolation: test harnesses run sandboxed; no network egress except allowlisted targets.