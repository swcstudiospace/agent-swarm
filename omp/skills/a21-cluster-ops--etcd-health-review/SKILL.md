---
name: "a21-cluster-ops--etcd-health-review"
description: "Use when an incident.alert or a pre-upgrade check needs an etcd verdict from database size, quota, leader churn, disk latency and quorum; no etcd mutation."
disable-model-invocation: true
---

# etcd health review

## When to Use

- An `incident.alert` from A13 (apiserver slow, "etcdserver: mvcc: database space exceeded", frequent leader elections) or the preflight of an upgrade or certificate rotation needs an etcd assessment.
- Self-managed or kubeadm clusters only. On managed control planes (EKS, GKE, AKS) etcd is not reachable: record `skipped:not-applicable` and use the provider's control-plane health status instead.
- Assessment only. Compaction, defragmentation, member removal and restore are control-plane mutations: L2 non-prod, L3 prod, and a cluster restore from snapshot over live data is L4.

## Procedure

1. Read the facts from metrics already collected by A13 or from read-only commands: `etcdctl endpoint status --cluster -w json` (per member `dbSize`, `dbSizeInUse`, leader) and `etcdctl endpoint health --cluster`, with explicit endpoints and the cluster context; metrics `etcd_mvcc_db_total_size_in_bytes`, `etcd_mvcc_db_total_size_in_use_in_bytes`, `etcd_server_leader_changes_seen_total` (increase over 15m), `etcd_disk_wal_fsync_duration_seconds` and `etcd_disk_backend_commit_duration_seconds` p99 converted to milliseconds. Never paste client certificates or tokens.
2. Call `k8s_etcd_health` with `db_size_bytes` (the LARGEST member), `quota_bytes` (the `--quota-backend-bytes` flag; omit for the 2 GiB default), `db_size_in_use_bytes`, `leader_changes_15m`, `wal_fsync_p99_ms`, `backend_commit_p99_ms` and `members` (`{name, healthy, learner}`; a learner does not count toward quorum).
3. Decide from `verdict`: `critical` (a `blocker`: quota use at 95% or more, where at 100% etcd raises NOSPACE and goes read-only, or lost quorum) is an immediate escalation; `degraded` (a `major`: quota at 80% or more, 4 or more leader changes in 15 minutes, WAL fsync p99 above 500 ms, backend commit p99 above 250 ms, database above 8 GiB, no failure tolerance) needs a plan within the task's deadline; `healthy` may still carry `minor` findings (fsync above the 10 ms or commit above the 25 ms guidance, defragmentation recommended when the live data is above 100 MiB and under half of the file). The 80% band is house policy; 95%, 4 leader changes, 500 ms, 250 ms and the defragmentation rule are the etcd mixin alert values, 10 ms and 25 ms the etcd FAQ guidance.
4. Recompute and cross-check the `k8s_etcd_health` output: `quota_used_pct` must equal `db_size_bytes * 100 / quota_bytes` to 2 places, `fragmentation_pct` must equal `(db_size_bytes - db_size_in_use_bytes) * 100 / db_size_bytes`, and `quorum` must equal `voters // 2 + 1` with `tolerable_failures = healthy_voters - quorum`. Any difference is E-CONTRACT: report both, state no verdict.
5. Choose the remedy from the `k8s_etcd_health` finding ids, as a proposal only: `etcd/quota-high` or `defrag-recommended` means compaction (`etcdctl compact <rev>` or the apiserver `--etcd-compaction-interval`) then `etcdctl defrag` one member at a time, followers before the leader, and an `etcdctl snapshot save` first; `leader-churn`, `wal-fsync-slow` and `backend-commit-slow` point at disk or CPU starvation, not at tuning etcd flags; `no-failure-tolerance` means repair the unhealthy voter BEFORE any restart or upgrade of the others.
6. Gate sibling work: when the review precedes an upgrade or rotation, `tolerable_failures` must be at least 1 and `verdict` not `critical`; otherwise BLOCKED for that plan. Control-plane certificates are in the same blast radius: call `k8s_cert_expiry` with `now` and `certs` for the etcd peer and server certificates when the task lists them; `verdict: critical` there outranks the size findings.
7. Call `shared_open_questions` with `{task_id}` for unresolved items. For `critical`, call `shared_escalate` with `{task_id, reason_code: "policy", summary: "etcd critical: needs L3 operator", needs: ["human-approval"]}`. Write the review with `write` to `cluster-reports/<cluster>/etcd-review.md` and digest it with `shared_artifact_digest`.

## Outputs

- `cluster.fleet.status` entry for the cluster's etcd: verdict, `quota_used_pct`, `fragmentation_pct`, `voters`, `healthy_voters`, `tolerable_failures`, findings, proposed remedy.

## Failure Handling

- INVALID_INPUT (`db_size_in_use_bytes` above `db_size_bytes`, duplicate member names, no voting member): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and no verdict for that cluster.
- The latency thresholds are the etcd hardware guidance and kubernetes-mixin alerts embedded in the tool; trust the tool output over memory, and state that versions of etcd may report metrics differently.
- Never run `etcdctl defrag`, `member remove`, `snapshot restore` or any write; a human does, under L3 (prod) or L4 (restore).

## Handover

`to_agent` A13 when the task is an `incident.alert`, else the assigner. `goal` (act on the etcd verdict), `completed`, `decisions` (verdict, proposed remedy order), `artifacts` (review file, digest, producer A21), `blockers` (`human-approval`), `open_questions`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a21-cluster-ops/etcd-health-review.md; edit the source and run scripts/build_agents.py -->
