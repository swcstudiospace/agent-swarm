---
name: "a23-mesh-network--circuit-breaker-tuning"
description: "Use when DestinationRules need outlier detection and connection-pool limits tuned so failing endpoints eject without cascading overload."
disable-model-invocation: true
---

# Circuit breaker tuning

## When to Use

- A service shows cascading failure, unbounded queues, one bad pod absorbing traffic, or a new service needs resilience settings in its `DestinationRule`.
- Design is L2. Applying to a production mesh is L3 (`prod_mesh_change`).
- Not for call-chain retry and timeout arithmetic (`skill://a23-mesh-network--retry-timeout-budget`) nor for version splits.

## Procedure

1. Gather the DestinationRules (`read`/`grep` from `gitops.manifests`, or `kubectl --context <ctx> -n <ns> get destinationrule -o json`) and the endpoints behind each service as `workloads` `[{namespace, service, labels}]`. From `cluster.capacity.report` (A21) and the service owner take: replicas, per-pod concurrency and latency target. Missing figures: BLOCKED needs=input; never size limits from guesses.
2. Call `mesh_destination_policy` with `destination_rules` and `workloads`. Keep `rule_count`, `subset_count`, `verdict`, findings. Read them: `DR-NO-OUTLIER-DETECTION` (minor: bad endpoints stay in rotation), `DR-OUTLIER-INERT` (major: `consecutive5xxErrors` 0 and no gateway or local-origin errors set, so it never ejects), `DR-OUTLIER-LOCAL-ORIGIN` (major: set without `splitExternalLocalOriginErrors`), `DR-OUTLIER-EJECT-ALL` (major: `maxEjectionPercent` 100), `DR-OUTLIER-EJECT-NONE` (minor: 0), `DR-OUTLIER-DURATION` (major: interval or ejection time below 1ms or not whole ms), `DR-POOL-NEGATIVE`, `DR-POOL-CONNECT-TIMEOUT` (major), `DR-POOL-NO-KEEPALIVE` (minor: `maxRequestsPerConnection` 1), `DR-POOL-RETRIES-UNBOUNDED` (info), `DR-SUBSET-NO-ENDPOINTS` and `DR-SUBSET-DUPLICATE` (major/blocker), `DR-HOST-DUPLICATE` (minor, two rules for one host: Istio merges unpredictably), `DR-TLS-DISABLE` (minor).
3. Set outlier detection. Istio defaults are `consecutive5xxErrors` 5, `interval` 10s, `baseEjectionTime` 30s, `maxEjectionPercent` 10. Starting values of this skill: `consecutive5xxErrors` 5 (3 for a latency-critical path), `interval` 10s, `baseEjectionTime` 30s, and `maxEjectionPercent` of at most 50, and never so high that fewer than half the endpoints remain (with 2 replicas a 50 percent cap ejects one of them). Add `consecutiveGatewayErrors` below `consecutive5xxErrors` (for example 3 next to 5) when the backend sits behind a proxy that returns 502/503/504: gateway errors count as 5xx, so a value at or above it has no effect (`DR-OUTLIER-GATEWAY-SHADOWED`). Detecting only 5xx hides timeouts: say so.
   Verify these values with `mesh_destination_policy`: `DR-OUTLIER-INERT` must not remain.
4. Set connection-pool limits from capacity, not from the defaults: `tcp.maxConnections` and `http.http2MaxRequests` for the service = replicas times per-pod concurrency (for example 6 pods at 100 concurrent requests: 600) with 20 percent headroom (this skill's default), `http.http1MaxPendingRequests` small (about the per-pod concurrency, so excess requests fail fast with 503 `UO`), `tcp.connectTimeout` 1s to 5s (whole ms), and keep-alive on (`maxRequestsPerConnection` 0 for unlimited). Envoy applies limits per client proxy to the whole destination, not per endpoint: multiply by the number of client proxies for total pressure.
   Compute the figures with `bash` arithmetic from the capacity inputs and keep the formula in the report.
5. Re-run `mesh_destination_policy` on the proposed rule: verdict `pass` and `finding_count` not above the baseline; `subset_count` unchanged. Any `DR-SUBSET-*` regression means labels changed: stop.
6. Interplay with retries: ejection and breaker overflow return 503, which retries may amplify. Call `mesh_retry_amplification` with the chain through this service (`calls`: `{caller, callee, retries}`) and `max_multiplier` 2 (default of this skill). `over_budget` true: lower retries before shipping the breaker, otherwise the breaker trips and the retries re-enter. Cross-check `retries` against the route: `mesh_traffic_split` on the `virtual_services` must stay `pass` with the same subsets.
7. Test the settings in a staging mesh with a load test and one killed pod: ejection within `interval` + one ejection round, error rate at the caller below the SLO (from A13), `upstream_rq_pending_overflow` growing only at overload. Without such a test, the proposal stays a draft.
   Record the test output with `write` next to the report; `mesh_retry_amplification` stays the check for the retry chain.
8. Write before/after parameters and test evidence to `mesh-reports/<cluster>/breaker-<service>.md` with `write`; digest with `shared_artifact_digest`. Production apply is L3: BLOCKED needs=human-approval with the report; rollback is reverting the DestinationRule commit (deleting is L4).

## Outputs

- `mesh.policy.set` (resilience variant): outlier and pool values per service with sizing basis, tool verdicts, load-test evidence.

## Failure Handling

- INVALID_INPUT (rule without `host`, workload without `service`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked rules keep their old values. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool checks structure, not the right numbers: thresholds above are starting points of this skill; defer to load-test evidence and the vendor reference for field defaults, and flag them unverified otherwise.
- Never retry an apply; a breaker that trips in production is rolled back under approval.

## Handover

- `to_agent`: A22 (manifests) and A13 (alerts on ejections and overflow). `goal`: services and values. `completed`, `decisions` (sizing basis), `artifacts` (report, digests), `open_questions` (missing concurrency figures), `verification` (tool verdicts, test result).
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/circuit-breaker-tuning.md; edit the source and run scripts/build_agents.py -->
