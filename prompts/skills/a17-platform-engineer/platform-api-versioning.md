---
name: a17-platform-engineer--platform-api-versioning
description: Use when a breaking platform API or template change needs a new version name, a deprecation timetable and an L3 approval packet before any announcement.
owner: a17-platform-engineer
tools: [plat_composition_diff, plat_api_deprecation, plat_golden_path_coverage, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Platform API versioning

## When to Use

- A change to a platform API (claim schema, template parameters, catalog descriptor contract) is already known to be breaking and needs a version and a retirement plan.
- Measuring the diff of one XRD and validating claims is `skill://a17-platform-engineer--xrd-composition-change`; announcing and tracking the sunset to consumers is `skill://a17-platform-engineer--deprecation-communication`.
- A breaking change is L3 (`platform_api_breaking_change`): you prepare the packet, a human approves, nothing is removed by you (removal is part of that L3 change and a human performs it).

## Procedure

1. `read` the change request and the current API inventory: for each served API its `name`, `version`, `stability` (`alpha`, `beta` or `ga`), `state` (`active` or `deprecated`), `active_consumers`, existing `deprecated_at` and `sunset_at`.
2. Call `plat_composition_diff` with `old_version`, `new_version` and the flattened `old_fields` and `new_fields` (`path, type, required, enum_values, minimum, maximum, min_length, max_length`). Use `verdict`: `compatible` needs only a minor bump and no deprecation; `breaking-new-version` needs a new version name and the plan below; `breaking-same-version` is rejected (version name reused).
3. From the `plat_composition_diff` `verdict`, choose the version name by stability: a stable API moves `v1` to `v2`; an alpha or beta API moves `v1alpha1` to `v1alpha2`, `v1beta1` to `v1beta2`, or promotes `v1beta1` to `v1`. State your choice as a decision; two names for the same schema are never allowed.
4. The notice window `plat_api_deprecation` applies comes from stability: beta 274 days (9 months) and alpha 0 follow the Kubernetes deprecation policy (rule 4a; it also says "or 3 minor releases, whichever is longer", which is not modelled, so say so). GA 365 days is this platform's house policy: Kubernetes sets no time window for GA APIs (they are not removed within a major version), so do not cite Kubernetes for it. Take `deprecated_at` as the announcement date and `sunset_at` at least the window later; a longer `min_window_days` than the default is a stated, justified override.
5. Call `plat_api_deprecation` with `checked_at` (ISO-8601 with offset), `warn_days` (default 30), the old API as `state: deprecated` with the proposed `deprecated_at` and `sunset_at`, and the new API as `active`. `window-too-short`, `deprecation-date-missing`, `sunset-missing` (major) and `sunset-passed-with-consumers` (blocker) block the packet.
6. Call `plat_golden_path_coverage` with `golden_paths` (`id`, `latest_version`, `min_supported_version`) and `services` (`name`, `tier`, `template`, `template_version`) to count affected consumers. Cross-check: `outdated_count` plus `unsupported_count` should be at least the old API's `active_consumers` when the API is a template; a lower number means consumers are counted from different sources: report both.
7. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "platform API breaking change awaiting L3 approval", needs: ["human-approval"]}`; return its `result_patch` as the BLOCKED result. Do not start the announcement before approval.
8. `write` the packet (diff verdict, version name, window, consumers, rollback) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `golden.path.template` or API contract revision with its version name and timetable; the approval packet.

## Failure Handling

- INVALID_INPUT from `plat_api_deprecation` (`sunset_at` not after `deprecated_at`, timestamp without offset, `deprecated_at` after `checked_at`): fix the named field once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; no packet is final.
- PARTIAL_SUCCESS: use `completed_fraction`.
- Windows come from the tool; if your org's policy differs, escalate instead of overriding silently.

## Handover

`to_agent`: `A05` (template and service implementers), or the assigner. Fill `goal` (approve and then migrate), `decisions` (version, window, override), `blockers` (`human-approval`), `artifacts` (packet with digest, producer `A17`), `verification` (`partial`). End with `shared_handover_validate`, then `shared_handover_write`.
