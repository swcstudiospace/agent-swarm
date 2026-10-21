---
name: "a30-custody-compliance--travel-rule-readiness"
description: "Use when a custody.compliance task asks if transfers carry FATF R.16 originator and beneficiary data above the regime threshold; uses synthetic cases."
disable-model-invocation: true
---

# Travel Rule readiness

## When to Use

- A `custody.compliance` task asks whether a custody or exchange flow is ready to exchange Travel Rule data (FATF Recommendation 16 for virtual assets), or whether a transfer template and counterparty process are complete.
- This skill checks field completeness and writes the readiness checklist. It does not contact counterparty VASPs, transmit personal data or decide a legal question; a legal threshold dispute goes to the compliance officer.
- List screening is `skill://a30-custody-compliance--sanctions-screening-process`; the whole regulatory bundle is `skill://a30-custody-compliance--mica-evidence-pack`.

## Procedure

1. Establish the regime and the threshold from the task: the regulation, the `threshold` amount and its currency, and whether the wording is "at or above" or "above" (`comparison`). Reference points to confirm with the compliance officer, not to assume: FATF INR.16 para 5 lets a country adopt a de minimis of at most USD/EUR 1,000 (below it both names and an account or reference each still travel, para 5(a)) and words qualifying transfers as "above any applicable threshold", so equality is for the regime to settle; the US rule (31 CFR 1010.410(f)) applies at USD 3,000 or more; the EU transfer-of-funds regulation (2023/1113) covers crypto-asset transfers of CASPs from 30 December 2024 and has no de minimis for them (EUR 1,000 only triggers the self-hosted-address ownership check, Art. 14(5)). No named regime is BLOCKED needs=input.
2. Read the transfer template or message schema in the repository with `read` or `grep` (look for originator and beneficiary fields) and map each field to the five the tool checks: `originator_name`, `originator_account`, one originator identifier (`originator_identifier_kind` of `geographic_address`, `national_id`, `customer_id` or `birth_date_and_place`, plus `originator_identifier`), `beneficiary_name`, `beneficiary_account`.
3. Build synthetic cases for `custody_travel_rule` only (never real names, ids or accounts, and never pass personal data of real customers into a tool): below the threshold, exactly at it, just above with complete data, above with each of the five fields missing in turn, and an identifier given with `originator_identifier_kind: "none"`.
4. Call `custody_travel_rule` once per case with `amount`, `threshold`, `comparison` and the six party fields. Expected: `status: not_applicable` (`applies: false`, `required_fields: 0`) below the threshold, and at the threshold only when `comparison` is `at_or_above`; below the threshold a missing originator or beneficiary name or account still yields an info finding `below-threshold-missing-<field>` (FATF para 5(a)), never a gap in `missing_fields`; `complete` above with all five present; `incomplete` with the missing key in `missing_fields` and one major finding `missing-<field>` per gap. Kind `none` with a non-empty identifier is INVALID_INPUT, which is the intended guard.
5. Cross-check each `custody_travel_rule` result: when `applies` is true, `present_fields + len(missing_fields)` equals `required_fields` (5) and the `findings` count equals the missing count; when false, `missing_fields` is empty and every finding is an info `below-threshold-missing-<field>`. The tool checks presence only, never accuracy or verification. Any deviation from your expectation in step 4 is E-CONTRACT: report the case and both values.
6. Add the process checks the tool cannot see, as a checklist in `custody/compliance/travel-rule-readiness.md` (`write`): counterparty VASP due diligence before the first transfer; a data format agreed with counterparties (IVMS 101 is the common interoperable model); handling of a transfer to or from a self-hosted wallet (the EU rule requires ownership verification above EUR 1,000; confirm the current text); what happens when data is missing or the counterparty does not respond (hold the transfer; never release on incomplete data); retention and protection of the personal data.
7. When a flow lets a transfer above the threshold proceed with `status: incomplete`, record it as a blocker and call `shared_escalate` with `{task_id, reason_code: "policy", summary: "Travel Rule data gap on a live flow", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the checklist, then see Handover.

## Outputs

- `custody.evidence.pack` section: case table (inputs, `status`, `missing_fields`), the regime and threshold used and the process checklist.

## Failure Handling

- INVALID_INPUT (field above 128 characters, identifier kind and value contradict, a key-shaped value): fix the named field once, then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; an unchecked case is not complete.
- Thresholds and effective dates above are recalled, not tool output: a reviewer verifies them before they are quoted to anyone. The tool checks presence only, never accuracy, never that a counterparty received the data.

## Handover

- `to_agent`: `A12` (compliance readiness is part of its release decision); `A10` when a gap is a blocker. `goal`: confirm readiness before go-live; `decisions`: regime, threshold, comparison; `artifacts`: checklist with digest; `open_questions`: unconfirmed legal thresholds; `verification`: partial while thresholds are unconfirmed.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/travel-rule-readiness.md; edit the source and run scripts/build_agents.py -->
