---
name: a21-cluster-ops--deprecated-api-remediation
description: Use when manifests in a repo must be cleared of APIs removed by a target Kubernetes version and of missing requests; scan, cross-check and list migrations.
owner: a21-cluster-ops
tools: [k8s_deprecated_api_scan, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Deprecated API remediation

## When to Use

- A `cluster.upgrade` or `iac.change` names a repository path holding Kubernetes manifests (YAML or JSON) and a `target_version`, and you must say which objects stop working at that version.
- Read-only: you list the migrations, A22 edits and deploys the manifests (it owns what runs on clusters). Whole-upgrade ordering is `skill://a21-cluster-ops--cluster-upgrade-plan`; a live cluster's objects need that skill's `apiserver_requested_deprecated_apis` check, not this one.

## Procedure

1. Run `python3 scripts/k8s_manifest_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --target-version <1.NN> --json`. `--path` is relative to `--root`; omit `--target-version` only to see every API of the table as removed. Exit 0 is `status: ok`, exit 1 is `status: fail` (a `major` or `blocker` finding exists, or an in-scope input was not analysed; not a tool error), exit 2 is an input error (path missing or outside the root, target not 1.x).
2. Read the coverage fields first: `files_scanned`, `manifests_scanned`, `files_skipped` (symlink, too_large, binary, unparsable, unreadable), `truncated` (stopped at 2000 files: narrow `--path` and rerun per directory) and `skipped` (an entry `skipped:tool-missing` for PyYAML means YAML files were not read; every skipped input makes `status` fail with a major `scan-incomplete` finding: say the scan is incomplete, never report "clean").
3. Split the `scripts/k8s_manifest_check.py` `findings` by `rule`. `removed-api` `blocker` = not served at `target_version`; `removed-api` `minor` = removed in the next minor. `missing-request` is `major`, `missing-limit` is `minor` (both cpu and memory, init containers included). Every finding carries `location` (file or `file#doc`); use it, do not guess lines.
4. Cross-check the removals with the tool: call `k8s_deprecated_api_scan` with `target_version` and `resources` built from the `removed-api` findings (`api_version`, `kind`, `name` read from their `id` `removed-api/<apiVersion>/<kind>/<name>`: the apiVersion itself contains a slash, so the last two segments are `kind` and `name`) and `horizon_minors: 1`. `removed_count` must equal the script's count of `blocker` `removed-api` findings and `upcoming_count` the `minor` ones; a difference is E-CONTRACT (the two share one table): report both counts.
5. For each removed API take the replacement from the `k8s_deprecated_api_scan` finding `summary` ("migrate to ..."), never from memory. Table examples to sanity-check, not to substitute: `batch/v1beta1` CronJob and `policy/v1beta1` PDB go away at 1.25, `autoscaling/v2beta2` HPA at 1.26, `policy/v1beta1` PodSecurityPolicy at 1.25 with no API replacement (Pod Security Admission or a policy engine, owned by A24).
6. Missing requests matter for upgrades because drains reschedule pods: `missing-request` on a workload means the scheduler and `k8s_nodepool_capacity` cannot place it reliably. Group them per workload; recommend values only from observed usage supplied by the task, otherwise state "request needed" with no number.
7. Write `cluster-reports/<cluster>/api-remediation.md` with `write`: per location the old `api_version`/`kind`, the replacement, severity, and the order (blockers before the control-plane hop, minors before the next upgrade). Run `shared_artifact_digest` on it.
8. Decide from `scripts/k8s_manifest_check.py` `status` and `findings`: any `blocker` blocks the upgrade (state it in the `cluster.upgrade.plan` as pre-work); a `scan-incomplete` finding keeps the verdict unready until the named inputs are readable and the scan is rerun; only minors means `attention`. Applying a manifest fix to a production cluster is an A22 L3 action; you never run `kubectl apply` or `kubectl convert` against a cluster.

## Outputs

- `cluster.upgrade.plan` fragment: blocker and minor lists with locations, replacements, coverage fields and the file digest.

## Failure Handling

- Script exit 2 / E-INPUT: fix `--path` or `--target-version` (for example `1.33`) or BLOCKED needs=input.
- INVALID_INPUT from `k8s_deprecated_api_scan`: fix the named `field` (a resource name over 63 characters is the usual cause) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`, name the gap; DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing`.
- The table is curated: CRDs, Helm-owned objects and operators' own APIs are not covered. Say so; do not claim the repo is free of removals beyond the table.
- A third-party chart with a removed API is not edited here: name the chart and version, owner A22.

## Handover

`to_agent` A22. `goal` (migrate the listed manifests before the upgrade), `completed` (counts, coverage), `decisions` (blockers vs minors), `artifacts` (report file, digest, producer A21), `open_questions` (skipped YAML, truncated scan), `verification` (`partial` when coverage is incomplete). Call `shared_handover_validate`, then `shared_handover_write`.
