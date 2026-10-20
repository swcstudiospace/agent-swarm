---
name: "a23-mesh-network--retry-timeout-budget"
description: "Use when a service call chain needs retry and timeout settings that cannot amplify load or invert deadlines; compute the multiplier and timeout gaps per hop."
disable-model-invocation: true
---

# Retry and timeout budget

## When to Use

- A `mesh.policy` task, an incident review or a new `architecture.blueprint` chain (edge, service A, B, datastore) needs retries and timeouts set or audited.
- Design is L2. Rolling the resulting VirtualService changes to production is L3.
- Not for ejecting bad endpoints (`skill://a23-mesh-network--circuit-breaker-tuning`) nor for version shifts.

## Procedure

1. Draw the chain from the `architecture.blueprint` and the `api.contract` (idempotency of each call decides whether it may be retried at all: only idempotent methods, or `retryOn` limited to `connect-failure,refused-stream`). List hops in order as `{caller, callee, retries, timeout_ms, per_try_timeout_ms}`; `calls[i+1].caller` must equal `calls[i].callee`. Read the current values from the VirtualService manifests (`read`/`grep`); Istio's default is 2 retries when none are set, so an absent `retries` block is not zero.
2. Call `mesh_retry_amplification` with `calls` (each with `retries`, and `budget_percent` only where the data plane really enforces an Envoy retry budget: a VirtualService has no such field, so otherwise omit it) and `max_multiplier` 2 (default of this skill; the SLO or task overrides). Keep `worst_case_multiplier`, `over_budget`, and per hop `cumulative_load`. Load on hop k is the product of (1 + retries) up to k when every attempt fails.
3. Decide: `RETRY-CYCLE` (a service twice on the path) is a blocker. `RETRY-AMPLIFICATION` (major, `over_budget` true) means cut retries at the deepest hops first, keep retries at one layer only (the edge or the caller of the flaky dependency), not at every layer. `RETRY-NESTED` (two or more hops retry without budget) is minor: fix when touching the chain.
   Apply the rule to the `mesh_retry_amplification` hops, deepest hop first.
4. Call `mesh_timeout_hierarchy` with the same `calls` and `min_gap_ms` (default 1; use 50 or more for network hops, this skill's rule of thumb). Keep `edge_budget_ms`, `tightest_gap_ms`. A caller's attempt budget (`per_try_timeout_ms`, else `timeout_ms`) must exceed the callee's total `timeout_ms`. `TIMEOUT-INVERTED` and `TIMEOUT-PERTRY-EXCEEDS-TOTAL` are major; `TIMEOUT-RETRIES-EXCEED-TOTAL` and `TIMEOUT-THIN-MARGIN` minor.
5. Fix by working from the leaf: set the leaf timeout to the dependency's p99 times about 2 (from A13 data, never guessed), then each caller's `per_try_timeout_ms` = callee timeout + hop margin, and `timeout_ms` >= `per_try_timeout_ms * (retries + 1)`. Re-run both tools until `over_budget` false, `tightest_gap_ms` >= `min_gap_ms` and verdict `pass`.
   Compute each value with `bash` arithmetic and keep the table for step 8.
6. Cross-check the result against the VirtualService text: call `mesh_traffic_split` with the proposed `virtual_services` and `destination_rules`; it must not raise `TRAFFIC-PERTRY-EXCEEDS-TIMEOUT`, `TRAFFIC-RETRY-BUDGET`, `TRAFFIC-RETRY-NO-TIMEOUT` or `TRAFFIC-DURATION-INVALID` (Istio rejects `timeout: 0s`; omit the field instead). Its timeout view and step 4 must agree for each edge; a disagreement is E-CONTRACT (different manifests): report both.
7. Check the end-to-end budget: `edge_budget_ms` must not exceed the client or load-balancer timeout in the task (for example an ingress idle timeout of 60s); if it does, shorten inner budgets rather than raising the edge.
8. Write the table (hop, before, after, multiplier, gap) to `mesh-reports/<cluster>/retry-budget-<service>.md` with `write`, digest it with `shared_artifact_digest`. Applying to production is L3 under `skill://a23-mesh-network--traffic-shifting-plan` style approval: BLOCKED needs=human-approval.

## Outputs

- `mesh.policy.set` (retry/timeout variant): per-hop retries and timeouts, final `worst_case_multiplier` and `tightest_gap_ms`, report digest.

## Failure Handling

- INVALID_INPUT (`calls` chain broken, `timeout_ms` not positive, `budget_percent` outside 0..100): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked hops keep their old settings. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Application-level retries (client libraries, gRPC `retryPolicy`, queues) are invisible to the mesh: ask the owners and add them as hops, or state the multiplier is mesh-only.
- No approval means no apply; never retry an apply.

## Handover

- `to_agent`: A22 (manifests); A13 is told the new timeouts for SLO alignment. `goal`: the chain and budget. `completed`, `decisions` (multiplier limit, margins), `artifacts` (table, digests), `open_questions` (client retries, latency data), `verification` (both tools pass).
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/retry-timeout-budget.md; edit the source and run scripts/build_agents.py -->
