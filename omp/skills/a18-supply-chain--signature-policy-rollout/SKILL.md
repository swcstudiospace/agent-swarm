---
name: "a18-supply-chain--signature-policy-rollout"
description: "Use when a signature verification policy (required signers, issuer and identity globs, transparency log) must be designed and dry-run before enforcing."
disable-model-invocation: true
---

# Signature policy rollout

## When to Use

- A new or changed policy for which signers may sign which images or attestations (Sigstore keyless identities or key ids) needs a rule set and a staged rollout plan; the evidence is a `supply.attestation.bundle`.
- Not for verifying one artifact: `skill://a18-supply-chain--provenance-verify-on-consume`. Not for installing the admission controller: A24 enforces cluster policy from your rules, A22 delivers it.
- Writing and dry-running rules is L2 (`image_policy`). Switching a production environment to enforce, or any exception, is L3 (human approval). Creating, rotating or trusting a new signing key or CA is L4 (`signing_key_change`); you never hold keys.

## Procedure

1. Use `read` to collect inputs: the repositories or image paths in scope, the CI workflows that sign them, and the issuer. For GitHub Actions keyless signing the issuer is `https://token.actions.githubusercontent.com` and the signer identity is the workflow URL with ref (`https://github.com/<org>/<repo>/.github/workflows/<file>@refs/heads/main`). Missing any of these: BLOCKED needs=input.
2. Draft `rules`, each `{name, issuer, identity, required}`: one required rule per release workflow with the narrowest glob that still matches (the glob `*` matches any run of characters). A rule whose `identity` is only `*` or ends in `/*` over a whole organization is rejected by this skill: tighten it. `min_valid_signatures` counts distinct (issuer, signer) pairs, and any signature that did not verify is a blocker even when others did (house rules of the tool, not Sigstore semantics). Set `min_valid_signatures` to 1 (2 when the policy demands a second independent signer) and `require_transparency_log` true for keyless signatures, as Sigstore's Rekor entry is what ties them to a time.
3. Dry-run against known good evidence: call `supply_signature_policy` with `signatures` (`signer`, `issuer`, `verified`, `in_transparency_log`, taken from `cosign verify` output run with `bash` or supplied; `verified` true only when the verifier said so) for a release that must pass. Expect `verdict` pass, `required_rules_missing` empty and `unexpected_signers` empty.
4. Dry-run `supply_signature_policy` against evidence that must fail: a signature from an unlisted workflow, one with `in_transparency_log` false, one with `verified` false. Expect respectively `signature.unexpected-signer` (minor), `signature.not-in-transparency-log` (major) and `signature.verification-failed` (blocker); `signature.required-signer-missing` (blocker) when no required rule matches and `signature.too-few` (major) below the minimum. A negative case that passes means the rule is too broad: fix it before going on.
5. Cross-check with `supply_attestation_bundle`: for the same artifact digest pass the attestations (`predicate_type`, `subject_digest`, `signed`, `signature_verified`) and `required_kinds` the policy demands (`provenance`, `sbom`, `vex`, `vsa`, `vuln-scan`). A signer policy that passes while `complete` is false means signatures exist but the required attestation kinds are absent: report both.
6. Plan the rollout as stages in the report: audit (log only, measure `verdict` over real traffic; promote when the false-fail rate is 0 over the observation window named in the policy), warn, then enforce; each stage names its owner and the rollback (return to the previous stage). Use `shared_verdict` with the dry-run severities as the evidence line.
7. Enforce on production, an exception for an unsigned legacy image, or any key or trust change: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "signature policy enforce awaiting L3 approval", "needs": ["human-approval"]}` (use `needs` `["human-approval", "key-owner"]` for a key change) and return its `result_patch` as BLOCKED. Call `shared_artifact_digest` for the rules file and report.

## Outputs

- `supply.attestation.bundle`: rules (JSON), dry-run results for pass and fail cases, rollout stages, open L3/L4 items.

## Failure Handling

- INVALID_INPUT on `rules` (empty issuer or identity) or `signatures`: fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: cite `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- `cosign` not installed: the signatures were not verified here; mark the dry-run partial.
- Never retry `shared_escalate`; a repeated escalation duplicates the request.
- Rekor or trust-root specifics beyond the tool's evaluation: defer to tool output and the Sigstore documentation, say so.

## Handover

- `to_agent`: A24 (enforces cluster admission). `goal`: apply the rules in audit mode; `completed`: dry runs; `decisions`: stage plan; `artifacts`: rules and report with digests (producer A18); `blockers`: `human-approval`; `constraints`: no enforce on production without L3; `verification`: partial until a real audit window.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/signature-policy-rollout.md; edit the source and run scripts/build_agents.py -->
