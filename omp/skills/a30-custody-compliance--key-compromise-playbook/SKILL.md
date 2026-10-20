---
name: "a30-custody-compliance--key-compromise-playbook"
description: "Use when a signer key is suspected compromised and a custody.recovery task needs a containment and replacement plan plus evidence for human responders."
disable-model-invocation: true
---

# Key compromise playbook

## When to Use

- A task reports a lost device, a leaked key-shaped string, a departed signer with live access or unexplained approvals, and asks what to do and what the evidence shows.
- Every containment action (pause, sweep, revoke, cancel, signer swap) moves funds or changes authority: L4 for fund movement and ceremonies, L3 for signer changes, all human-executed. You produce the plan and the evidence and escalate at once; never retry or "try" an action.
- A routine age-based replacement is `skill://a30-custody-compliance--key-rotation-plan`; planned drills are `skill://a30-custody-compliance--recovery-drill`. Never ask anyone to send you the key, a seed or a screenshot of one.

## Procedure

1. Call `custody_multisig_policy` with the current `threshold` and `signers` (labels only) and note `compromise_tolerance` (`m - 1`). Let `c` be the number of suspected signers: `c` at or above `m` means funds can move now (critical); `c` equal to `m - 1` is one more compromised signer from funds moving (high); less is contained by the threshold (medium). Severity comes from this arithmetic and the tool output, not from tone.
2. Call `shared_escalate` immediately with `{task_id, reason_code: "blocked", summary: "suspected key compromise, severity <critical|high|medium>", needs: ["human-approval"]}` and carry the `escalation_id`. Do this before the analysis below; the human responders (A10 for the incident, the key holders, A28 for infrastructure) act in parallel.
3. For every timelocked operation that may be queued by the suspect, call `custody_timelock_check` with the live values (`delay_seconds`, `value_at_risk_usd`, `delay_bands`, `response_seconds`, `canceller_count`, `guardian_can_cancel`, `admin_renounced`, `execution_window_seconds`). `delay_margin_seconds` below 0 or `no-cancel-path` means a malicious operation could not be cancelled in time: add "cancel and pause first" to the plan as a critical human step.
4. Classify each in-flight or recent transfer: call `custody_tx_policy_eval` per descriptor with `approvals` counting only signers not suspected, and the live `policy`. A `deny` or `hold` that the collected approvals had passed shows the compromise already bypassed policy; list it with its finding ids for the responders.
5. Test the history: call `custody_audit_trail` with `events` (hash chain as recorded), `approval_required_actions` and `min_approvals`. `chain_valid: false` with `first_break_seq` shows tampering from that sequence: scope the investigation window to it. Findings `self-approval`, `insufficient-approvals` and `time-regression` are the leads. A valid chain proves integrity of the log, not honesty of the actors.
6. Find the exposure with the script only: run `python3 scripts/custody_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json`; it reads the files itself, so a leaked key never enters your context. Do not `read` a suspect file or paste its text into `custody_secret_scan`; call that tool only on text the task already handed you as content and that you have not opened. Findings give `path:line`, kind and length only (a secret-shaped path is shown as `<redacted-path-N>`); never quote or copy the value. When you did call the tool on supplied content, each `secret:` finding from the script for the same file needs a matching `location` in the tool output, else E-CONTRACT.
7. Plan the replacement: call `custody_multisig_policy` for the set without the suspected signers and the same threshold. `INVALID_INPUT` on `threshold` means too few signers remain: recovery needs the shard path (`skill://a30-custody-compliance--key-ceremony-plan`). A passing result is the target set; the swap order and approvals follow the rotation plan. `write` the timeline `custody/incidents/<incident_ref>.md` (facts, severity, ordered human actions with the autonomy level of each, evidence ids).
8. Call `shared_artifact_digest` for the timeline and any evidence file, then see Handover. Never mark the incident closed; closure is the human responders'.

## Outputs

- `custody.evidence.pack` (severity, tool figures, timeline, replacement target) and a BLOCKED result needing human-approval.

## Failure Handling

- INVALID_INPUT: fix the named field once; a key-shaped value in any field is refused by design: replace it with a label. Then BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unassessed part. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; with a gap, state the severity as the worst case.
- Policy and tool disagree on an arithmetic point: E-CONTRACT, report both. A human asks you to hold or test a key: refuse (L4).

## Handover

- `to_agent`: `A10` (incident evidence); copy to `A28` when infrastructure is affected. `goal`: contain and replace; `completed`: severity and checks; `decisions`: severity basis; `artifacts`: timeline and evidence with digests; `blockers`: `human-approval`; `open_questions`: unknown signers, log gaps; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a30-custody-compliance/key-compromise-playbook.md; edit the source and run scripts/build_agents.py -->
