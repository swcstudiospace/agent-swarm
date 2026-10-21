---
name: a30-custody-compliance--sanctions-screening-process
description: Use when a custody.compliance task needs the sanctions screening process defined or its records audited for list freshness and the action per match state.
owner: a30-custody-compliance
tools: [custody_sanctions_screening, shared_open_findings, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Sanctions screening process

## When to Use

- A task asks to define how customers, counterparties and destination addresses are screened (OFAC SDN, EU consolidated list, UN, UK OFSI), or to audit screening records already produced by a provider.
- You assess records and write process rules. You do not screen with a provider key, clear a match, release, block or report: those are compliance-officer and L4 fund-moving actions.
- Travel Rule data is `skill://a30-custody-compliance--travel-rule-readiness`; one transaction is `skill://a30-custody-compliance--tx-policy-evaluation`.

## Procedure

1. Fix the scope from the task: subject labels (`subject_id`: an internal id or public address, never a name or document number), the lists in use (`list_name`) and the regime. Without a named regime or list, BLOCKED needs=input; do not pick one.
2. Choose the freshness limits and write them down: `max_list_age_hours` (this skill's proposal 24: the list a screening used must be at most a day old when it ran) and `max_screening_age_hours` (proposal 24 for a `clear` that gates a release). A stricter limit from the compliance officer wins.
3. Call `custody_sanctions_screening` with `as_of` (UTC timestamp with a zone), `max_list_age_hours`, `max_screening_age_hours` and `screenings: [{subject_id, list_name, list_updated_at, screened_at, result, action}]` where `result` is `clear`, `potential_match`, `confirmed_match` or `error` and `action` is `none`, `released`, `held`, `blocked` or `reported`. Up to 200 records per call; split larger sets.
4. Apply the decision rules to the `custody_sanctions_screening` `findings`. Blockers stop everything and escalate: `released-on-error`, `potential-match-released`, `confirmed-match-not-blocked` (fail closed: an errored screening is never released; a confirmed match whose action is not `blocked`, `reported` included, is a blocker). Major: `stale-list`, `error-unactioned`, `potential-match-unactioned` (a potential match is under review only with action `held` or `blocked`). Minor: `stale-screening` (rescreen before release), `error-retry-pending`, `confirmed-match-report-pending` (blocked, not yet reported). Info: `potential-match-under-review`, `clear-but-withheld` (record why).
5. Cross-check the `custody_sanctions_screening` output: the four `counts` sum to `screened`; your own tally of `result` values equals `counts`; the number of `stale-list` findings equals `stale_lists`. When `omitted_findings` is above 0 the list is truncated at 100 findings: say so and rerun on smaller batches. A mismatch is E-CONTRACT.
6. For gate-linked records, call `shared_open_findings` with `{task_id}` to see whether a failing compliance verdict already covers the same subjects; do not duplicate a finding already open, cite its id.
7. `write` the process rules in `custody/compliance/sanctions-process.md`: when screening runs (onboarding, before an address is allowlisted, each outbound transfer, rescreen on every list update), the action matrix (clear: release; potential match: hold for four-eyes human review; confirmed match: block, then report: OFAC expects a report of blocked property within 10 business days, 31 CFR 501.603; EU and UK channels per the compliance officer), error handling (hold and retry) and record retention (at least 5 years under the usual AML rules; confirm with compliance).
8. For any confirmed match or unresolved blocker call `shared_escalate` with `{task_id, reason_code: "policy", summary: "sanctions screening blocker needs compliance-officer action", needs: ["human-approval"]}`; never mark a subject cleared. Call `shared_artifact_digest` for the rules file, then see Handover.

## Outputs

- `custody.evidence.pack` section: screening audit (counts, stale lists, findings by id) and the process rules.

## Failure Handling

- INVALID_INPUT (screened after `as_of`, list version newer than the screening, an identifier shaped like key material): fix the named record once, then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unassessed batch. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; unscreened subjects are held, not cleared.
- Legal deadlines and list names differ per jurisdiction: defer to the compliance officer and to the tool output; never state a legal conclusion.

## Handover

- `to_agent`: `A10` (the compliance evidence feeds its gate). `goal`: review the screening audit; `decisions`: limits chosen; `artifacts`: rules file with digest; `open_questions`: regime, owner for reporting; `blockers`: `human-approval` for confirmed matches; `verification`: verified only if all cross-checks agreed.
- End with `shared_handover_validate`, then `shared_handover_write`.
