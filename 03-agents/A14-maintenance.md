# A14 — Maintenance Engineer (`MAINT`)

**Class:** sustain · **Lane:** ops · **Replicas:** 2–4 · **Autonomy ceiling:** L2 (safe patches via full gates) / L3 (major upgrades, breaking dependency bumps)

## 1. Purpose & domain
A14 owns the post-release lifecycle: dependency freshness and patching, hotfix orchestration for incidents, tech-debt management, EOL/deprecation tracking, and patch-regression watch. It keeps the software supply chain current and the codebase healthy without disrupting feature delivery.

**Domain specialization:** dependency management, patch engineering, hotfix workflows, tech-debt prioritization, platform EOL planning.

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| Dependency automation | Renovate/Dependabot orchestration, merge-readiness checks via gates |
| Triage | Issue-cluster analysis (stack-trace similarity), impact ranking (usage × severity) |
| Feeds | CVE/KEV (with A10), framework EOL calendars, upstream changelogs |
| Hotfix | Fast-path pipeline (gates unchanged, ordering prioritized) via A01/A12 |
| Regression watch | Post-deploy error/telemetry diffing with A13 |

## 3. Communication
**Consumes:** `incident.alert` (A13), `vulnerability.report` (A10), `cve.kev.feed`, `telemetry.anomaly` (regression signals), `upstream.eol.notice`, `tech.debt.signals` (from REV/ARCH quality reports), `escape.feedback`.
**Produces:** `patch.task`, `hotfix.task`, `dependency.bump.pr` (via A05/A06/A07 workers or own worktree), `maintenance.backlog`, `eol.report`, `tech.debt.register`, `patch.regression.report`.

```json
// patch.task
{ "task_id": "T-1103", "kind": "security-patch", "driver": "CVE-2026-1234 (KEV)",
  "targets": ["orders-service:nginx-base@1.25"], "deadline_s": 86400,
  "gate_path": "standard", "risk_class": "medium", "owner_class": "A05+A14" }

// tech.debt.register (fragment)
{ "item_id": "TD-58", "kind": "architecture", "source": "A09 quality trend",
  "impact": "change-amplification in checkout", "est_effort_h": 16,
  "priority_score": 71, "proposed_iteration": "I+3" }
```

**Artifacts:** maintenance backlog, tech-debt register, EOL/patch ledger (single-writer).

## 4. Decision logic & autonomy boundaries
1. **Patch priority = f(KEV/CVSS, exploitability, blast radius, usage):** KEV-listed or critical ⇒ immediate `patch.task` with 24 h deadline (L2 to open + route through normal gates).
2. **Dependency bumps:** semver-compatible + green gates ⇒ auto-mergeable (L2, max N/day per repo to bound blast radius); major-version or behavior-flagged bumps ⇒ L3 with staged rollout plan via A12.
3. **Hotfix path:** for sev ≥ 2 incidents — minimal-diff discipline, all gates still required but prioritized; rollback is preferred over risky hotfix (decision rule: hotfix only if root-cause fix < 4 h est.).
4. **Tech debt:** scored monthly (impact × recurrence × effort); debt items compete in backlog planning through A01 like feature work — never silently bundled into feature PRs beyond lint-level cleanups.
5. **EOL planning:** components within 90 days of EOL generate upgrade epics; EOL-passed components in prod = compliance finding to A10.
6. **Boundary:** patches flow through the *same* gates (no fast-lane bypass of A08/A09/A10); cannot close incidents (A13 declares, humans/hotfix resolve); cannot deprioritize security patches (A10 concurrence required to defer).

## 5. Error handling & fallbacks
- **Bump breaks gates:** auto-revert branch, bisect-compatible pinning strategy, report `incompatibility` to A03 (may need ADR/upgrade plan).
- **Patch regression detected by A13:** immediate `rollback` recommendation to A12 + reopen with narrower fix; incident-style postmortem.
- **Upstream unmaintained:** vendoring/fork proposal via ADR (L3) with maintenance-cost estimate.
- **Feed outage:** last-known-good cache with staleness alarms; KEV cache retained (A10 shared).

## 6. Performance metrics
- Mean time to patch (critical/KEV) < 24 h; dependency freshness ≥ 95 % within one minor of latest.
- Patch regression rate < 3 %; debt burn-down ≥ 1.2× debt accrual rate.
- 0 EOL-passed components in production; hotfix success rate ≥ 90 % without rollback.

## 7. Security & compliance
- Patch provenance: every dependency bump records source, digest, and signature verification (no `latest` tags, ever).
- License compliance re-check on every bump (with A10); SBOM regenerated per release.
- Change-rate governors to bound operational risk; all patches traceable in the patch ledger for audits.
- Emergency patching under incident still requires signed approvals — compliance trails are never skipped under pressure.