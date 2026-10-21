---
name: a15-docs--runbook-reverify
description: Use when A13 reports a failed or unused runbook step, or a tier-1 last_verified has aged out, and existing runbooks must be rewritten in one iteration.
owner: a15-docs
tools: [shared_handover_bundle, docs_stale_doc_age, docs_runbook_verified_ratio, docs_runbook_schema_check, shared_artifact_check, shared_artifact_digest, shared_escalate, shared_handover_validate, shared_handover_write]
---

## When to Use
- A13 reports failed or unused steps, or the verified-date ratio fell under 95 percent (agents/A15-docs.md section 5: runbook drift, rewrite within one iteration).
- Not for first-time runbooks: use `a15-docs--runbook-author`. This skill states only what differs: edit named steps, never raise a date without a drill record.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; list the affected `runbook_id`s and A13's evidence (which step failed or never ran) from `goal`, `handover.document` and `upstream`.
2. Call `docs_stale_doc_age` with `as_of` (today, `YYYY-MM-DD`), `max_age_days` and `docs` (`{path, last_verified}` for the runbook files) to see which have aged; `invalid` entries have an unusable date.
3. Call `docs_runbook_verified_ratio` with `as_of`, `max_age_days`, `runbooks` (`{runbook_id, tier, last_verified}`) and `target_pct` 95; record `tier1_verified_pct` and `unverified` as the before value.
4. `read` each affected runbook and `edit` only the steps A13 named. Keep untouched steps, ids and the whitelist unless the evidence says a whitelisted step failed; then remove it from `whitelisted_for_automation`.
5. Call `docs_runbook_schema_check` on the rewritten runbooks (`runbooks`, `known_actions`); `invalid` and `non_reversible_whitelisted` must be empty except a `last_verified` entry you are about to leave unverified.
6. A new `last_verified` is set only when A13 supplies a drill record: call `shared_artifact_check` with `task_id`, `outputs` (the drill-record artifact, `producer` included) and `agent` `A13`; take the date from the record and set it only when `results[].status` is `ok`.
7. If no drill evidence exists, keep the old date and call `shared_escalate` with `task_id`, `reason_code` `blocked`, `summary`, `needs` (A13 drill record for each runbook).
8. Re-run `docs_runbook_verified_ratio` and report before and after; call `shared_artifact_digest` with `path` of each rewritten runbook.

## Outputs
- Updated runbooks, the unverified list, and before/after `tier1_verified_pct`.

## Failure Handling
- INVALID_INPUT from the ratio tool (no tier-1 row, duplicate id): fix the rows once; else BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing` and do not publish a percentage.
- Never raise a date to pass the 95 percent target.

## Handover
Fill `goal`, `completed` (steps rewritten), `remaining` (runbooks still unverified), `blockers` (missing drill records), `artifacts` (runbook paths each with its `shared_artifact_digest` digest, producer `A15`), `verification` (`partial` while any date is unverified). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to `A13`.
