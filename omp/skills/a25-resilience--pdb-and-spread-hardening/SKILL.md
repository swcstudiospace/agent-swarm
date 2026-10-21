---
name: "a25-resilience--pdb-and-spread-hardening"
description: "Use when workload manifests lack PodDisruptionBudgets, zone spread or anti-affinity, or a drain stalls; fix budgets and spread against replica and zone counts."
disable-model-invocation: true
---

# PDB and spread hardening

## When to Use

- A scan shows `pdb-missing`, `pdb-blocks-eviction`, `zone-spread-missing` or similar, a node drain hangs on evictions, or a launch review asks whether a zone loss is survivable.
- Not for replicated consensus stores: `skill://a25-resilience--quorum-protection`. Node pool resizing and drain plans belong to A21.
- Manifest edits and staging applies are L2; production applies are L3.

## Procedure

1. Run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <manifest-dir> --json`. Keep `counts`, `workloads_checked`, `pdbs_found` and the findings `pdb-*`, `spread-missing`, `zone-spread-*`, `deprecated-zone-label`. `skipped` with `yaml:skipped:tool-missing` means YAML was not read: report the scan as partial.
2. Call `resil_pdb_check` with `{"workloads": [{name, kind, replicas, ready_replicas, min_available | max_unavailable, unhealthy_pod_eviction_policy}]}`. Use as `replicas` the HPA `minReplicas` when an HPA targets the workload (the script does the same), else `spec.replicas`. Set only one of the two budget fields; both is INVALID_INPUT, as the API refuses it. Omit both for a workload without a PDB.
3. Cross-check `resil_pdb_check` against the script per workload: every id the tool returns must also appear in the script output for that workload with the same severity. Script-only ids `pdb-multiple` (blocker: pods covered by two PDBs are refused by the eviction API) and `pdb-invalid` need a manifest fix, not a tool call.
4. Pick the budget from the `budgets[]` of `resil_pdb_check`: `pdb-unsatisfiable` (blocker) and `pdb-blocks-eviction` (`disruptions_allowed` 0) need a lower `minAvailable` or `maxUnavailable: 1`; `pdb-no-protection` and `pdb-permissive` need a tighter one. Percentages round up, so `minAvailable: 50%` of 3 demands 2. `pdb-stuck-unhealthy` is fixed with `unhealthyPodEvictionPolicy: AlwaysAllow`. One replica gets no `minAvailable: 1` PDB (it blocks every drain); `single-replica` is reported with no budget and also with a budget that still allows the eviction (a `maxUnavailable` percentage rounds up to the one pod): ask for 2 or more replicas.
5. Read the zone count with read-only `kubectl --context <ctx> get nodes -L topology.kubernetes.io/zone` (explicit context). Call `resil_spread_check` with `{workload, replicas, min_available_replicas, topology_domains: [{key: "topology.kubernetes.io/zone", count}], spread_constraints: [{topology_key, max_skew, when_unsatisfiable, min_domains}], anti_affinity: [{topology_key, mode}]}`.
6. Decide from the `resil_spread_check` output: `unschedulable_replicas` above 0 is a blocker (required anti-affinity or hard spread with more replicas than domains); `pods_surviving_zone_loss` below `min_available_replicas` is `zone-loss-breaches-minimum`. Prefer `topologySpreadConstraints` with `maxSkew: 1`, `DoNotSchedule` on `topology.kubernetes.io/zone` and a soft rule on `kubernetes.io/hostname`; the legacy `failure-domain.beta.kubernetes.io/zone` label is deprecated.
7. Apply the fix with `edit` on a branch named `<seat-prefix>/<task_id>`; verify with `kubectl --context <ctx> -n <ns> apply --dry-run=server -f <file>` and `kubectl diff`. Re-run the script: targeted workloads must show no blocker or major, and `counts` must fall. Production apply is L3: BLOCKED needs=human-approval.
8. Use `write` for `resilience-reports/<service>/pdb-spread.md` (before and after counts, per-workload budget and spread values) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `resilience.report` section: PDB and spread findings before and after, `disruptions_allowed`, `pods_surviving_zone_loss`, digest.

## Failure Handling

- INVALID_INPUT: fix the named `field` (both budget fields, `min_available_replicas` above `replicas`, zone key missing from `topology_domains`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Tool and script disagree on a finding: E-CONTRACT, report both. Zone count unreadable: do not assume 3; BLOCKED needs=input.

## Handover

`to_agent` A12. `goal`: gate the release on the hardened budgets. `completed`, `decisions` (chosen budget per workload), `artifacts` (report and patch with digests, producer A25), `open_questions` (unscanned YAML), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a25-resilience/pdb-and-spread-hardening.md; edit the source and run scripts/build_agents.py -->
