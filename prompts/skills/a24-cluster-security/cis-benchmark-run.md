---
name: a24-cluster-security--cis-benchmark-run
description: Use when CIS Kubernetes Benchmark results (kube-bench output) must be scored against a pass-rate threshold with Level 1 failures and per-section gaps reported.
owner: a24-cluster-security
tools: [kpol_cis_score, kpol_rbac_risk_scan, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# CIS benchmark run and scoring

## When to Use

- A `kpol.benchmark` task asks for a CIS Kubernetes Benchmark score for a cluster or node pool, or a trend after a hardening change.
- Scoring and findings only. Fixing a failed recommendation is a change by the owner (A21 for nodes and control plane, A22 for workloads); individual workload hardening is `skill://a24-cluster-security--pss-enforce-rollout`.
- Reading an existing result file is L2. Running kube-bench creates a privileged Job (host PID, host mounts): non-prod L2, production L3 (human approval).

## Procedure

1. Take the explicit `--context`, cluster id and benchmark from the task, and the server version and provider from `cluster.fleet.status` (A21). Self-managed clusters use the generic `cis-<version>` benchmark; managed ones (EKS, GKE, AKS) use the provider benchmark, because control-plane checks cannot be run. Which benchmark id matches which Kubernetes version is not fixed here: take it from `kube-bench --help` or the task, never guess.
2. Obtain results: a supplied kube-bench JSON file read with `read`, or a fresh run `kube-bench run --json` per node role through `bash` after approval (`shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]` for production). Never run it on production without the L3 reply.
3. With `bash` or `eval`, map every kube-bench recommendation to `{id, status, scored, level}`: `status` one of `PASS`, `FAIL`, `WARN`, `INFO`; `scored` true for automated checks, false for manual ones; `level` 1 or 2 from the benchmark profile. Skip nothing; manual checks stay in with `scored: false`.
4. Call `kpol_cis_score` with `results` (up to 2000) and `min_score_pct` from the task (default 85 when the task gives none; say it is a default). Read `total`, `scored_count`, `manual_count`, `passed`, `failed`, `warned`, `score_pct` (rounded half up to two places; the gate compares the exact value), `sections`, `verdict`.
5. Self-check the `kpol_cis_score` arithmetic: `total` equals the number of mapped results, `passed + failed + warned` equals `scored_count`, and the section sums equal the top-level counts. A mismatch is E-CONTRACT; report both and state no score.
6. Read the ids: `cis.fail` (major at Level 1, minor at Level 2), `cis.warn` (a scored check that could not be determined, minor), `cis.manual-fail` (info), `cis.score-below-threshold` (major). Any Level 1 scored failure makes the verdict `fail` regardless of `score_pct`.
7. Compare the workload-facing sections (RBAC and Pod Security in the policies section, numbering varies by benchmark version: use the report ids) with `kpol_rbac_risk_scan` run on the live roles and bindings. CIS and the tool measure different things (many CIS policy checks are manual), so a difference is an open question for the owner, not a tool error: report both.
8. Write `cluster-reports/<cluster>/cis.md` with `write`: score, thresholds, failing ids per section, owner per id, plus the previous score if the task has one; digest it with `shared_artifact_digest`.

## Outputs

- `cluster.security.findings` (CIS): score, section table, Level 1 failures, warn items that need manual evidence.
- `cluster.policy.set` only when a remediation manifest is proposed.

## Failure Handling

- INVALID_INPUT (`min_score_pct` outside 0 to 100, `level` not 1 or 2, empty `results`): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; the unscored sections are listed as gaps, not as passes. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Manual and Level 2 checks do not gate. Never claim compliance or certification: the score is evidence for A10.
- A benchmark run is non-idempotent on a cluster: never retry it. L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A10 (gate owner; A21 gets the node-level failures). Fill `goal`, `completed`, `decisions` (benchmark id, threshold), `artifacts` (report and digest), `open_questions` (manual checks needing evidence), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
