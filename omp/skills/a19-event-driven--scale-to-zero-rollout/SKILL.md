---
name: "a19-event-driven--scale-to-zero-rollout"
description: "Use when an event consumer on Kubernetes moves to KEDA or HPA scaling with min 0 replicas; verify bounds, reaction time and drain safety and stage the rollout."
disable-model-invocation: true
---

# Scale-to-zero rollout

## When to Use

- A consumer deployment gets (or changes) an event-driven scaler: KEDA `ScaledObject` or HPA with an external metric, often with `minReplicaCount: 0`.
- Output is a `serverless.config` with scaler settings: L2 in dev and staging. Production rollout is L3: you prepare, the pipeline owners apply (A11, A22). Delete of a scaled object or workload is L4.
- Not for cold start of managed functions (`skill://a19-event-driven--cold-start-tuning`) nor for an ongoing lag incident (`skill://a19-event-driven--backpressure-handling`).

## Procedure

1. Read the manifest (`ScaledObject`, Deployment) with `read`. Note `minReplicaCount`, `maxReplicaCount`, `pollingInterval`, `cooldownPeriod`, the trigger threshold (for Kafka `lagThreshold`), `terminationGracePeriodSeconds`, the longest message processing time and the partition count of the source. Do not rely on defaults you did not read (KEDA documents pollingInterval 30 s and cooldownPeriod 300 s; confirm in the manifest and docs for the installed version). Name the cluster `context` and `namespace` you analyse; none given is BLOCKED `needs=input`.
2. Call `event_scaler_sanity` with `min_replicas`, `max_replicas`, `polling_interval_s`, `cooldown_period_s`, `replica_startup_s` (measured image pull plus start plus first poll), `max_reaction_s` (the latency SLO of the first event), `target_backlog_per_replica`, `peak_backlog`, `termination_grace_period_s`, `max_processing_s`, `partitions`.
3. Read `needed_replicas`, `effective_max_replicas` and `reaction_time_s` (polling plus startup). Major findings: `needed_replicas` above `effective_max_replicas` (the lower of max replicas and partitions: replicas beyond partitions sit idle on Kafka-like sources, so raise partitions via `skill://a19-event-driven--topic-design` instead of replicas), reaction above `max_reaction_s`, grace period shorter than `max_processing_s` (scale-down kills in-flight work: set grace above it and handle SIGTERM by finishing then exiting). Blocker: `min_replicas` above `max_replicas`.
4. With `min_replicas` 0, `event_scaler_sanity` gives an info finding: the first event waits up to `reaction_time_s`. Accept it only if that fits the SLO; otherwise set `min_replicas` 1 and say what it costs in idle capacity. KEDA's `cooldownPeriod` only delays the scale to zero, so the tool judges it only here: a `cooldown_period_s` below the polling interval is a minor finding.
5. Cross-check capacity with `event_lag_assessment`: `lag_msgs` = `peak_backlog`, `arrival_msgs_per_s`, `consumer_count` = `effective_max_replicas`, `per_consumer_msgs_per_s` measured, `slo_s`, `partition_count`. `slo_breach` true or `trend` `growing` means the scaler ceiling cannot hold the load: fix it, `consumers_needed` must be at or below `effective_max_replicas`; disagreement with step 3 is E-CONTRACT.
6. Validate syntax without touching the cluster: `kubectl diff --context <context> --namespace <namespace> -f <manifest>` (read-only) or a client dry run, through `bash`; never `apply`, never `delete`. Run the repo checks named by `shared_toolchain` with `{"scan": true}` and `shared_check_plan` with `{"scan": true}` when the manifests are templated.
7. Write the staged plan: dev, staging (replay a load profile, watch `reaction_time_s` and lag), production at L3 with a rollback (previous manifest revision, `minReplicaCount` back to its old value). Call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod scaler change awaiting L3 approval", "needs": ["human-approval"]}` and return its `result_patch` for the production stage.
8. Call `shared_artifact_digest` with `{path}` for the manifest and plan, then see Handover.

## Outputs

- `serverless.config` (scaler manifest, staged rollout plan, rollback) with the tool fields cited.

## Failure Handling

- INVALID_INPUT: fix the named field or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the gap.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; `kubectl` missing: BLOCKED `needs=tool`, never describe a diff you did not produce.
- Scaler semantics differ by version and trigger: when the tool output and the docs disagree, report both and state no verdict.
- Never retry an escalation; production and delete actions are BLOCKED `needs=human-approval`.

## Handover

`to_agent`: `A22` (GitOps owner of the manifest; `A11` also consumes `serverless.config`, name it in `remaining`). Fill `goal`, `completed`, `decisions` (min, max, cooldown, reaction), `artifacts` (digests), `blockers` (`human-approval`), `constraints` (context, namespace), `verification` (`partial`). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a19-event-driven/scale-to-zero-rollout.md; edit the source and run scripts/build_agents.py -->
