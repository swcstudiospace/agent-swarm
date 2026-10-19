---
name: "a10-security--secret-triage"
description: "Use when sec_gate reports a secret hit that may be a placeholder, fixture or public identifier and you must classify it and tell A05 what to fix."
disable-model-invocation: true
---

## When to Use
- The recorded gate verdict holds `secrets` findings (rules `aws-access-key`, `aws-secret-key`, `github-token`, `slack-token`, `stripe-live-key`, `google-api-key`, `private-key`, `jwt`, `generic-credential`) and at least one is ambiguous: a test fixture, documented example, hash, UUID or encoded id.
- This works on repository hits. Secrets found inside incident logs belong to `skill://a10-security--incident-evidence`.
- Never paste a credential value into any tool, note or handover. A `sec_gate` finding's evidence carries only a 4-character prefix and the length; that is all you may quote.

## Procedure
1. Call `shared_open_findings` with the `task_id` of the gated target (verdict rows are stored per `gate_for` target). It lists only failing, unexpired verdicts. Keep the entries whose `kind` is `secrets` (the summary starts with the rule name); note each `id` (`SEC-` hash), `location` and `severity` (critical). When `count` is 0 there is nothing to triage: stop.
2. For each hit open the line with `read` (or find it with `grep`) and classify from context before any tool: placeholder words that `sec_gate` already filters (`example`, `changeme`, `dummy`, `sample`, `fake`, `<...>`, `${...}`), a test or fixture path, documentation, or a real-looking value in application code or config.
3. Call `sec_credential_scan` with `lines` set to the hit line and its neighbours (at most 10000 lines of 4096 characters), every value overwritten by a same-length run of `x` first. Structural rules (`generic_secret_assignment`, `private_key_block` header) still fire on the mask and give a rule name and a 1-based line number within your list; format-specific rules (`aws_access_key_id`, `github_token`, `slack_token`, `stripe_live_key`, `google_api_key`, `jwt`) cannot, so zero hits is never proof of absence.
4. Only for `generic-credential` hits whose value you already judged to be a public identifier (commit SHA, UUID, content hash, fixture id), call `sec_line_entropy` with that string as `text`. `sec_gate` treats a value below entropy 3.2, or purely alphabetic, as non-secret; report the `entropy` figure against that threshold. If you cannot rule out a live credential, skip this call and classify the hit `real`.
5. From the `read` context and the tool results, classify each hit `real`, `placeholder` or `test-fixture`. Any format-specific key, private key block or value in production config is `real` unless it carries the documented example marker (such as `EXAMPLE`). Ambiguity left is `real` (fail-closed).
6. For `real` hits write the recommendation from the `shared_open_findings` ids (read-only; A10 has no write or edit tool): rotate and revoke first, then remove from code and history, then re-run the gate. Name the secret store the repo uses; do not change files yourself.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` (see Handover).

## Outputs
- A table of finding id, rule, `file:line`, classification, evidence (entropy figure or context note) and the recommended action.
- `placeholder` and `test-fixture` items are proposed for the allow-list with a justification; the request goes through `skill://a10-security--risk-exception`, never straight into the file.

## Failure Handling
- `sec_line_entropy` or `sec_credential_scan` PARTIAL_SUCCESS: keep the unscanned lines as unresolved findings.
- DEPENDENCY_UNAVAILABLE after two retries: report `skipped:tool-missing`, keep the hit blocking.
- INVALID_INPUT: fix `lines` (array of strings, at most 4096 characters each) or `text` once, then BLOCKED needs=input.
- A `real` classification never lowers the gate verdict: it stays `fail` until a new scan is clean.

## Handover
`shared_handover_write` to A05 (fix task) with `goal` (remove or rotate the listed secrets), `completed` (hits classified), `remaining` (rotation, removal, re-scan), `decisions` (classification with rationale per id), `artifacts` (none unless a report file exists in the workspace, then kind `security.findings` with its digest), `constraints` (no values in commits, history rewrite needs a human), `blockers` (ids classified `real`), and `verification` {status `partial` when any hit stayed unresolved, else `verified`; checks `sec_credential_scan:rules`, `sec_line_entropy:<figure>`}.

<!-- generated from prompts/skills/a10-security/secret-triage.md; edit the source and run scripts/build_agents.py -->
