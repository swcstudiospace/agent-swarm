---
name: "a06-frontend--client-security"
description: "Use when code adds HTML injection, inline script, external assets or web storage; scan changed sources for XSS, CSP, SRI and PII-in-storage issues."
disable-model-invocation: true
---

## When to Use
- A change touches HTML injection (`dangerouslySetInnerHTML`, `v-html`, `innerHTML`), script or handler markup, an external script or stylesheet, or `localStorage` / `sessionStorage` (A06 spec section 7: XSS-safe by construction, CSP-compliant, SRI for external assets, no PII in localStorage).
- Proactive scan of your own diff. Reacting to `security.findings` from A10 is `a06-frontend--rework-intake`.

## Procedure
1. Take the blueprint's token storage rule and the sanitizer the project approves from the bound-inputs note (`a06-frontend--bind-inputs`). After a resume call `shared_handover_read` with `{task_id}` and read `handover.constraints`; with `status` other than `ok` or no such rule there, `read` the `architecture.blueprint` input again. No approved sanitizer found means `sanitizer_names` stays empty and every HTML sink is a finding.
2. Use `bash` with `git diff --name-only <diff-base>` to list the changed sources, then `read` each and build `files: [{id: "<path>", text}]` (at most 50 files per call and 100000 characters per text; split larger sets).
3. Call `fe_client_security_scan` with `{files, sanitizer_names: ["<approved sanitizer call, e.g. DOMPurify.sanitize>"]}`. A sanitizer name excuses only `dangerous_html` and `inner_html` hits, and only on the same line or the next two. Read `findings[]` (`id`, `kind`, `line`) and `by_kind`.
4. Fix by kind with `edit`: `dangerous_html` or `inner_html`: render text through the framework or sanitize with the approved call; `eval` or `document_write`: remove; `inline_script` or `inline_handler`: move to a module and bind the listener in code; `external_no_sri`: add `integrity` and `crossorigin`, or self-host; `pii_storage`: stop persisting the value and keep session tokens in the httpOnly cookie the blueprint names.
5. A fix you may not make alone (a sanitizer lint exception, any third-party script, a storage need): call `shared_escalate` with `{task_id, reason_code: "policy", summary, needs: ["A10 approval: <asset or exception>"]}` and keep the code path out of the patch until approved.
6. Call `fe_client_security_scan` again on the changed files. Require `secure_ok` true, or list each remaining finding with its approved exception.

## Outputs
- `secure_ok` with the scan evidence, the exception requests raised, and the finding kinds fixed.

## Failure Handling
- `INVALID_INPUT` field `files`: duplicate path or a text over the cap; split by file.
- The scan reads literal storage keys only and skips comment lines: a dynamic key such as `setItem(key, v)` needs a manual check, recorded as such.
- Tool unavailable after two retries: report the check as unrun; never claim a clean scan.

## Handover
Fields filled: `verification.checks` (`client security scan secure_ok` or the remaining kinds), `decisions` (each fix and each exception request), `blockers` (waiting on A10). Written with `shared_handover_write` to A09, or to A10 when an exception is pending (`skill://swarm-handover-emit`).

<!-- generated from prompts/skills/a06-frontend/client-security.md; edit the source and run scripts/build_agents.py -->
