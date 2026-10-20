---
name: "a29-dapp-wallet--signing-prompt-accessibility"
description: "Use when a dapp.signing task builds the confirmation shown before a wallet prompt and it must be readable, phishing-resistant and WCAG 2.2 accessible."
disable-model-invocation: true
---

# Signing prompt accessibility

## When to Use

- A `dapp.signing` task renders what the user must understand before signing: action, recipient or spender, amount, chain, origin, deadline, risk.
- Layout, theming and generic components are A06 and A04; this skill checks the content and the accessibility of the signing and transaction prompts only. Changing what a prompt tells the user is a signing-flow change (L3); the code is L2.

## Procedure

1. `read` `ux.spec` and `design.system.tokens` (A04). List every prompt screen: connect, message sign, typed-data sign, approval, send, status. Each must state the action in words, the counterparty, the amount with token symbol, the chain name and the app origin. A missing element is BLOCKED needs=input for A04, not an invention.
2. Call `dapp_signing_request_scan` with the `requests` the screens represent (`method`, `to`, `data`, `origin`, `typed_domain_verifying_contract`), `known_contracts`, `expected_domains`, `link_urls` (every link a prompt shows) and `hardcoded_addresses` (literals in the prompt copy). Findings the prompt must surface: `SIGN-METHOD` on `eth_sign` (blocker) or `eth_signTransaction` (major), `SIGN-UNLIMITED`, `SIGN-SPENDER-UNKNOWN` (blocker), `ORIGIN-*`, `LINK-*` and `ADDRESS-CHECKSUM`. The prompt text for each finding id comes from the scan summary, not from memory.
3. Call `dapp_approval_risk` with `approvals` and `known_spenders` for the approval screens and use `unlimited_count`, `nft_blanket_count` and `unknown_spender_count` to choose the warning variant (none, unlimited, blanket NFT, unknown spender). Cross-check: a screen marked safe while `verdict` is `block` is E-CONTRACT.
4. Check the markup (`grep`, `read`) against WCAG 2.2: the prompt is a dialog (`role="dialog"` or `<dialog>`, `aria-modal`, focus moves in and returns to the trigger, `Escape` closes); status changes use `role="status"` or `aria-live` (4.1.3); a risk is never colour only (1.4.1) and carries an icon and text; text contrast is at least 4.5:1 (3:1 for large text) and UI component boundaries 3:1 (1.4.3, 1.4.11); targets are at least 24 x 24 CSS px (2.5.8); focus is not hidden behind the sticky bar (2.4.11).
5. `grep` for hard-coded values: colours, spacing and radii come from `design.system.tokens`; find hex colours and pixel sizes in the prompt components and replace them with token references. Measure contrast on the token pair, and list pairs you could not measure.
6. `edit` the confirmation (WCAG 3.3.4, financial transactions): a separate explicit confirm control after the summary, default focus on Cancel for approvals above `requested_amount`, never auto-submit. A countdown for an expiring permit must not trap the user (2.2.1): when the deadline nears, offer to refresh the request.
7. Write the screen table to `dapp/wallet-flow/prompts-<task_id>.md` and call `shared_artifact_digest` on it and on the patch.

## Outputs

- `wallet.flow.spec` (prompt content table, finding to copy mapping, accessibility checklist with measured and unmeasured items) and `code.patch`, citing `verdict` and counts from both tools.

## Failure Handling

- INVALID_INPUT (`data` over 16386 chars, `requests[].method` outside the enum): fix the field or BLOCKED needs=input. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The static checks cover markup and tokens, not screen reader behaviour: state that a manual pass by A08 or the project's a11y test run is still owed. WCAG criterion numbers come from WCAG 2.2; if the project targets another level, defer to `ux.spec`.
- Do not paste wallet-supplied text into the prompt as HTML.

## Handover

- `to_agent`: `A04`. `goal`: confirm copy, tokens and the checklist; `decisions`: warning variants; `artifacts`: spec and patch with digests (producer `A29`); `open_questions`: contrast pairs not measured; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/signing-prompt-accessibility.md; edit the source and run scripts/build_agents.py -->
