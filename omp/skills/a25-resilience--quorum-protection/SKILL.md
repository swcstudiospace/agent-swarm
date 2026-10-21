---
name: "a25-resilience--quorum-protection"
description: "Use when a replicated StatefulSet such as etcd or ZooKeeper must keep quorum through drains and a zone loss; check members per zone and its PDB."
disable-model-invocation: true
---

# Quorum protection

## When to Use

- A consensus or quorum-write store (etcd, ZooKeeper, Consul, a Raft cluster) runs as a StatefulSet and a drain, upgrade or zone outage is planned or feared. Kafka replication with `min.insync.replicas` is not a consensus store: it is a replication rule, so its `consensus` is false.
- Not for stateless Deployments: `skill://a25-resilience--pdb-and-spread-hardening`. Membership changes and etcd health review belong to A21.
- Analysis is L2; changes to production StatefulSets are L3.

## Procedure

1. Find the replicated StatefulSets: run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <manifest-dir> --json` and use `grep` for `kind: StatefulSet`. Read live placement read-only with `kubectl --context <ctx> -n <ns> get pods -o wide` plus the node zone labels; explicit context and namespace always.
2. Call `resil_quorum_safety` with `{workload, replicas, members_per_zone, quorum_size, consensus, unavailable_members, min_available | max_unavailable}`. `members_per_zone` must sum to `replicas`. `quorum_size` is the only override of the majority quorum: give it only when the store's own rule differs from a majority. `consensus` is a boolean, default true (a consensus store); set it to false only for a replication rule such as Kafka's `min.insync.replicas`, or when the store's write rule is not a majority vote, and record why. A non-boolean `consensus` is INVALID_INPUT. `unavailable_members` is the count down now.
3. Read the numbers: `quorum` is `floor(replicas / 2) + 1` unless `quorum_size` overrides it; `fault_tolerance` is `replicas - quorum` (3 members tolerate 1, 5 tolerate 2, 7 tolerate 3). `survives_zone_loss` is true only when `replicas - worst_zone_members` is at least `quorum`; `max_members_per_zone` equals the tolerance and `min_zones_required` is `ceil(replicas / tolerance)` (5 members over zones 2/2/1 survive a zone loss; 3 members over 2 zones as 2/1 do not).
4. Decide from the `resil_quorum_safety` findings. `quorum-below-majority` (blocker): `quorum_size` is below `floor(replicas / 2) + 1` while `consensus` is true (the default); raise it to the majority or remove it. The finding is not raised when `consensus` is false, so record the reason in the report. `quorum-lost` (blocker): stop all drains and experiments, hand to A13 as an incident, no remediation here. `no-failure-headroom` (major): one more loss breaks quorum, so no drain, no chaos. `zone-loss-while-degraded` (major): `unavailable_members` are already down and a zone loss on top would break quorum (`survives_zone_loss` describes the healthy cluster), so no zone-level experiment or drain until the members are back. `pdb-allows-quorum-loss` (blocker): the budget must allow at most `fault_tolerance` simultaneous evictions, normally `maxUnavailable: 1`. `zone-loss-breaks-quorum` and `no-fault-tolerance` (major): add members or zones. `even-member-count` (minor): the extra member costs money and adds no tolerance; consensus stores run 3 or 5 members, rarely more than 7.
5. Cross-check the budget: call `resil_pdb_check` with the same `replicas` and budget fields; `budgets[].disruptions_allowed` must equal `pdb_disruptions_allowed` of the quorum tool when all members are ready. Different numbers are E-CONTRACT.
6. Cross-check placement: call `resil_spread_check` with `min_available_replicas` set to `quorum` and the real zone spread constraints; `pods_surviving_zone_loss` must be at least `quorum` and agree with `survives_zone_loss`. Hard rules that leave `unschedulable_replicas` above 0 are a blocker: a lost zone then leaves members Pending.
7. Never change a consensus store's size by editing `replicas` alone: membership changes follow the store's own procedure (for etcd `etcdctl member add` or `remove`) and are L3 in production with a human, owned by A21. Prepare the patch with `edit` and check it with `kubectl diff` and `--dry-run=server` only; do not apply it.
8. Use `write` for `resilience-reports/<service>/quorum.md` (members per zone, quorum, tolerance, budget, verdict per workload) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `resilience.report` section: quorum verdict per StatefulSet, PDB and spread evidence, required changes, digest.

## Failure Handling

- INVALID_INPUT (`members_per_zone` sum, `quorum_size` above `replicas`, `unavailable_members` above `replicas`): fix the named `field` or BLOCKED needs=input. Quorum and budget messages name the field, never its value.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool computes majority arithmetic only; it knows nothing about learners, witnesses, rack awareness or leader election. Defer such rules to the store's documentation and say so.
- Any L3 change: BLOCKED needs=human-approval; do not retry membership changes.

## Handover

`to_agent` A12. `goal`: block drains and releases that break quorum. `decisions` (tolerance, required zones), `artifacts` (report with digest, producer A25), `open_questions` (membership changes sent to A21), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a25-resilience/quorum-protection.md; edit the source and run scripts/build_agents.py -->
