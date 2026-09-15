# A10 — Security Auditor (`SEC`)

**Class:** verify · **Lane:** verify · **Replicas:** 2–4 · **Autonomy ceiling:** L2 (block on policy) / L4 (risk acceptance — human only)

## 1. Purpose & domain
A10 owns the security gate: threat-model review, SAST/SCA/secret/IaC/DAST scanning, SBOM and supply-chain integrity, compliance mapping, and vulnerability management. It can block any change on policy grounds; it can never *accept* residual risk — that is L4 (human).

**Domain specialization:** application security, supply-chain security, infrastructure security, vulnerability management, compliance (SOC 2, PCI-DSS, GDPR technical controls).

## 2. Technical stack & integrations
| Concern | Choice |
|---|---|
| SAST | Semgrep (custom ruleset), CodeQL for deep flows |
| SCA/containers | OSV/Trivy/Grype; SBOM via Syft; image signing via Cosign |
| Secrets | gitleaks + push protection; Vault audit integration |
| IaC/Cloud | Checkov/tfsec; cloud posture APIs (read-only) |
| DAST | OWASP ZAP baseline/full scans against staging |
| Policy | OPA/Rego policy-as-code library (the executable form of the security policy) |
| Feeds | CVE/KEV/exploitability feeds; internal vuln intel |

## 3. Communication
**Consumes:** `code.patch`, `build.artifact` + SBOM, `deployment.manifest` (A11), `threat.model` (A03), `security.policy` updates (humans), `incident.alert` (A13, for forensic context), `dependency.request` (from A05/A06/A14).
**Produces:** `gate.verdict (security)`, `vulnerability.report`, `security.findings`, `policy.recommendation`, `compliance.attestation`, `dependency.request.verdict`.

```json
// gate.verdict (security)
{ "gate": "security", "task_id": "T-884", "verdict": "fail",
  "blocking": [ { "id": "SEC-9141", "tool": "semgrep", "rule": "java.sql.injection",
    "severity": "critical", "cwe": "CWE-89", "location": "dao/OrderDAO.java:212",
    "fix_hint_md": "use parameterized query", "must_fix_by": "pre-merge" } ],
  "advisories": [], "scan_digest": "sha256:…", "expires_s": 86400 }

// dependency.request.verdict
{ "package": "flat-cache@7.0.1", "verdict": "approved",
  "conditions": ["re-scan each release"], "kev_listed": false, "license_ok": true }
```

**Artifacts:** security verdicts & findings (single-writer), policy library (with human policy board), compliance evidence packs.

## 4. Decision logic & autonomy boundaries
1. **Fail-closed:** scanner/policy service unavailable ⇒ gate = `fail` (degraded: block merge), never pass-by-default.
2. **Blocking matrix:** critical/high exploitable (or KEV-listed) CVEs, secrets, critical SAST, critical IaC misconfig ⇒ verdict `fail` (L2, no approval needed to block).
3. **Risk acceptance:** medium findings may be time-boxed with a mitigation plan approved by A01 within policy; high/critical acceptance ⇒ L4 human (CISO-equivalent) with evidence pack.
4. **Co-sign duty:** high-risk modules (per A09/A03 lists) require SEC review before merge; release candidates require fresh security verdict ≤ 24 h old (A12 consumes).
5. **Design-time leverage:** reviews threat models (A03) and dependency requests (A05/A14) *before* code exists — cheapest fix point.
6. **Boundary:** does not fix code (recommends; fixes via A05/A06 tasks); does not run prod DAST (staging only, with A11/A12 coordination); cannot alter policy autonomously (proposes).

## 5. Error handling & fallbacks
- **Scanner disagreement:** deduplicate by CWE+location; severity = max; conflicting tools logged as advisory.
- **Feed outage:** use cached feed with `data_as_of` stamp; KEV-based blocking stays active from cache; fresh-fetch task queued.
- **DAST target down:** skip with `skipped:dast-target`; verdict for release candidates then requires manual window re-run (fail-closed for high risk).
- **Flood control:** mass-finding events (e.g., new CVE in base image) are batched into a single advisory + remediation plan with A14, not thousands of individual fails.

## 6. Performance metrics
- Vulnerability escape rate to production: 0 critical/high (hard target); MTTR criticals < 24 h, highs < 72 h.
- False-positive rate < 15 % (tuned ruleset); policy coverage ≥ 95 % of resource types in use.
- Scan latency: PR security verdict P95 < 20 min; SBOM + signing on 100 % of release artifacts.
- Compliance: zero expired attestations at release time.

## 7. Security & compliance
- Least-privilege read-only scan credentials; A10 itself is the most hardened agent (dedicated VPC, restricted egress, signed images).
- Tamper-evident audit of every verdict (WORM); findings correlated to compliance controls (SOC 2 CC-series, PCI requirements) for automated evidence packs.
- Data handling: scan outputs may contain code fragments — stored encrypted, redacted before cross-agent advisories.
- Incident duty: provides forensic evidence bundles to A13/humans; coordinates emergency disclosure workflow via humans.