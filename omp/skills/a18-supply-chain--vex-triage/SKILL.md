---
name: "a18-supply-chain--vex-triage"
description: "Use when A10's vulnerability.report lists findings in components that are present but not exploitable and you must issue evidence-backed OpenVEX statements."
disable-model-invocation: true
---

# VEX triage

## When to Use

- A `vulnerability.report` from A10 lists CVE/GHSA findings and you can show, per finding and product, that the component is absent, the code is not reachable, or the issue is fixed; the output is `vex.statements` (OpenVEX) and the effective exposure.
- A10 owns the gate and the vulnerability list; you never accept risk, never waive a finding and never edit the report. A VEX statement is evidence A10 and A14 may use, not a verdict. Mass triage ordering is `skill://a10-security--vuln-flood`; risk acceptance is `skill://a10-security--risk-exception`.
- Drafting statements is L2. Anything that says "accept" or "ignore" is L3 for a human, via A10.

## Procedure

1. Call `shared_handover_read` with the A10 `task_id` and use `read` on the `vulnerability.report`. Build `vulnerabilities` as `[{id, product, severity}]`: `id` is the CVE or GHSA id, `product` the package URL (purl) of the affected component, `severity` one of critical, high, medium, low, unknown. No purl in the report: take it from the SBOM (validate it first with `supply_sbom_validate` on `document`; `blocking_count` above 0 means the SBOM cannot map products, stop) or BLOCKED needs=input.
2. For each candidate, gather evidence with `grep` and `read`: the component is not in the SBOM or lockfile (`component_not_present`); the vulnerable function or module is never imported (`vulnerable_code_not_present`); it is present but not on any execute path (`vulnerable_code_not_in_execute_path`); no adversary can control the input (`vulnerable_code_cannot_be_controlled_by_adversary`); a mitigation exists (`inline_mitigations_already_exist`). Those five are the OpenVEX `not_affected` justifications. No evidence: status `under_investigation`, never `not_affected`.
3. Use `write` to draft `statements`: `vulnerability`, `products` (purls; a purl without version covers every version and one without qualifiers covers every variant, so give the exact version and, for arch- or distro-specific packages, the same `arch`/`distro` qualifiers as the vulnerable product: a statement naming `arch=arm64` never suppresses `amd64`), `status` (`not_affected`, `affected`, `fixed`, `under_investigation`), `justification`, `impact_statement` (one sentence with the evidence path), `timestamp` ISO-8601 with an explicit offset. Critical or high severity `not_affected` needs evidence from two independent checks (for example the SBOM and a code search).
4. Call `supply_vex_apply` with `vulnerabilities` and `statements`. Check `ignored_statements` (must be 0: each is a `vex.not-affected-unjustified` major finding, fix and rerun), `unmatched_statements` (statements whose product or vulnerability matched nothing: a typo in the purl, fix or drop), `exposed_count`, `suppressed_count` and `verdict` (`exposed` when anything remains). Each exposed row is also a finding whose id is the vulnerability id and whose severity maps critical to blocker, high to major, medium and unknown to minor, low to info, so `blocking_count` counts exposed critical and high plus ignored statements.
5. Cross-check the `supply_vex_apply` output: `exposed_count` plus `suppressed_count` must equal `total` and `total` must equal the number of rows you sent. The `exposed` and `suppressed` listings are cut at 48 KB (the counts are always totals): batch large reports. Exposed `status` is `affected`, `under_investigation` or `unassessed` (no statement): list each `unassessed` row with its severity, worst first. Compare the exposed list with A10's own count in the report; a different figure is E-CONTRACT, report both, state no exposure.
6. In `supply_vex_apply` the newest statement per pair wins and equal timestamps resolve to the worse status: when you replace an older statement, give it a later `timestamp`. Never backdate.
7. Call `shared_verdict` with the `severity` of every entry in `findings` as evidence only (the gate verdict is A10's); it must agree with `blocking_count` (fail exactly when above 0). Save the statements as a file with `write` and call `shared_artifact_digest` with its path.

## Outputs

- `vex.statements`: OpenVEX statements file (digest), effective exposure (`exposed`, `suppressed`), counts, evidence per `not_affected`, and the `unassessed` list.

## Failure Handling

- INVALID_INPUT naming `statements` (bad timestamp offset, unknown justification) or `vulnerabilities` (duplicate id and product): fix the named row once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; more than 300 findings: split into batches and cross-check totals across batches.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; exposure is then unknown, not zero.
- Evidence conflicts between tool and code search: keep `under_investigation`.
- Whether a status is defensible legally or contractually is not decided here: defer to A10.

## Handover

- `to_agent`: A10 (consumes `vex.statements`; A14 consumes them for patch planning, so name A14 in `remaining`). `goal`: reflect the effective exposure in the gate; `completed`: counts; `decisions`: each `not_affected` with justification; `artifacts`: statements with digest (producer A18); `open_questions`: `unassessed` critical/high; `verification`: verified only when step 5 agreed.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/vex-triage.md; edit the source and run scripts/build_agents.py -->
