---
name: a17-platform-engineer--deprecation-communication
description: Use when an approved deprecation of a platform API or template version needs consumers notified and the sunset tracked until removal is ready.
owner: a17-platform-engineer
tools: [plat_api_deprecation, plat_golden_path_coverage, plat_catalog_graph, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Deprecation communication

## When to Use

- A deprecation was approved (see `skill://a17-platform-engineer--platform-api-versioning`) and now needs the notice, the consumer list and the reminders, or a scheduled run checks the timetable.
- Choosing the new version and window is the versioning skill; this one runs the announced timetable. Removing an API, template or entity is part of the L3 `platform_api_breaking_change`: you report `removable` and a human approves and removes.
- Marking an entity `lifecycle: deprecated` and drafting the notice is L2.

## Procedure

1. `read` the deprecation record: `name`, `version`, `stability` (`alpha`, `beta`, `ga`), `state`, `deprecated_at`, `sunset_at`, `active_consumers`, and the replacement version. Dates are ISO-8601 with an offset.
2. Call `plat_api_deprecation` with `checked_at` (now, from `bash` `date -u +%Y-%m-%dT%H:%M:%S+00:00`), `warn_days` 30 (or the task's value) and `apis`. Act on the finding ids: `sunset-imminent` (major: sunset within `warn_days` with consumers), `consumers-pending-migration` (minor), `sunset-passed-with-consumers` (blocker), `window-too-short` (major, planning error), `removable` (info), `sunset-on-active-api` (minor: set the state to deprecated).
3. Cross-check the `plat_api_deprecation` counts: `deprecated_count` is the number of `state: deprecated` rows; `past_sunset_count` is `removable_count` plus the number of `sunset-passed-with-consumers` findings; `at_risk_count` counts distinct majors and blockers. A mismatch is E-INTERNAL.
4. For a template-based API call `plat_golden_path_coverage` with `golden_paths` (`latest_version`, `min_supported_version` of the replacement) and `services`. `unsupported` services (version below the floor) and `outdated` ones are the consumers to notify; `version-unknown` (major) services have no provable version and must be asked.
5. Call `plat_catalog_graph` with `entries` `[{ref, lifecycle, owner, depends_on}]`: `most_depended_on` shows which consumers have dependents of their own and so need the notice first; a consumer with no owner (`unowned`) goes to `open_questions`.
6. `write` the notice (what changes, who is affected, the replacement version, `deprecated_at`, `sunset_at`, days left from `checked_at`, migration steps, contact) and the consumer table with owner and status. `edit` the descriptors of deprecated entities to `spec.lifecycle: deprecated`.
7. Escalate: `sunset-passed-with-consumers` or `sunset-imminent` for a `tier-0` or `tier-1` consumer: call `shared_escalate` with `reason_code` `blocked`, the consumer refs in `summary` and `needs` `["human-approval"]` (extend the sunset or force the migration, a human decides). `removable` APIs are announced as ready for human-approved removal, never removed.
8. Call `shared_artifact_digest` with `{path}` for the notice and the table; hand over.

## Outputs

- `service.catalog.entry` updates (lifecycle `deprecated`), the notice, the consumer table and the dated status per API.

## Failure Handling

- INVALID_INPUT (blank `name` or `version`, `deprecated_at` after `checked_at`, `sunset_at` not after `deprecated_at`, timestamp without offset): fix the named field once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no status is declared on a skipped check.
- PARTIAL_SUCCESS: use `completed_fraction`, name unchecked APIs.
- Notice windows by stability: beta 274 days and alpha 0 follow the Kubernetes deprecation policy; the GA 365 days is house policy (Kubernetes sets none). Defer to the approved record.

## Handover

`to_agent`: `A15` (publishes the notice and changelog). Fill `goal`, `completed`, `remaining` (reminders due, with dates), `decisions`, `blockers`, `artifacts` (notice and table with digests, producer `A17`), `open_questions`, `verification`. End with `shared_handover_validate`, then `shared_handover_write`.
