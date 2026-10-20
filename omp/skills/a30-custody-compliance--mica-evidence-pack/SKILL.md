---
name: "a30-custody-compliance--mica-evidence-pack"
description: "Use when a custody.compliance task needs one evidence pack mapping fresh tool results to MiCA, DORA and Travel Rule controls as evidenced, partial or gap."
disable-model-invocation: true
---

# MiCA evidence pack

## When to Use

- A task asks for the pack a regulator, auditor or A10/A12 gate reads for a crypto-asset custody service operating in the EU: what controls exist and the proof of each.
- Evidence only. The pack never states "compliant"; that is a legal conclusion for the compliance officer and counsel. Nothing here touches keys or funds.
- Individual checks have their own skills (`skill://a30-custody-compliance--recovery-drill`, `skill://a30-custody-compliance--audit-trail-verify`); this skill assembles and re-verifies them.

## Procedure

1. Call `shared_handover_bundle` with `{task_id}` to list the upstream results and artifacts (the earlier `custody.policy.set`, `key.ceremony.plan`, A27 `audit.report`, A12 `release.plan`), then call `shared_artifact_check` with `{task_id}`: a missing file or a digest mismatch marks that item stale and it is re-produced, never reused.
2. Fix the control map in the pack with `write` (`custody/evidence/mica-control-map.md`), each row a control, its source and the evidence item. Reference set to confirm with counsel: MiCA, Regulation (EU) 2023/1114, Art. 70 (safekeeping of clients' crypto-assets, segregation) and Art. 75 (custody policy, register of positions, return of assets, liability); Art. 68 (governance arrangements); DORA, Regulation (EU) 2022/2554 (ICT risk, backup, restoration testing); Transfer of Funds Regulation (EU) 2023/1113 (Travel Rule). Cite the control map as this skill's mapping, not as a legal reading.
3. Re-run the numeric evidence at one `as_of` (UTC), reusing each check's inputs from its source artifact: `custody_multisig_policy` (`threshold`, `signers`, `tiers`), `custody_key_rotation` (`as_of`, `warn_days`, `keys`), `custody_recovery_drill` (`as_of`, `max_age_days`, `target_rto_minutes`, `required_scenarios`, `drills`), `custody_sanctions_screening` (`as_of`, `max_list_age_hours`, `max_screening_age_hours`, `screenings`) and `custody_audit_trail` (`events`, `approval_required_actions`, `min_approvals`). Evidence older than 30 days (this skill's rule) is re-run, not copied.
4. Mark each control from its tool result: `evidenced` when `verdict` is `pass`; `partial` when `warn`, when the tool was skipped (`skipped:tool-missing`), or when `omitted_findings` is above 0; `gap` when `fail` or no evidence exists. `custody_audit_trail` needs `chain_valid: true` for `evidenced`; `custody_recovery_drill` needs `counts.current` equal to `required_count`; `custody_key_rotation` needs `overdue_count` 0.
5. Cross-check each fresh result against its source artifact: a result that differs from the stored one (for example `overdue_count`, `signer_count`, `chain_valid`) is a change, not an error: list both values and the date, and keep the fresh one. A tool result that contradicts the `scripts/custody_policy_check.py` run of step 6 is E-CONTRACT.
6. Run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json`: `status: ok`, `secret_findings: 0` and the named coverage gaps (`files_skipped`, `skipped`, `truncated`) go into the pack. A secret finding makes the whole pack `gap` for key safekeeping and stops publication until `skill://a30-custody-compliance--key-compromise-playbook` runs.
7. Draft the pack index (`write` `custody/evidence/pack-<task_id>.md`): control table with status, evidence file, digest and date, open gaps with owner, scope statement (what is not covered: legal conclusions, off-chain procedures not observed). Call `shared_dod_check` with `{result: <draft>, task_id}` and resolve every `unknown` criterion or list it as open.
8. Call `shared_artifact_digest` for the index and every evidence file it names, then see Handover.

## Outputs

- `custody.evidence.pack`: index, control map, fresh tool results with `as_of`, script counts, digests and an explicit gap list.

## Failure Handling

- INVALID_INPUT from any check: fix the named field once from the source artifact; a missing input is BLOCKED needs=input and that control stays `gap`.
- PARTIAL_SUCCESS: use `completed_fraction` and mark the control `partial`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never mark such a control `evidenced`.
- Article numbers, applicability dates and the wording of the controls are recalled, not tool output: counsel verifies them before the pack is shared externally. No regulator-facing claim without that review.

## Handover

- `to_agent`: `A10` first (security and compliance gate); a second `shared_handover_write` to `A12` (release decision). `goal`: review the pack and its gaps; `decisions`: statuses and why; `artifacts`: index and evidence with digests; `open_questions`: counsel review, off-chain evidence; `verification`: partial until counsel review is recorded.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/mica-evidence-pack.md; edit the source and run scripts/build_agents.py -->
