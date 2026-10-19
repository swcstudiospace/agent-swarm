---
name: "a02-requirements--compliance-tagging"
description: "Use when a brief names regulated data or a regime (GDPR, CCPA, HIPAA, PCI-DSS, SOC2); map it to non-negotiable NFRs and prove the spec text is redacted."
disable-model-invocation: true
---

# Compliance Tagging

Maps the regimes found at intake to compliance NFRs that no gate may waive (spec section 7), and proves the
published text carries no PII. Intake only detects; this skill decides what the regime requires of the spec.

## When to Use

- `regimes_hint` from intake is not empty, or the brief names GDPR, CCPA, HIPAA, PCI-DSS or SOC2.
- The spec is about to leave A02 and its redaction has not been re-proved.

## Procedure

1. Call `shared_handover_read` with `task_id`; take the regimes from `handover.constraints` and the PII report from the intake `handover.artifacts`.
2. Use `read` on `docs/requirements/stories.md`, `docs/acceptance.md` and `docs/requirements/nfr.md` (the first and third are convention paths).
3. Call `req_pii_scan` with `text` set to the combined spec text. If `has_pii` is true, use `edit` to replace each occurrence with the matching `[REDACTED:<KIND>]` marker from `redacted_text`, then scan again until `has_pii` is false. Detected kinds are ssn, card (Luhn-valid), email, ipv4 and phone only; a name or address needs a manual read.
4. Use `edit` on `docs/requirements/nfr.md` to add, for each regime in `regimes_hint` or the brief, one NFR of kind `compliance` with a stable id (for example `NFR-C1`), the regime name, a testable obligation (such as "no card number stored", "deletion request completed within 30 days") and the tag `non-negotiable`. SOC2 never appears in `regimes_hint`: it comes from the brief.
5. Call `req_nfr_coverage` with `nfrs` as `{id, kind}` including the new compliance entries and `required_kinds` set to `["compliance", "security"]`. `complete` must be true.
6. Use `write` to save `docs/requirements/compliance.md` (convention path) listing each regime, its NFR ids and the final clean scan result, then call `shared_artifact_digest` with that `path`.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`.

## Outputs

- Tagged compliance NFRs (id, regime, obligation, `non-negotiable`).
- Redaction proof: the final `req_pii_scan` result with `has_pii` false.
- Request for a security review, stated in `constraints`; A02 never accepts security risk.

## Failure Handling

- A regime you cannot map to a testable obligation: add it to `open_questions`; never weaken or drop the NFR.
- `req_pii_scan` still reports PII after two edit rounds: BLOCKED needs=input, do not publish.
- `req_nfr_coverage` INVALID_INPUT: fix the unknown `kind` field it names (only perf, security, a11y, i18n, compliance exist).
- Tool TIMEOUT: retry once, then E-TIMEOUT.

## Handover

Fill `constraints` ("regimes: GDPR, PCI-DSS; compliance NFRs non-negotiable; security review requested from A10"),
`completed` (NFR ids tagged), `remaining` (publish gate), `artifacts` (kind compliance, path, digest, `producer` `A02`) and
`verification` (`checks`: the clean scan and the coverage result). Call `shared_handover_write` with `to_agent`
`A01` (an agent id, never the slug) so the orchestrator can route the security review.

<!-- generated from prompts/skills/a02-requirements/compliance-tagging.md; edit the source and run scripts/build_agents.py -->
