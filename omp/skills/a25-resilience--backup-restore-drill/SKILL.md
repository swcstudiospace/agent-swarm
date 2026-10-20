---
name: "a25-resilience--backup-restore-drill"
description: "Use when a resilience.backup task must prove backups work: coverage versus RPO and retention, a restore-time estimate against the RTO, and a scratch drill."
disable-model-invocation: true
---

# Backup coverage and restore drill

## When to Use

- A `resilience.backup` task asks whether volumes, etcd, databases and object stores are protected, or for a restore drill.
- Not for the disaster-recovery procedure itself: `skill://a25-resilience--dr-failover-runbook`. Alerting on failed backup jobs belongs to A13.
- Restoring into a scratch target in staging is L2; a scratch restore in the production cluster is L3; restoring over a live asset is L4.

## Procedure

1. Use `read` and `grep` on the backup configuration the task names (Velero schedules, an etcd snapshot CronJob, database PITR settings, object-store replication) to list each asset with its `kind` (`volume`, `etcd`, `database`, `object-store`), `criticality` and `rpo_hours`, and each job's `interval_hours`, `retention_days`, `last_success`, `offsite`, `last_restore_test`. A missing `rpo_hours` is BLOCKED needs=input.
2. Get the instant with `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) and call `resil_backup_coverage` with `{as_of, min_retention_days, restore_test_max_age_days, assets, jobs}`. Cross-check `assets_total` against the number of assets sent and `coverage_percent` against `assets_covered / assets_total * 100`.
3. Decide from the `resil_backup_coverage` findings: `backup-missing` or `backup-never-succeeded` on a critical asset is a blocker; `backup-stale` is major past one RPO and blocker past two; `schedule-exceeds-rpo`, `retention-too-short` and `no-offsite-copy` are majors to fix; `restore-untested` and `restore-test-stale` pick the drill targets (critical first). `assets_protected` below `assets_covered` means a job exists but is not trustworthy.
4. Call `resil_restore_rto` for each drill target with `{data_size_gib, restore_throughput_mib_s (per stream), parallel_streams, parallel_efficiency, network_limit_mib_s, replay_size_gib, replay_rate_mib_s (required when replay_size_gib is above 0), fixed_overhead_minutes, safety_margin (1 or more), target_rto_minutes}`. `rto-exceeds-target` is a blocker, `rto-margin-eroded` a major; `max_data_gib_for_target` says how much data still fits.
5. Prepare the scratch restore as commands for the operator (for example `velero restore create --from-backup <name> --namespace-mappings <src>:<scratch>`, or `etcdutl snapshot status <file>` then a restore into a new data directory), with explicit context and namespace. Check backup metadata with read-only `bash` only. Never target the live asset: that is L4.
6. After the operator reports the drill, compare the measured restore minutes with `estimated_minutes`. More than 25% apart: re-run `resil_restore_rto` with the measured throughput and keep that as the new basis.
7. Call `resil_drill_cadence` with the drill appended (`scenario` such as `restore:<asset>`, `performed_at`, `outcome`, `evidence_ref`, `measured_rto_minutes`, `measured_rpo_minutes`). `drill-without-evidence` means the drill does not count: obtain the `evidence_ref`.
8. Use `write` to create `resilience-reports/<service>/backup-coverage.md` (asset table, findings, estimate versus measured, next drill `due_at`) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `backup.coverage.report` with per-asset findings, RTO estimate versus measured value and the drill record reference.

## Failure Handling

- INVALID_INPUT (`as_of` without UTC offset, duplicate asset, job for an unknown asset, missing `replay_rate_mib_s`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Backup tool unreachable for read-only checks: report from files only and say so; never claim a restore worked without an `evidence_ref`.
- L3 or L4 restore steps: BLOCKED needs=human-approval; a restore is not retried blindly.

## Handover

`to_agent` A13 (consumes `backup.coverage.report`). `goal`: alert on backup freshness and failed jobs for the listed assets. `completed`, `decisions` (drill targets, estimate basis), `artifacts` (report with digest, producer A25), `open_questions` (assets without a restore test), `verification` (`verified` only when a measured drill exists). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a25-resilience/backup-restore-drill.md; edit the source and run scripts/build_agents.py -->
