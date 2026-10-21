---
name: a30-custody-compliance--audit-trail-verify
description: Use when a custody.compliance task needs a custody action log proven intact: hash chain, sequence, approvals, with the first break located.
owner: a30-custody-compliance
tools: [custody_audit_trail, shared_open_findings, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Audit trail verification

## When to Use

- A task asks whether the log of custody actions (signer changes, allowlist edits, limit changes, rotations, sweeps) is complete, tamper-evident and properly approved, as input to an evidence pack or an incident.
- Read-only analysis of an exported log. You never connect to the system that holds it, and the log must hold identifiers only: a key-shaped actor or approver value is refused by the tool.
- Collecting the whole regulatory bundle is `skill://a30-custody-compliance--mica-evidence-pack`; an incident timeline is `skill://a30-custody-compliance--key-compromise-playbook`.

## Procedure

1. Locate the exported log with `glob` or `read` (the task names it) and call `shared_artifact_digest` with its `path` so the evidence pins the exact bytes verified. Each event needs `seq`, `at` (timestamp with zone), `actor`, `action`, `approvers`, `prev_hash` and `hash` (64 lowercase hex). Events with `|` or `,` inside `actor`, `action` or an approver cannot be hashed by the tool: report the export format as unsupported, do not edit the log. The hash recipe in step 5 is this tool's own convention, not a standard: a log chained with another recipe fails `hash-mismatch` on every event, which is a format mismatch to report, not evidence of tampering (tampering shows as a first break with intact events before it).
2. Set `approval_required_actions` and `min_approvals` for `custody_audit_trail` from the custody policy (for example signer change, allowlist addition, limit change, rotation, sweep; at least 2 independent approvals for these). Without a policy, BLOCKED needs=input; never invent the list.
3. Call `custody_audit_trail` with `events` (at most 300 per call), `approval_required_actions` and `min_approvals`. For a log longer than 300 events, split by `seq` and pass `genesis_hash` for each later chunk equal to the `hash` of the last event of the previous chunk (the first chunk uses the default 64 zeros). Check that `seq` is contiguous across the cut.
4. Apply the `custody_audit_trail` findings. Blockers (integrity): `genesis-mismatch`, `duplicate-seq`, `seq-gap`, `chain-broken`, `hash-mismatch`. Major (process): `time-regression`, `missing-actor`, `missing-action`, `self-approval` (actor among its approvers), `insufficient-approvals` (fewer than `min_approvals` independent approvers excluding the actor; names are compared trimmed, case-folded and NFKC-normalised, so `bob` and `Bob ` are one approver, and the same applies to `approval_required_actions`). `chain_valid: false` and `first_break_seq` are decisive: events before that `seq` are intact, events after it are untrusted until explained.
5. Cross-check by hand: use `bash` to recompute the SHA-256 of the string `seq|at|actor|action|approvers|prev_hash` (approvers sorted, joined with `,`, UTF-8) for one intact event before `first_break_seq` and for the event at it, for example `printf '%s' "<string>" | sha256sum`. The intact one must equal its stored `hash`. What the event at `first_break_seq` must show depends on its finding: `hash-mismatch` means the recomputed value differs from the stored `hash`; `chain-broken` means the recomputed value equals the stored `hash` (a re-hashed event) while its `prev_hash` differs from the stored `hash` of the event with the previous `seq`; for `seq-gap`, `duplicate-seq` and `genesis-mismatch` compare the `seq` numbers or the genesis value directly. Any other outcome is E-CONTRACT: report both values and state no verdict.
6. When `omitted_findings` is above 0 the tool listed only 100 findings: say so, rerun on smaller chunks (by `seq`) until every finding is seen, and never claim the log clean from a truncated list. Call `shared_open_findings` with `{task_id}` to avoid restating findings an issued verdict already holds; cite their ids.
7. `write` the report `custody/evidence/audit-trail.md`: log digest, event count, `chain_valid`, `first_break_seq`, findings by id, chunks used, what the check does not prove (a valid chain shows the log is unchanged, not that the recorded approvals were genuine or that nothing was left out before the genesis).
8. For any blocker finding call `shared_escalate` with `{task_id, reason_code: "conflict", summary: "audit trail integrity failure at seq <n>", needs: ["human-approval"]}`; a broken chain is never repaired by you. Call `shared_artifact_digest` for the report, then see Handover.

## Outputs

- `custody.evidence.pack` section: log digest, chain verdict, break location and process findings.

## Failure Handling

- INVALID_INPUT (hash not 64 lowercase hex, bad timestamp, a `|` or `,` in a field, key-shaped text): fix the named event reference only when it is an export mistake you can show; otherwise BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked chunk. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no chain verdict without the tool.
- The hash formula is the tool's own; a log chained with another scheme fails by design: report that, do not conclude tampering.

## Handover

- `to_agent`: `A10` (its gate consumes integrity evidence). `goal`: review the integrity result; `decisions`: policy actions and `min_approvals` used; `artifacts`: report and log digest; `open_questions`: omitted findings, pre-genesis history; `blockers`: `human-approval` on a break; `verification`: verified only if the hand recomputation agreed.
- End with `shared_handover_validate`, then `shared_handover_write`.
