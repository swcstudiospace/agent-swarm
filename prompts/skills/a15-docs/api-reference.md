---
name: a15-docs--api-reference
description: Use when an api.contract is new or its endpoints changed and the api.reference must be rendered from it, covering every endpoint and bound to its digest.
owner: a15-docs
tools: [shared_handover_bundle, shared_artifact_check, shared_artifact_digest, shared_escalate, docs_api_endpoint_coverage, docs_link_targets, docs_anchor_check, shared_handover_validate, shared_handover_write]
---

## When to Use
- `api.contract` arrives from A03, or a `code.patch` changes public endpoints (capability `docs.api`).
- Not for decisions and their statuses: use `a15-docs--adr-render`. Not when the contract file is absent: use `a15-docs--missing-artifact-stub`.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; take the contract artifact (`kind`, `uri`, `digest`, `producer`) from `artifacts`. No contract listed means `a15-docs--missing-artifact-stub`.
2. Call `shared_artifact_check` with `task_id`, `outputs` (the contract artifact, `producer` included) and `agent` (its producer). Read `results[].status`: `ok` continues; `no_digest` continues but you say in the handover that the producer declared no digest; `missing` goes to `a15-docs--missing-artifact-stub`; `unchecked`, `digest_mismatch` or `producer_mismatch` goes to step 7.
3. Call `shared_artifact_digest` with `path` the contract file; keep the `digest` (`sha256:<hex>`).
4. Use `read` on the contract and `write` the reference (the path the task `outputs[]` names): one section per endpoint, headed `## <METHOD> <path>`, with parameters, request and response taken from the contract only (single source rule, 03-agents/A15-docs.md section 4.1). Put `bound_artifact: <contract path>` and `bound_digest: <digest>` in the front matter (a convention that `a15-docs--staleness-sweep` reads).
5. Use `grep` with pattern `^#+ (GET|POST|PUT|PATCH|DELETE) ` on the reference, then call `docs_api_endpoint_coverage` with `total_endpoints` (every `METHOD /path` in the contract), `documented_endpoints` (the same strings from the headings) and `target_pct` 100. Add every `undocumented` endpoint; remove or justify each `unmatched_documented` one.
6. Call `docs_link_targets` with `files` (repo-relative paths of the docs plus every file the reference links to) and `links` (`{source, target}` per link, found with `grep` pattern `\]\([^)]*\)`), and `docs_anchor_check` with `headings` (the heading texts of the page a fragment points at, in order) and `references` (the fragments); fix every `broken` entry. When the reference is final, call `shared_artifact_digest` with `path` the reference for the handover.
7. If the contract itself is inconsistent or its digest does not match, do not edit it: call `shared_escalate` with `task_id`, `reason_code` `conflict`, `summary` and `needs` (the upstream fix). Docs never modify contracts.

## Outputs
- `api.reference` markdown with bound digest, and the coverage numbers `covered_pct`, `undocumented`, `unmatched_documented`.

## Failure Handling
- INVALID_INPUT: fix the named field once (an endpoint not written as `METHOD /path` is the usual cause); then BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; coverage is then unproven and verification is `partial`.
- Public-facing API docs are L3: stop with BLOCKED `needs=human-approval` and hand to `a15-docs--public-docs-gate`.

## Handover
Fill `goal`, `completed` (endpoints rendered), `remaining` (uncovered endpoints), `decisions` (divergences fixed in docs), `artifacts` (the reference path with its digest, producer `A15`; the contract digest it is bound to goes in `completed`), `verification` with `confidence` from the coverage result. Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent named in task routing, A01 when unknown.
