---
name: "a29-dapp-wallet--siwe-login"
description: "Use when a dapp.signing task adds or reviews Sign-In with Ethereum (EIP-4361) so the message, domain binding, nonce and validity window are verified end to end."
disable-model-invocation: true
---

# Sign-In with Ethereum

## When to Use

- A `dapp.signing` task builds a login that signs an EIP-4361 message and exchanges it for a session.
- Structured data is `skill://a29-dapp-wallet--typed-data-signing`. The server-side signature verification and session store belong to A05; this skill covers the message the client builds. A login message that mentions assets or permissions is a signing-flow change (L3).

## Procedure

1. `read` `api.contract` (A03) for the nonce and verify endpoints, then `grep` the client for the message builder. The nonce must come from the server per login attempt (at least 8 alphanumeric characters, single use). A client-generated nonce is a defect: record it.
2. With `bash` or a unit test, capture the exact message the builder emits for a fixed account. Messages use LF line endings only.
3. Call `dapp_siwe_check` with `message`, `expected_domain` (the RFC 3986 authority this app serves, from config), `expected_chain_id`, `now`, `expected_address` (the connected account), `expected_nonce` (the value the server issued) and `max_age_seconds` (tool default 600). Blockers: `SIWE-DOMAIN-MISMATCH` (a phishing site can replay the message), `SIWE-ADDRESS-MISMATCH`, `SIWE-NONCE-MISMATCH`, `SIWE-EXPIRED`, `SIWE-NOT-YET-VALID`, `SIWE-ADDRESS-MALFORMED`, `SIWE-CHAIN-MALFORMED`.
4. Majors: `SIWE-STATEMENT-ASSETS` (a login statement must not talk about approvals or transfers), `SIWE-SCHEME-INSECURE` (not https), `SIWE-DOMAIN-USERINFO` (a `user@` before the domain), `SIWE-URI-HOST`, `SIWE-VERSION` (must be 1), `SIWE-NONCE-WEAK`, `SIWE-ISSUED-STALE`/`-FUTURE` (house policy: older than `max_age_seconds`, or more than 60 s of clock skew), `SIWE-EXPIRATION-ORDER`, `SIWE-LINE-ENDINGS`, `SIWE-ADDRESS-CHECKSUM`. `SIWE-NO-EXPIRATION` and `SIWE-CHAIN-LEADING-ZERO` (valid grammar, non-canonical spelling) are minor: set `Expiration Time` anyway. Read `issued_age_seconds` and `expires_in_seconds` to confirm the window.
5. Negative runs of `dapp_siwe_check`: re-run step 3 with a different `expected_domain`, a replayed `expected_nonce` and a message with `Issued At` older than `max_age_seconds`; each must produce the matching blocker or major, or the builder is not enforcing it.
6. Call `dapp_signing_request_scan` with `requests` `[{"method": "personal_sign", "origin": <page origin>}]`, `expected_domains`: any `ORIGIN-*` or `LINK-INSECURE` finding means the login page itself is untrusted; stop.
7. Run `python3 scripts/dapp_wallet_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <frontend dir> --json`: `DAPP-KEY-STORAGE` and `DAPP-KEY-LOG` must be zero, and no session token or secret goes to web storage or console (keep it in an httpOnly cookie set by the server). `edit` the builder; contract wallets sign with ERC-1271 (and ERC-6492 when counterfactual), so the verify endpoint must not assume an EOA: note it for A05.
8. Call `shared_artifact_digest` on the patch and the sample messages, then hand over.

## Outputs

- `code.patch` (message builder, login UI call) and `wallet.flow.spec` (message fields, nonce flow), citing `verdict`, `domain`, `issued_age_seconds`, `expires_in_seconds`.

## Failure Handling

- INVALID_INPUT (`message` over 8192 chars, `expected_chain_id` out of range): fix or BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool does not verify the signature or the nonce store; say so and leave both to A05. If `api.contract` and the builder disagree on the nonce source, E-CONTRACT.
- Never log a signed message together with a session identifier.

## Handover

- `to_agent`: `A09`. `goal`: review the login flow; `completed`: positive and negative runs; `decisions`: expiry and max age; `artifacts`: patch with digest (producer `A29`); `open_questions`: server verification owed by A05; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/siwe-login.md; edit the source and run scripts/build_agents.py -->
