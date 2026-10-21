---
name: a15-docs--runbook-author
description: Use when A13 sends a runbook.request for a new failure mode and a runbook must be written in the executable schema, whitelisting only reversible steps.
owner: a15-docs
tools: [shared_handover_bundle, docs_runbook_schema_check, docs_runbook_verified_ratio, docs_anchor_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- A13 files a `runbook.requests` entry (capability `docs.runbook`) for a failure mode that has no runbook (03-agents/A15-docs.md section 3).
- Not for runbooks whose steps A13 reports failed, unused or aged: use `a15-docs--runbook-reverify`.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; take the failure mode, trigger (for example an SLO burn expression), tier (1 to 3) and the actions A13's executor knows from `goal`, `handover.document` and `upstream`. Missing trigger or tier is BLOCKED `needs=input`.
2. Use `write` to draft the fragment at the registry path the task `outputs[]` names (single writer: you are the only agent that writes it): `runbook_id`, `title`, `trigger`, `preconditions`, `steps` (`action`, `target`, `reversible`), `whitelisted_for_automation`, `last_verified` (the RB-31 example in 03-agents/A15-docs.md section 3). Whitelist only steps that are `reversible: true` and on A13's whitelist (flag-off, scale-out, cache-flush, per 03-agents/A13-observability.md); anything else stays human-run.
3. Call `docs_runbook_schema_check` with `runbooks` (the fragment) and `known_actions`. Fix until `invalid` is empty and `non_reversible_whitelisted` is empty. A single `last_verified is missing` entry is expected: leave it blank, because the date comes only from a drill record, never from you.
4. Call `docs_runbook_verified_ratio` with `as_of` (today, `YYYY-MM-DD`), `max_age_days`, `runbooks` (every registry row plus the new one with its `tier`, a blank `last_verified` for the new one) and `target_pct` 95, to show the new runbook in `unverified` and the registry percentage against 95. The tool needs at least one tier-1 row and counts only tier 1.
5. If the runbook has a readable companion page, call `docs_anchor_check` with `headings` (the page's heading texts, in order) and `references` (the alert-side `#fragment` links) so cross-links still resolve.
6. Once step 3 passes, call `shared_artifact_digest` with the `path` of the registry file.

## Outputs
- Runbook fragment in the executable schema, the schema verdict (`valid`, `invalid`, `non_reversible_whitelisted`) and the tier-1 verified percentage after the change.

## Failure Handling
- An action A13 needs is not in `known_actions`: do not invent it; BLOCKED `needs=input` naming the action.
- INVALID_INPUT: fix the named field once; then BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then report the schema check as `skipped:tool-missing` and mark the runbook not ready for A13.

## Handover
Fill `goal` (A13 can execute RB-nn), `completed`, `remaining` (drill to set `last_verified`), `decisions` (what was whitelisted and why reversible), `artifacts` (runbook path, digest, producer `A15`), `verification` (`partial` until A13 verifies). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to `A13`.
