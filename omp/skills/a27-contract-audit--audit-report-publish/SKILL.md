---
name: "a27-contract-audit--audit-report-publish"
description: "Use when an audit.report task must assemble the final report from findings and fix review and pass the completeness check before release or publication."
disable-model-invocation: true
---

## When to Use

- All findings are written (`skill://a27-contract-audit--finding-writeup-poc`) and fixes were reviewed (`skill://a27-contract-audit--fix-verification-reaudit`); a `draft` report can be made earlier.
- Not for the release decision (A12) or the security gate (A10). Publishing outside the organization is a human step.
- Assembling the report is L2. A severity change is L3; accepting a high or critical risk is L4 and appears in the report only as a recorded human decision.

## Procedure

1. Run `python3 scripts/audit_findings_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path audit/findings.json --json`. For a `final` report exit 0 is required; exit 1 lists `major` or `blocker` entries (open critical or high, missing PoC, `risk-acceptance-missing`) to resolve first; exit 2 is an input error.
2. Call `audit_dedupe_findings` with `findings` = `[{id, title, category, file, severity, function, root_cause}]` from the file. `duplicate_count` must be 0 for publication; any group with `severity_conflict` true is BLOCKED `needs=human-approval` (L3), never resolved by you. Cross-check `input_count` with the script `findings_total`.
3. Call `audit_fix_verification` with the findings' `fix_status`, `fix_commit`, `regression_test`, `reaudited`. Take `residual_level`, `residual_pct`, `release_ready` for the fix-review section; `unverified` greater than 0 is stated in the report, not hidden.
4. `write` `audit/report.md` with the required sections in order: `executive_summary`, `scope` (scope_commit, in-scope files, out-of-scope), `methodology` (tools and versions, manual review, campaign numbers from `skill://a27-contract-audit--fuzz-campaign-review`), `severity_definitions` (OWASP impact x likelihood; impact high at a loss of 10 % of TVL or more, medium at 1 % or more), `findings_summary`, `findings_detail`, `disclaimer`; optional `appendix`, `fix_review`, `tooling`, `change_log`. The disclaimer states that an audit is not a guarantee.
5. Call `audit_report_completeness` with `stage` (`draft` or `final`), `scope_commit` (the full 40 or 64 hex id from the scope manifest; a short id is INVALID_INPUT), `sections` (the headings you wrote), `findings` = `[{id, severity, has_location, has_recommendation, has_poc, fix_status}]` derived from the file, not from memory. Read `sections_missing`, `poc_missing`, `risk_acceptances_needed`, `findings_incomplete`, `completeness_pct` and `verdict`.
6. Decide: a missing required section is medium (`findings_detail` high); a high or critical without a PoC is high; at `final`, an open high or critical is high; `acknowledged` or `wontfix` on high or critical needs an L4 acceptance: each one without a `risk_acceptance` reference is BLOCKED, call `shared_escalate` with `task_id`, `reason_code` `policy`, `summary`, `needs` `["human-approval"]` and return its `result_patch`. Do not publish `final` while the verdict is `fail`.
7. Cross-check totals: `findings_total` from step 5 equals script `findings_total` and `unique_count` from step 2; the severity counts in the summary equal script `by_severity`. Call `shared_verdict` with `severities` of unresolved findings mapped to the gate scale (info to `info`, low and medium to `minor`, high to `major`, critical to `critical`); it must be `fail` exactly when the `audit_report_completeness` verdict you state is `fail` (a `warn` maps to `pass`).
8. Call `shared_artifact_digest` with `path` `audit/report.md` and the findings file; the digests go into the report's `change_log` and the handover. Report `stage` honestly: `draft` until step 6 passes.

## Outputs

- `audit.report` (markdown at the path the task names, default `audit/report.md`) with completeness result and the unresolved-risk list; the findings file it was built from (`audit.findings`).

## Failure Handling

- INVALID_INPUT (`scope_commit` not a full id, duplicate finding ids, unknown section name): fix the field; sections must be the tool's names. More than 150 findings do not fit one `audit_report_completeness` call (the fix and dedupe tools take 200): BLOCKED `needs=input`, never drop findings to fit. When the findings list is truncated (`REP-TRUNCATED`), the counts stay exact; read the omitted ones from the findings file.
- PARTIAL_SUCCESS: when fix review or fuzz review is missing, publish only a `draft` and name the gap.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no `final` without `audit_report_completeness`.
- Two sources disagree (counts): E-CONTRACT, no report until they match.

## Handover

- `to_agent`: `A12` (release decision). Other consumers (A15 docs, A26, A28, A30) read the same artifact. `artifacts`: report and findings with digests. `blockers`: pending L3/L4. `verification`: verified only when `completeness_pct` is 100 and the verdict is not `fail`.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/audit-report-publish.md; edit the source and run scripts/build_agents.py -->
