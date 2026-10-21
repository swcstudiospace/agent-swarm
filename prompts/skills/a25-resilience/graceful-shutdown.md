---
name: a25-resilience--graceful-shutdown
description: Use when deploys, drains or scale-downs drop in-flight requests or hit SIGKILL; size terminationGracePeriodSeconds, preStop and drain time against measurements.
owner: a25-resilience
tools: [resil_shutdown_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Graceful shutdown

## When to Use

- Rollouts or node drains produce 502/503 bursts, cut-off jobs, or containers that always end in SIGKILL; a scan reports `no-prestop`, `sigkill-before-drain` or `grace-zero`.
- Not for probe values: `skill://a25-resilience--probe-tuning`. Not for drain ordering of nodes: A21 owns drain and cordon plans.
- Manifest edits and staging rollouts are L2; production rollouts are L3.

## Procedure

1. Run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <manifest-dir> --json`. Keep `no-prestop`, `grace-zero`, `sigkill-before-drain`, `thin-margin`, `shutdown-invalid`. The script assumes zero drain time, SIGTERM handled and traffic kept after SIGTERM, so it reports a lower bound; the measured inputs in step 2 are what make the tool stricter.
2. Measure per workload with `read` of `deploy.telemetry` (A13) or a staging test: `app_drain_seconds` (longest in-flight request or job after SIGTERM), `handles_sigterm` (a shell wrapper as PID 1, such as `sh -c`, swallows SIGTERM: use `exec` or an init that forwards), `keeps_serving_after_sigterm`, `endpoint_propagation_seconds` (time until Service endpoints and the load balancer stop sending traffic; take the load balancer's deregistration delay if one exists), and `node_drain_timeout_seconds` from the node-pool or drain tooling. Unmeasured value: BLOCKED needs=input.
3. Call `resil_shutdown_check` with `{workload, serves_traffic, termination_grace_period_seconds, prestop_seconds, app_drain_seconds, handles_sigterm, keeps_serving_after_sigterm, endpoint_propagation_seconds, node_drain_timeout_seconds}`. `prestop_seconds` is the preStop sleep time: the sleep durations in the hook summed and rounded up to whole seconds; 0 without a hook.
4. Decide from the `resil_shutdown_check` output. `required_seconds` is preStop plus drain, run one after the other inside one grace period. `sigkill-before-drain` (blocker): grace is below `required_seconds`. `grace-zero` (blocker): the container is killed at once. `traffic-after-sigterm` (major): a pod that stops accepting on SIGTERM needs a preStop at least `endpoint_propagation_seconds` long, normally a sleep. `sigterm-ignored`: fix the entrypoint, not the number. `grace-exceeds-drain-timeout` (major): the node drain gives up before the pod may end.
5. Set `terminationGracePeriodSeconds` to at least `recommended_grace_seconds` from `resil_shutdown_check`, which adds the larger of 5 s and 10% to the effective need; `margin_seconds` is grace minus `required_seconds` and a value under 5 is `thin-margin`. The preStop sleep uses the `sleep` lifecycle action where the cluster supports it, else `sleep N` in a shell the image must contain (distroless images have none).
6. Cross-check the script against `resil_shutdown_check`: for any workload where the script reports a grace problem, the tool run with `app_drain_seconds` 0 must give the same finding id; when they differ, the script's defaults are the cause, not a tool error. Never raise grace past `node_drain_timeout_seconds`: long jobs need checkpointing or a separate drain strategy, so hand that to the owning team as an open question.
7. Apply with `edit` on a branch named `<seat-prefix>/<task_id>`, verify with `kubectl --context <ctx> -n <ns> apply --dry-run=server -f <file>`, re-run the script and `resil_shutdown_check`: no blocker or major may remain. Production rollouts are L3: BLOCKED needs=human-approval.
8. Use `write` for `resilience-reports/<service>/shutdown.md` (inputs with their source, `required_seconds`, `margin_seconds`, chosen values) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `resilience.report` section: shutdown budget per workload with measurement sources, before and after findings, digest.

## Failure Handling

- INVALID_INPUT: fix the named `field` or BLOCKED needs=input. PARTIAL_SUCCESS: use `completed_fraction`.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Drain time is a guess: say `unmeasured`, never lower the finding. Whether the sleep action exists depends on the cluster version: check with the task and defer to the cluster, not to this skill.

## Handover

`to_agent` A12. `goal`: release the shutdown settings. `decisions` (grace, preStop, measured drain and its source), `artifacts` (report and patch with digests, producer A25), `open_questions` (long jobs, unmeasured propagation), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
