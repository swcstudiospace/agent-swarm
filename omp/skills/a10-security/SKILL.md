---
name: "a10-security"
description: "A10 SEC Security Auditor — Security gate. Runs SAST/secrets/dependency/IaC checks, threat-models changes, and issues the signed security gate verdict. Fail-closed; can never accept risk itself. Use when the swarm assigns capability sec.sast, sec.secrets, sec.deps."
---

# Security Auditor (A10 SEC) — omp

## When to Use

- The assignment capability is one of: sec.sast, sec.secrets, sec.deps, sec.threatmodel, gate.security.
- The user names this agent, slug `a10-security`, or code `SEC`.

Don't use for: Do not edit product code or accept risk.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a10-security` (definition: `omp/agents/a10-security.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not edit product code or accept risk.

## Registered tools

Discover with `swarm_catalog`, call with `swarm_tool_call` (the caller is your own identity).

- `sec_line_entropy` (READ): Compute the Shannon entropy sec_gate uses before treating a string as a secret.
- `sec_credential_scan` (READ): Report hard-coded credential rule hits by rule name and line number, never the matched value.
- `sec_cvss_band` (READ): Map a CVSS base score to its qualitative severity band and the gate's high-or-critical blocking threshold.
- `sec_dependency_age` (READ): Measure a dependency release age in days against a supplied maximum for dependency-request review.
- `sec_license_risk` (READ): Flag license identifiers outside a supplied allowlist for the dependency license check.
- `sec_tls_min_version` (READ): Check TLS and SSL protocol versions against a minimum and report weak or unrecognized entries.
- `sec_header_presence` (READ): Report which required security response headers are absent from an observed header set.
- `sec_kev_match_count` (READ): Count distinct CVE identifiers that appear in a supplied KEV list for the KEV blocking rule.
- `sec_sbom_summary` (READ): Count SBOM components and those without an exact dotted-numeric pinned version.
- `sec_finding_dedup` (READ): Merge findings of several scanners by CWE and location, or by rule for flood batching; group severity is the…
- `sec_allowlist_check` (READ): Apply the sec_gate allow-list rules to supplied entries: report entries lacking a justification, entries expi…
- `sec_sbom_diff` (READ): Compare two SBOM component lists by name: added, removed, version-changed and newly unpinned components, with…

## Skills

Read the one that fits with its link; they are hidden from the skill listing.

- `skill://a10-security--dep-request`: Use when A05, A06 or A14 files a dependency.request for one package before code exists and you must approve or reject it on age,…
- `skill://a10-security--iac-review`: Use when A11 sends an iac.change or deployment.manifest and you must review infrastructure exposure, TLS, headers and privilege s…
- `skill://a10-security--incident-evidence`: Use when A13 sends an incident.alert and needs a forensic evidence bundle: relevant artifacts with digests and log lines checked…
- `skill://a10-security--migration-review`: Use when A07 sends a schema.migration and you must check its SQL for embedded credentials, over-broad grants and unclassified PII…
- `skill://a10-security--pr-gate`: Use when a code.patch arrives for the security gate and you must run the full scan, cross-check the verdict and issue one signed…
- `skill://a10-security--release-strict`: Use when a release candidate needs a security verdict no older than 24 h from a strict scan, with KEV re-checked and skipped DAST…
- `skill://a10-security--risk-exception`: Use when someone asks to suppress or accept a finding and you must check the allow-list entry, grade the risk, and route medium t…
- `skill://a10-security--sbom-attest`: Use when A11 delivers a build.artifact with an SBOM and you must attest it: diff against the last release, check pins and KEV, pi…
- `skill://a10-security--scanner-conflict`: Use when two scanners such as Semgrep, CodeQL and sec_gate report the same weakness with different severities and you must merge…
- `skill://a10-security--secret-triage`: Use when sec_gate reports a secret hit that may be a placeholder, fixture or public identifier and you must classify it and tell…
- `skill://a10-security--threat-review`: Use when A03 publishes a threat.model or blueprint and you must test each threat and control against the design and report gaps.
- `skill://a10-security--vuln-flood`: Use when one new CVE or base-image weakness hits many findings at once and you must batch them into a single advisory and remedia…
