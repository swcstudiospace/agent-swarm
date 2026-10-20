---
name: a22-gitops--canary-rollout-design
description: Use when a gitops.rollout task needs an Argo Rollouts canary or blue-green strategy with analysis; validate steps, blast radius and the abort rule.
owner: a22-gitops
tools: [gitops_rollout_check, gitops_canary_verdict, gitops_kustomize_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Canary rollout design

## When to Use

- A `gitops.rollout` task asks how one workload reaches production gradually: strategy, steps, pauses, analysis and abort conditions.
- Not for deciding whether it ships (A12) and not for SLO targets (A13 owns them; take `error_ratio` and latency limits from its `slo.manifest`). Serverless workloads use Knative revisions (A19 `serverless.config`): record traffic split there and stop. Mesh routing objects (A23 `mesh.policy.set`) are inputs, never rewritten here.
- Production rollout apply is L3, non-prod L2. This skill produces the plan only.

## Procedure

1. Read the workload `Rollout` (or `Deployment` to convert) with `read`, plus replicas per environment and any A23 `mesh.policy.set` that names its Services. Choose canary when the workload is stateless with a metric to judge; blueGreen when two versions must not serve together or a preview check exists.
2. Draft the strategy with `edit` in the Rollout. Canary without a mesh or ingress `trafficRouting` exposes whole pods only, and `maxSurge` (default 25%) takes part in the choice (Argo Rollouts `approximateWeightedCanaryStableReplicaCounts`): with 4 replicas a 10% step becomes 20%; with 1 replica and the default surge a 10% step exposes 50%. With routing, set `stableService` and `canaryService`. Start with steps such as `setWeight: 5`, `pause: {duration: 5m}`, an `analysis` step, 25, 50, 100; keep `maxSurge` and `maxUnavailable` explicit (not both 0).
3. Call `gitops_rollout_check` with `rollout`, `analysis_templates` (the names that really exist in the repo, from `grep`) and `max_first_step_weight` (default 25). Read `strategy`, `effective_weights`, `manual_gate_count`, `total_pause_seconds`, `analysis_count`. Rules: `no-analysis`, `analysis-template-missing`, `weight-decrease` and `surge-and-unavailable-zero` block; `large-first-step` and `weight-granularity` (10 points or more between asked and effective weight) are fixed or justified in writing.
4. Define the analysis from A13 limits, to feed `gitops_canary_verdict`. For every metric choose `direction`, `aggregation` (p99 for latency), an absolute `threshold` and, when a baseline exists, `max_regression_pct`; set `failure_limit` (0 for error ratio) and `min_samples` equal to the AnalysisTemplate `count`. These are proposals to A13 until it confirms them.
5. Dry-run the verdict with sample data from the task or a previous rollout: call `gitops_canary_verdict` with `metrics` (`name`, `direction`, `aggregation`, `threshold`, `max_regression_pct`, `failure_limit`, `canary_samples`, `baseline_samples`) and `min_samples`. `action` must be `abort` for a clearly bad series and `promote` for a clean one; if your own limits give `Inconclusive` (`pause`) for a clean series, `min_samples` is unreachable: lower it or lengthen the pause.
6. Cross-check the two tools: `analysis_count` from step 3 must be at least 1 for every metric group you defined, and the total soak (`total_pause_seconds` plus analysis `count` x `interval`) must not exceed the maintenance window of the task.
7. If a Kustomize overlay patches the Rollout, call `gitops_kustomize_check` with its `kustomization` and `resources`; patches that match nothing silently leave the safe defaults out.
8. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <app-dir> --json`. Write the plan (steps, `effective_weights`, abort rule, manual gates) with `write`; call `shared_artifact_digest` on the Rollout and the plan.

## Outputs

- `rollout.plan`: strategy, step table, analysis metrics with limits, `verdict` of `gitops_rollout_check`, dry-run `action`.

## Failure Handling

- INVALID_INPUT: fix the named field (`metrics` without a threshold or baseline, a `rollout` not `argoproj.io/v1alpha1`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; name the metric not evaluated.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; the plan stays `draft`.
- The 25% first-step limit and the 10-point granularity are policy of the tool, not Argo Rollouts limits; a human may accept a deviation in writing. Defer metric limits to A13.
- Never run `kubectl argo rollouts promote` on production: L3, BLOCKED needs=human-approval.

## Handover

`to_agent` A12 (decides the rollout; A13 reads it to wire analysis). Fill `goal`, `completed`, `decisions` (strategy, steps, limits), `artifacts` (Rollout and plan with digests, producer A22), `open_questions` (limits A13 must confirm), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
