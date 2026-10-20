---
name: "a30-custody-compliance--key-ceremony-plan"
description: "Use when a key.ceremony task needs a witnessed ceremony plan and evidence checklist for key generation, shard distribution or restore; plan only."
disable-model-invocation: true
---

# Key ceremony plan

## When to Use

- A task asks for the plan, script or evidence checklist of a key ceremony: `key_generation`, `key_rotation`, `shard_distribution` or `recovery_restore`.
- The ceremony itself is L4 and human-only. You never see, generate, transport or store key material; you only handle labels, evidence references and public identifiers.
- Rotating an existing signer set (schedule, on-chain swap order) is `skill://a30-custody-compliance--key-rotation-plan`; a drill of recovery is `skill://a30-custody-compliance--recovery-drill`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` to get the `release.plan` (A12: freeze windows, dates) and the signer or shard design (`custody.policy.set`). Pick one `ceremony_type`; a task mixing types is split into one plan per type.
2. For a secret-sharing design (Shamir or MPC shares), call `custody_shard_distribution` with `threshold: t` and `shards: [{shard_id, custodian, organization, country}]` (`country` is ISO 3166-1 alpha-2 in capitals, one organization per custodian). `fail` stops the plan: `threshold-one`, `single-custodian-quorum` and `single-organization-quorum` are blockers; `single-jurisdiction-quorum` and `no-loss-tolerance` are major. Cross-check `loss_tolerance == n - t`, `compromise_tolerance == t - 1` and that `min_organizations_for_quorum` is at least 2.
3. Fetch the catalogue by calling `custody_ceremony_checklist` with `ceremony_type`, `min_witnesses` and `items: []` (if an empty list is refused, send every item with `done: false`, `evidence_ref: ""`, `witness_count: 0`). Its `verdict` is `fail` by construction (every item open): use only `required_items` and `missing_items`. `required_items` must equal the catalogue size 10 (`key_generation`), 9 (`key_rotation`), 8 (`shard_distribution`) or 7 (`recovery_restore`); anything else is E-CONTRACT. Keep `missing_items` as the ceremony script's step list.
4. Choose `min_witnesses` for `custody_ceremony_checklist`: the policy value, else 2 independent witnesses (3 for a root or cold key), none of them a key holder of that ceremony. These items need that many witnesses (a rule of the tool; the number is this skill's default): `participants-identified`, `hardware-integrity-verified`, `offline-environment-attested`, `entropy-source-attested`, `public-key-recorded-out-of-band`, `backup-restore-verified`, `backup-custody-receipted`, `media-sanitized`, `shard-integrity-verified`, `shard-custody-receipted`, `restore-verified-against-public-key`, `old-key-material-retired` and `ceremony-record-signed`; every other item needs only a done flag and an evidence reference.
5. `write` `custody/ceremonies/<ceremony_id>.plan.md`: one section per `missing_items` entry with who performs it, who witnesses, the `evidence_ref` identifier to record (ticket or document id, never a secret), abort criteria, and the order for rotation (new key added on-chain before the old is removed). State that mainnet use of any new key waits for a passing re-score.
6. Run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path custody --json` and require `secret_findings: 0`; a hit means a ceremony document holds key-shaped text: remove it before anything else.
7. Define the after-action gate: the humans return the filled record; you call `custody_ceremony_checklist` again. `completion_pct` of 100, `verdict: pass` and empty `missing_items` are required before the key is used; any `item-open`, `missing-evidence` or `insufficient-witnesses` finding keeps it unused.
8. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "key ceremony needs L4 human execution", needs: ["human-approval"]}`. Call `shared_artifact_digest` for the plan, then see Handover.

## Outputs

- `key.ceremony.plan`: script, witness matrix, evidence references and the scoring gate; BLOCKED needs=human-approval.

## Failure Handling

- INVALID_INPUT naming `items` (item outside the type's catalogue, listed twice) or a key-shaped `evidence_ref`: fix that field once, then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction` and name the unscored step. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; an unscored ceremony is not complete.
- A human asks you to receive, hold or check a key, share or seed: refuse and record it; L4.

## Handover

- `to_agent`: `A12` (it schedules the human ceremony). `goal`: run the ceremony and return the record; `completed`: plan, shard check; `remaining`: the ceremony and the re-score; `decisions`: `ceremony_type`, `min_witnesses`; `artifacts`: the plan with its digest; `blockers`: `human-approval`; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/key-ceremony-plan.md; edit the source and run scripts/build_agents.py -->
