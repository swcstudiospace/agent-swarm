---
name: a13-observability--dashboard-coverage
description: Use when tier-1 user journeys are reviewed before a release and each needs an SLO manifest and a fresh dashboard, ending in a dashboard.bundle and gap list.
owner: a13-observability
tools: [obs_dashboard_staleness, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A release review or periodic audit of observability coverage (spec metric: SLO and dashboard coverage of 100 % of tier-1 user journeys).
- Spec section 5: dashboards auto-expire after 30 days unused. The repo has no usage signal, so this skill uses refresh age as the stand-in (convention): `max_age_hours: 720` and it reports expiry; it deletes nothing.
- Defining a missing manifest is `skill://a13-observability--slo-manifest-intake`; this skill only audits and bundles.

## Procedure

1. Use `read` on the task input listing tier-1 journeys and, for each, its service, dashboard reference and last-refresh timestamp (ISO 8601 with timezone). The journey list is an input; never infer it.
2. Run `python3 scripts/obs_slo.py --json --task-id $TASK --correlation-id $CORR` with neither `--define` nor `--evaluate`; it lists the manifests under `.swarm/slo/` (`manifests`). Map each journey to the manifest naming its service.
3. Use `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) once for `checked_at`. For each dashboard call `obs_dashboard_staleness` with `last_updated_at`, that `checked_at` and `max_age_hours: 720`. `stale: true` marks it expired. Use a shorter `max_age_hours` only if the task names one.
4. Using the `obs_dashboard_staleness` results and the manifest list, classify each journey: covered (manifest and fresh dashboard), missing SLO (no manifest), missing dashboard, or stale dashboard. Compute coverage as covered over total.
5. Use `write` to create the `dashboard.bundle` at the path the task names, listing journey, SLO manifest path, dashboard reference, `age_hours` and status.
6. Call `shared_artifact_digest` with `path` of the bundle file you wrote (the path comes from the task outputs; do not invent one).
7. Call `shared_handover_validate`, then `shared_handover_write` to `A15` so the docs reference the bundle and gaps.

## Outputs

- `dashboard.bundle` and a gap list: journeys missing an SLO, missing a dashboard, or stale beyond 720 h.

## Failure Handling

- `obs_dashboard_staleness` INVALID_INPUT on `checked_at` or `last_updated_at` (no timezone, or `checked_at` before `last_updated_at`): correct from the source, otherwise list the dashboard as unverified.
- Journey list absent: BLOCKED needs=input; coverage cannot be stated without it.
- DEPENDENCY_UNAVAILABLE: retry twice, then mark ages `skipped:tool-missing` and do not call any dashboard fresh.

## Handover

- `to_agent`: `A15`. `goal`: document the coverage and link the bundle.
- `artifacts`: `{kind: "dashboard.bundle", uri, digest, producer: "A13"}`. `remaining`: gap closure per journey. `open_questions`: journeys without owners. `verification`: verified when every age was tool-computed.
- Tools: `shared_handover_validate` then `shared_handover_write`.
