---
name: a21-cluster-ops--version-support-eol
description: Use when a cluster's Kubernetes version may be near or past upstream patch support and you need the supported target, hop count and a deadline to leave it.
owner: a21-cluster-ops
tools: [k8s_fleet_drift, k8s_upgrade_plan, k8s_deprecated_api_scan, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Version support and end of life

## When to Use

- A cluster (or a short list) runs an old minor, an `incident.alert` or a CVE notice names an unpatched version, or a review asks "how long can we stay on 1.NN and how many upgrades is it to supported?".
- Answers for individual clusters on a deadline. The whole-fleet table is `skill://a21-cluster-ops--fleet-drift-report`; the executable plan of a chosen target is `skill://a21-cluster-ops--cluster-upgrade-plan`. Read-only analysis, L2; an upgrade it motivates is L3 on production.

## Procedure

1. Get the facts with `read` from the task: each cluster's name, `environment`, current version and provider type (self-managed or a managed service). Get `latest_version` (newest upstream minor) and its source from the upstream release page named in the task; never from memory. Provider support calendars (standard and extended support dates) must be supplied by the task: you do not know them.
2. Call `k8s_fleet_drift` with `latest_version`, `clusters` and `max_minor_lag` set to 2 (so `supported` reflects the upstream window of the three newest minors: `lag` 0, 1 or 2). Per cluster: `current` or `supported` is inside upstream patch support; `out_of_policy` with `blocker` `unsupported-upstream` (`minor_lag` 3 or more) means no upstream security patches. Keep `minor_lag` and `status`.
3. Upstream releases about three minors a year and patches each for about 14 months (a year plus a two-month upgrade period); the tool encodes the "three most recent minors" rule, not dates. State dates only when the task gives the upstream or provider calendar; otherwise say "date per calendar" and give only lag.
4. For each cluster at `minor_lag` 3 or more, pick the target: the oldest minor still supported upstream, that is `latest_version` minor minus 2, reached with the fewest hops; a cluster at lag 2 sits on the last supported minor and leaves support when the next minor ships, so plan `latest_version` minor minus 1 for it. Call `k8s_upgrade_plan` with `current_version`, that `target_version` and the pools (or a single placeholder pool from the task); read `control_plane_hops` (one per minor, none skipped). A target more than 6 minors ahead is INVALID_INPUT: split it into two stages through an intermediate minor.
5. Cross-check `k8s_upgrade_plan` against `k8s_fleet_drift`: `control_plane_hops` must equal the target minor minus the current minor (patch-only upgrade: 1). For lag the arithmetic is `latest minor - current minor` and must equal `minor_lag` from step 2; a difference is E-CONTRACT, report both. If the target hop passes a removal minor, name the API removals that bite on the way (for example 1.22 for `networking.k8s.io/v1beta1` Ingress, 1.25 for `policy/v1beta1` PodSecurityPolicy).
6. Call `k8s_deprecated_api_scan` with the chosen `target_version`, `horizon_minors` 4 and the `resources` from the task or from `skill://a21-cluster-ops--deprecated-api-remediation`. `blocked` (`removed_count` above 0) adds pre-work to the hop; `attention` adds follow-ups. Each intermediate minor is scanned in turn when several hops are needed: the removal that matters is the one at the hop that crosses it.
7. Rank clusters: production at `unsupported-upstream` first, then production out of policy, then lower environments that must go first to rehearse (a lower environment must not lag its production, see `ahead-of-lower-env`). Write the ranked list with `write` to `cluster-reports/version-support.md` with lag, target, hops, API pre-work and the stated source of `latest_version`; digest it with `shared_artifact_digest`.

## Outputs

- `cluster.upgrade.plan` summary: per cluster support status, target, hop count, API pre-work, ranking, file digest.

## Failure Handling

- INVALID_INPUT: fix the named `field` (`latest_version` not 1.x, duplicate cluster, target too far) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Dates: no support-end date is stated unless the calendar was supplied; if the upstream and provider calendars disagree, E-CONTRACT with both in the report.
- The tool's rule is the three newest upstream minors; some managed services keep older minors on extended support at extra cost: that is a recommendation for A20 and a human, not a purchase.
- Never start an upgrade; production upgrade approval is L3.

## Handover

`to_agent` A12 (A14 and A22 read the same plan). `goal` (schedule the ranked upgrades), `completed`, `decisions` (target per cluster, source of `latest_version`), `artifacts` (list, digest, producer A21), `open_questions` (missing calendars), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
