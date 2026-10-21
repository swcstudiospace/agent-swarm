---
name: a15-docs--staleness-sweep
description: Use when an upstream artifact changed or a scheduled sweep runs and bound docs must be checked against current digests, marked stale, and update tasks raised.
owner: a15-docs
tools: [shared_artifact_digest, docs_digest_staleness, docs_stale_doc_age, shared_handover_validate, shared_handover_write]
---

## When to Use
- After any contract, ADR, release record or SLO manifest changes, or on the scheduled docs sweep (agents/A15-docs.md section 4.2, the staleness law).
- Detects and flags only. Rewriting is the job of `a15-docs--api-reference` and `a15-docs--runbook-reverify`; creating a placeholder for an absent artifact is `a15-docs--missing-artifact-stub`.

## Procedure
1. Use `grep` with pattern `^(bound_artifact|bound_digest|last_verified):` over the docs tree to collect each doc's binding (the convention `a15-docs--api-reference` writes into front matter).
2. For each distinct `bound_artifact` that is a repo file, call `shared_artifact_digest` with `path`; keep `{artifact, digest}`. A non-file artifact cannot be digested: list it as unchecked in the handover. It is left out of `current`, so step 3 reports its docs as stale (conservative, never fresh).
3. Call `docs_digest_staleness` with `docs` (`{path, bound_artifact, bound_digest}`) and `current`. Record `stale` (entries with `path` and `reason`), `unbound`, `fresh_count` and `stale_scopes`.
4. Call `docs_stale_doc_age` with `as_of` (today, from `bash` `date +%F`), `max_age_days` 7 and `docs` (`{path, last_verified}`) for the date view; the target is under 5 percent stale (section 6 metrics). Report it separately: a doc can be fresh by digest and old by date. Docs in its `invalid` list have no usable date.
5. `edit` each stale doc's front matter to add `stale: true` and the reason; do not change its body.
6. From the `docs_digest_staleness` result build the update tasks: one `remaining` entry per stale doc naming the artifact and the skill that fixes it, and one `blockers` entry per scope in `stale_scopes`: "release notes for <scope> blocked until docs refreshed" (stale docs block release-notes publication for their scope).
7. Take `unbound` from the `docs_digest_staleness` result: those docs go to `open_questions` so an owner binds them; do not guess a binding.

## Outputs
- `stale.docs.alert`: `stale` list with reasons, `stale_scopes`, `unbound`, `fresh_count`, age-based `stale_pct`.

## Failure Handling
- A bound artifact is absent from the current set: the tool marks the doc stale; hand it to `a15-docs--missing-artifact-stub` if no artifact exists at all.
- INVALID_INPUT (duplicate path, a current artifact listed with two digests, malformed digest): fix the named field once; else BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; do not clear any stale flag.

## Handover
Fill `goal` (refresh stale docs), `completed` (docs checked), `remaining` (update tasks), `blockers` (release-notes scopes), `open_questions` (unbound docs), `verification` (`partial` if any artifact was unchecked). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to `A01`, who owns task creation.
