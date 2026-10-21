---
name: "a18-supply-chain--builder-compromise-response"
description: "Use when a CI builder, runner or signing identity is suspected compromised and every artifact built or signed in the exposure window must be scoped."
disable-model-invocation: true
---

# Builder compromise response

## When to Use

- An A10 `vulnerability.report`, an A13 incident or a pipeline alert says a builder id, runner pool, workflow or signing identity may be compromised, and you must list which artifacts depend on it. Output: a `supply.attestation.bundle` with affected and unaffected digests.
- A13 declares the incident and A12 freezes releases; A10 owns the security verdict. You never revoke, rotate or trust a key (L4 `signing_key_change`), never accept the risk and never delete images; quarantine and key actions are human steps.
- Routine verification of one healthy artifact is `skill://a18-supply-chain--provenance-verify-on-consume`.

## Procedure

1. Call `shared_handover_read` with the incident or A10 `task_id`. Fix the scope in one record: compromised `builder_id` (the provenance builder), signer identity and issuer, exposure window (start and end instants, `end` open if unknown), repositories. No window: assume the earliest plausible time and say so; BLOCKED needs=input if none can be justified.
2. Inventory candidate artifacts with `bash` and `read`: provenance files, registry listing (`skopeo`, `crane`, read-only) and handovers of builds in the window. Keep each artifact's digest (`sha256:<hex>`), push time and provenance path. An artifact without provenance cannot be cleared: classify it unknown, not clean.
3. For each artifact call `supply_provenance_match` with `provenance`, `artifact_digest`, `artifact_name`: only a matched subject can be attributed to a builder. Then call `supply_slsa_level` with `trusted_builder_ids` set to the trusted list MINUS the compromised builder, the verified facts as before and `required_level` the policy's. The expected effect is `slsa.l2-builder-untrusted`; an affected artifact is one whose `builder_id` equals the compromised one and whose time lies in the window, plus anything signed by the compromised identity.
4. Call `supply_signature_policy` with `signatures` (from `cosign verify` output run with `bash`), `rules` that exclude the compromised identity and `require_transparency_log` true: signatures from that identity become `signature.required-signer-missing` or `signature.unexpected-signer`, confirming dependence. Cross-check with step 3: an artifact flagged by one but not the other is E-CONTRACT, treat it as affected until explained.
5. Check survivors with `supply_attestation_bundle` (`artifact_digest`, `attestations`, `required_kinds` of the policy): `complete` true for a digest built by another builder outside the window means it can be cleared with its evidence cited; incomplete means unknown.
6. Protect evidence and deployed state: call `supply_retention_plan` with the registry `tags`, `protected_digests` set to every affected and deployed digest, a high `keep_latest`, `min_age_days` at least the window length, `max_delete_pct` 0 and `as_of`: no tag of an affected digest may appear in `delete_tags`, and `delete_pct` must be 0. This is a freeze of cleanup, not a cleanup.
7. Write the affected list (digest, builder, signer, reason, deployed where) with `write`, call `shared_artifact_digest` for it, then call `shared_escalate` with `{task_id, "reason_code": "blocked", "summary": "builder compromise: quarantine, key revocation and rebuild need human decisions (L4)", "needs": ["human-approval", "key-owner"]}` and return its `result_patch` as BLOCKED. Rebuild on a clean builder after human approval is A11's task, and the new artifacts go through `skill://a18-supply-chain--provenance-verify-on-consume`.

## Outputs

- `supply.attestation.bundle`: scope (builder, signer, window), affected, cleared and unknown digests with the tool evidence, protection plan result, open L4 actions.

## Failure Handling

- INVALID_INPUT naming `provenance`, `signatures` or `tags`: fix once, else the digest is `unknown` (never dropped).
- PARTIAL_SUCCESS: use `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; the affected list is then incomplete and says so.
- Evidence you did not see (builder logs, key usage records) is not inferred: mark it a question for A10.
- Never retry `shared_escalate`; never run `cosign` or registry commands that revoke, sign, tag or delete.

## Handover

- `to_agent`: A10 (security verdict and bundle consumer); A12 and A24 read the same bundle for freeze and admission. `goal`: decide quarantine and key actions; `completed`: scope and lists; `decisions`: classification rules used; `artifacts`: affected list with digest (producer A18); `blockers`: `human-approval`, `key-owner`; `open_questions`: unknown digests; `verification`: partial.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/builder-compromise-response.md; edit the source and run scripts/build_agents.py -->
