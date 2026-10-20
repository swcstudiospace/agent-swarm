---
name: "a21-cluster-ops--fleet-drift-report"
description: "Use when a fleet of clusters needs a status report on version lag, environment ordering, skew and certificate health; a cluster.fleet.status for A13 and A24."
disable-model-invocation: true
---

# Fleet drift report

## When to Use

- A periodic or on-demand request wants one `cluster.fleet.status` over several clusters (dev, test, staging, prod): who lags, who leads a lower environment, who is already out of skew or about to lose certificates.
- Status only: L2, read-only, no cluster is changed. Planning the upgrade of one lagging cluster is `skill://a21-cluster-ops--cluster-upgrade-plan`; which versions are still supported and the deadline is `skill://a21-cluster-ops--version-support-eol`; policy drift inside clusters is A24.

## Procedure

1. Get the inventory from the task or the cloud architecture (`cloud.architecture` from A16): cluster name, `environment` (`dev`, `test`, `staging`, `prod`) and version, plus per cluster the apiserver and kubelet versions via `kubectl --context <ctx> get nodes -o json` (one explicit context per cluster; read-only). A cluster you cannot reach is listed as `skipped:unreachable`, never assumed current.
2. Find `latest_version` as the newest minor the upstream release page lists, or the value the task gives; write its source in the report. Never take it from memory.
3. Call `k8s_fleet_drift` with `latest_version`, `clusters` (`{name, environment, version}`, names unique) and `max_minor_lag` (the task's policy; the default is 1, meaning N-1). Read `clusters[].status`: `current`; `supported` (lag within policy and under 3); `out_of_policy` (lag above the policy); `ahead` (newer than `latest_version`: refresh the input). `blocker` `unsupported-upstream` means lag of 3 or more minors: upstream patches only the three newest minors. `ahead-of-lower-env` (minor) means a higher environment leads a lower one: the upgrade was not rehearsed first.
4. Take the fleet verdict from the `k8s_fleet_drift` `verdict`: `noncompliant` (any `major` or `blocker`), `drift` (only minors) or `compliant`. Use `distribution` for the one-line spread (for example 1.31: 2, 1.32: 1).
5. Per cluster call `k8s_version_skew` with its `apiserver_versions` and `components` (kubelets with `node`, kube-proxy, kubectl). Cross-check the minor in `k8s_fleet_drift` (the cluster `version`) against `apiserver_newest` here: they must agree; a mixed-version control plane (`apiserver/spread`) means the cluster is mid-upgrade or broken. Report `max_kubelet_lag` and any `blocker` per cluster.
6. Call `k8s_cert_expiry` with `now` and the control-plane `certs` for each reachable self-managed cluster. Carry only `verdict`, `nearest_expiry` and the `expired` and `critical` counts into the fleet table; the rotation plan belongs to `skill://a21-cluster-ops--cert-rotation-plan`.
7. Assemble the table with `write` at `cluster-reports/fleet-status.md`: cluster, environment, minor, lag, status, skew verdict, certificate verdict, finding ids, then the ranked next actions (blockers first, then out-of-policy production, then ahead-of-lower). Digest it with `shared_artifact_digest`.

## Outputs

- `cluster.fleet.status`: the per-cluster table, fleet `verdict`, `distribution`, `max_minor_lag`, source of `latest_version`, unreachable clusters, file digest.

## Failure Handling

- INVALID_INPUT from `k8s_fleet_drift` (duplicate cluster name, version not `1.x`, `max_minor_lag` above 10): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, list which clusters were evaluated; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- E-CONTRACT when `k8s_fleet_drift` and `k8s_version_skew` disagree on a cluster's minor: report both, check the inventory source, and state no status for that cluster.
- Managed Kubernetes channels (rapid, regular, stable) and provider-specific end-of-life dates are not in these tools: the report says "per provider calendar" until the task supplies it.
- Never upgrade, patch or reconfigure a cluster from this report.

## Handover

`to_agent` A13 (A24 reads the same `cluster.fleet.status`). `goal` (track and alert on the listed drift), `completed`, `decisions` (policy `max_minor_lag`, `latest_version` and its source), `artifacts` (table, digest, producer A21), `open_questions` (unreachable clusters), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a21-cluster-ops/fleet-drift-report.md; edit the source and run scripts/build_agents.py -->
