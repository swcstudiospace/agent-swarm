---
name: a09-reviewer--risk-routing
description: Use when the changed paths are known and the review must choose auto-approve or full semantic review and decide whether an A10 co-sign is required.
owner: a09-reviewer
tools: [shared_handover_bundle, rev_sensitive_paths, rev_auto_approve_check, rev_risk_tier]
---

## When to Use
- The list of changed paths and the line counts are available (after `a09-reviewer--size-intake`).
- You must decide the route (decision_logic items 2 and 3) and set `co_sign_required` before any finding exists.
- Not for reading contract content: `a09-reviewer--contract-conformance` does that.

## Procedure
1. Take the changed paths, `added` and `deleted` from `a09-reviewer--size-intake`. Call `shared_handover_bundle` with the gated target's `task_id` (verdict rows are stored per `gate_for` target): in `verdicts[]` the row with gate `security` (and its `signature`, `expired`, `findings`) gives the SAST count from A10 (context only, never A10's verdict); the author's first-pass rate comes from the assignment inputs.
2. Call `rev_sensitive_paths` with `paths`. Keep `categories`, `flagged_paths` and `co_sign_required` (true for auth, payments, pii, iac).
3. Call `rev_auto_approve_check` with `added`, `deleted`, `changed_paths`, `sast_findings` (the A10 row's `findings`), `tests_present` (true when the changed paths include a test file) and `first_pass_pct`. If the A10 row is absent or `expired`, or the first-pass rate is not in the inputs, do not invent a figure: skip the call, route to full semantic review and list the figure under UNVERIFIED. Read `route` and `failed_criteria` (`diff_size`, `restricted_paths`, `sast`, `tests`, `first_pass_rate`).
4. Call `rev_risk_tier` with `categories`, `diff_lines` (from step 3 or added plus deleted), `blocking_count` 0, `max_severity` "none", `co_sign_required`. This tier is provisional: `a09-reviewer--verdict-assembly` recomputes it with real findings.
5. Route from `rev_auto_approve_check`. `auto_approve` true: the L2 auto-approve path, which still ends in `swarm_gate`, whose script runs the rules-only checks. Anything else: full semantic review. `co_sign_required` true: A10's co-sign must exist before approval is recorded; A01 enforces the conjunction, and a high-risk approval at L3 also needs human policy.

## Outputs
- Route (`auto_approve` or `full_semantic_review`), provisional `risk_tier` with `reasons`, `categories`, `co_sign_required`, `failed_criteria`.

## Failure Handling
- Any tool error or missing figure: fail closed to full semantic review, never to auto-approve.
- `rev_risk_tier` INVALID_INPUT naming `co_sign_required`: the categories include auth, payments, pii or iac, so set it true and call again.
- You never approve your own class's output and never override A08 or A10.

## Handover
- No handover of its own: the route, `categories`, `co_sign_required` and the provisional tier feed `a09-reviewer--verdict-assembly`.
- On cancel or pause, hand over per `swarm-cooperative-cancel`: `decisions` (route and tier with reasons), `constraints` ("A10 co-sign required before approval" when set), `open_questions` (figures that were unavailable).
