---
name: "a18-supply-chain--license-exception"
description: "Use when SBOM components trip the license allow/deny policy and you must classify them and prepare a time-boxed exception request for human approval."
disable-model-invocation: true
---

# License policy check and exception request

## When to Use

- A component in an SBOM has a denied, missing or uncovered license (copyleft in a distributed image, a changed license after an upgrade) and the owner wants it kept; output is an `sbom.bundle` with license evidence and an exception request.
- Not for validating the SBOM itself: `skill://a18-supply-chain--sbom-attest-publish`. Not for choosing a replacement dependency: A03/A05. This is engineering evidence, not legal advice; legal reading of a license belongs to the human approver.
- Evaluating the policy is L2 (`image_policy`). Granting an exception is L3 (`policy_exception`) and always human: you prepare it, you never accept the risk.

## Procedure

1. Use `read` on the SBOM and call `supply_sbom_validate` with `document`: `blocking_count` above 0 or `component_count` 0 stops here. Licenses are only as good as the SBOM (the NTIA `ntia_elements` do not include licenses, so check `license` presence yourself in the next step).
2. Use `read` on the SBOM to build `components` as `[{name, license}]` (at most 500; split larger SBOMs and combine counts). `license` is the SPDX expression (`MIT`, `Apache-2.0 OR GPL-2.0-only`, `GPL-3.0-only WITH Classpath-exception-2.0`); copy CycloneDX `license.id` or `expression` and SPDX `licenseConcluded` (use `licenseDeclared` when concluded is `NOASSERTION`). Cross-check: `components` length must equal `component_count` from step 1 or the gap is listed.
3. Take policy from the task: `allow` and `deny` (SPDX ids), `deny_categories` from `strong-copyleft`, `network-copyleft`, `weak-copyleft`, `source-available`. No policy supplied is BLOCKED needs=input; never invent one. Call `supply_license_policy` with `components`, `allow`, `deny`, `deny_categories`.
4. Read `verdict`: `fail` when `denied_count` above 0 (`license.denied`, major), `review` when only `license.review` or `license.missing` or `license.unparseable` (minor) remain, `pass` otherwise. For `OR` expressions the tool evaluates the alternatives; `AND` needs every part allowed; `X+` is resolved only for GPL/LGPL/AGPL ids, any other `X+` is `review`. Check `allowed_count` plus `denied_count` plus `review_count` equals `total`.
5. Resolve each `review` item with `read`: the package's LICENSE file or metadata in the vendored source or lockfile. A resolved id moves the component to `allow` evidence; an unparseable string is rewritten to a valid SPDX id only when the file proves it, else it stays `review`. Never guess a license.
6. For a change between releases call `supply_sbom_diff` with `before` and `after`: every `license_changed` entry (`before`, `after`) must appear among the components you evaluated, and a component that moved from allowed to denied is new evidence for the owner (`sbom.license-changed`, minor). `downgrade_count` above 0 is reported with it.
7. For each remaining denied component write the exception request with `write`: component and version, expression, which rule denied it, how it is used (linked, dynamically loaded, only in a build stage, distributed or internal), scope (image, repository), proposed expiry (30 to 90 days is this skill's convention, the approver decides) and the replacement plan. Call `shared_artifact_digest` for the file.
8. Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "license exception awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` as BLOCKED. Without an exception request the denied components simply stay failing.

## Outputs

- `sbom.bundle` (license section): counts, per-component status and evidence path, exception request file with digest, `verdict`.

## Failure Handling

- INVALID_INPUT naming `components`, `license` or a list: fix once (empty list, license over 300 characters), else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- SPDX id the tool does not know: it reports `license.unparseable` or review; defer to the SPDX License List and state which id.
- Never retry `shared_escalate`; never mark an exception as granted from your own output.

## Handover

- `to_agent`: A10 (consumes `sbom.bundle`; the human approver reads the request). `goal`: decide the exception; `completed`: policy result; `decisions`: components resolved with source; `artifacts`: request and bundle with digests (producer A18); `blockers`: `human-approval`; `verification`: verified when step 4 totals matched.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/license-exception.md; edit the source and run scripts/build_agents.py -->
