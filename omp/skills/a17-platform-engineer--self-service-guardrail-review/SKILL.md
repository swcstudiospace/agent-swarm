---
name: "a17-platform-engineer--self-service-guardrail-review"
description: "Use when a self-service policy change (regions, classes, sizes, caps, prod replicas) must be tested on boundary requests and capacity before approval."
disable-model-invocation: true
---

# Self-service guardrail review

## When to Use

- A proposal edits the self-service policy: a new region or class, a larger `max_size` or `auto_approve_size`, higher cpu, memory or storage caps, or a lower `min_prod_replicas`.
- Deciding individual requests is `skill://a17-platform-engineer--environment-request-intake`; quota sizing is `skill://a17-platform-engineer--tenant-quota-allocation`. This skill tests a policy, not a request.
- Tightening a rule is L2 once the impact list is attached. Loosening one (more regions, larger sizes, higher caps, fewer prod replicas) is treated here as L3, like `tenant_quota_change`, because it lets teams consume more without a review; if the owner disagrees, escalate for the level.

## Procedure

1. `read` the current policy and the proposed policy. Write both as the tool inputs: `allowed_regions`, `allowed_classes`, `size_order`, `auto_approve_size`, `max_size`, `max_cpu_cores`, `max_memory_gib`, `max_storage_gib`, `min_prod_replicas`. List every changed field with old and new value.
2. With `write`, build the test set of `requests` (`id`, `environment`, `region`, `size`, `service_class`, `replicas`, `cpu_cores`, `memory_gib`, `storage_gib`): one real request per class from the history, plus boundary cases for each changed limit: exactly at the cap, just above it, the old and new `max_size`, and in `prod` with `min_prod_replicas` and one fewer replica. Boundary ids are named `t-<field>-<at|above>`.
3. Call `plat_request_guardrails` with the old policy and the test set, then again with the new one. A decision must be fully determined by the tool: `allow`, `needs_approval` (size above `auto_approve_size` but not above `max_size`) or `deny`.
4. Diff the `plat_request_guardrails` decisions per `id`. Tightening shows as allow to needs_approval, or needs_approval to deny; loosening the other way. Any request that moved from `deny` to `allow` by a loosening change is listed with its old finding ids (`size-above-max`, `cpu-above-cap`, `region-not-allowed` ...).
5. Cross-check each `plat_request_guardrails` boundary: a request exactly at a cap must not carry the `-above-cap` finding (the tool denies strictly above); one above it must. An unexpected result means the test case or the policy is mis-stated: fix the input, do not explain it away.
6. Call `plat_quota_fit` with `capacity`, current `allocations` and each tenant's worst case under the loosened limits (cap times expected count of requests); `oversubscribed` (major) means the new cap cannot be honoured by capacity, so the loosening is refused until capacity or quota changes.
7. Tightening: list the existing consumers that would now be denied on their next change, so the owner can announce it. For any loosening, call `shared_escalate` with `{task_id, reason_code: "policy", summary: "self-service guardrail loosening awaiting approval", needs: ["human-approval"]}` and return its `result_patch`.
8. `write` the decision matrix (old vs new per `id`) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `platform.claim` (the proposed policy as a claim) with the old and new decision matrix and the capacity result.

## Failure Handling

- INVALID_INPUT (`auto_approve_size` or `max_size` missing from `size_order`, auto larger than max, duplicate request `id`, blank size): fix the named field once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never certify an untested policy.
- PARTIAL_SUCCESS: use `completed_fraction`, name unrun cases.
- The size names are the caller's catalogue; no size ladder is assumed, defer to `size_order`.

## Handover

`to_agent`: `A11` when the policy is enforced in a pipeline gate, else the assigner. Fill `goal`, `decisions` (each changed limit, direction), `blockers` (approval), `artifacts` (matrix with digest, producer `A17`), `open_questions` (consumers affected), `verification`. End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a17-platform-engineer/self-service-guardrail-review.md; edit the source and run scripts/build_agents.py -->
