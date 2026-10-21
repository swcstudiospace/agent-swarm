---
name: a10-security--risk-exception
description: Use when someone asks to suppress or accept a finding and you must check the allow-list entry, grade the risk, and route medium to A01 and high to a human.
owner: a10-security
tools: [sec_allowlist_check, sec_cvss_band, shared_escalate, shared_handover_validate, shared_handover_write]
---

## When to Use
- A requester (A05, A06, A01) proposes an allow-list entry for `sec_gate`, asks to accept a finding for a release, or an entry in an allow-list is expiring.
- This is the only decision path for exceptions. It does not scan; scanning is `skill://a10-security--pr-gate`.
- You may block (L2) but never accept risk yourself: medium needs A01 with a mitigation plan, high and critical need an L4 human (A10 prompt, decision logic; agents A10 section 4, rule 3).

## Procedure
1. Read the allow-list file the requester supplied with `read`. `sec_gate --allow-list` takes a JSON list of `{id, justification, expires?}` (an object `{"allow": [...]}` or a bare id string without justification also loads, and the latter is refused). You do not create or edit that file.
2. Call `sec_allowlist_check` with `entries` (all of them, `expires` as `YYYY-MM-DD`), `finding_ids` (the `SEC-` ids in the current verdict's `payload.findings` and `payload.suppressed`) and `as_of` (today, `date -u +%F` through `bash`). Read the result: `unjustified` entries make the file invalid (`valid` false; the script refuses a blank justification and this tool also treats whitespace-only text as blank), `expired` entries stop applying on their expiry day, `no_expiry` entries are not time-boxed, `suppressed_ids` is what a run would hide, `unused` entries match no current finding. A duplicate id is INVALID_INPUT.
3. For each finding the exception covers, call `sec_cvss_band` with its base `score`, or take the gate severity when no score exists. Band `none`, `low` and `medium` stay in the medium path; `high` and `critical` (and any KEV-listed finding) are the high path.
4. Medium path: require a mitigation plan, a non-empty justification and an `expires` date; an entry in `no_expiry` is returned for a date. Recommend the entry to A01, who approves within policy. Applying it is the runner's step: `swarm_gate` has no `allow_list` parameter and `scripts/sec_gate.py` cannot be run through `bash` in omp, so you cannot confirm the id under `suppressed` yourself. Ask the runner to re-run with `--allow-list`.
5. High or critical path: do not recommend suppression. Call `shared_escalate` with your task's `task_id`, `reason_code` "policy", a `summary` stating the finding id, band and exposure, and `needs` `["human-approval", <evidence items: finding id, rule, CWE, location, score, KEV status, mitigation, proposed expiry>]`. Then stop; the answer is BLOCKED until a human replies.
6. Never mark a finding suppressed in your own result unless a recorded verdict's `payload.suppressed` contains it and the approval is recorded; `suppressed_ids` from `sec_allowlist_check` only predicts it.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- A decision per finding id: `recommended-to-A01`, `escalated-L4`, `rejected` (no justification, expired) or `expires-soon`, with the check that decided it.

## Failure Handling
- INVALID_INPUT naming `entries`, `as_of` or a bad date: ask the requester for a corrected file once, then BLOCKED needs=input.
- Duplicate ids in the file: ambiguous expiry, return it unchanged to the requester.
- `shared_escalate` DEPENDENCY_UNAVAILABLE after two retries: report BLOCKED needs=human-approval in the result by hand.
- Self-approval, or waiving your own gate, is never allowed.

## Handover
`shared_handover_write` to A01 for medium items (for high items too, naming the human decision as `remaining`; the `owner` returned by `shared_escalate`, A01 or A14, is who tracks it) with `goal` (decide on the listed exceptions), `completed` (checks run), `remaining` (approval or human decision), `decisions` (path chosen and band), `constraints` (expiry required, no self-approval), `blockers` (items awaiting L4), and `verification` {status `verified`; checks `sec_allowlist_check:valid=<bool>`, `sec_cvss_band:<band>`}.
