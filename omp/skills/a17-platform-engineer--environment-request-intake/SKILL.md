---
name: "a17-platform-engineer--environment-request-intake"
description: "Use when a self-service environment or resource request arrives and needs an allow, needs_approval or deny decision with a validated platform.claim for A11."
disable-model-invocation: true
---

# Environment request intake

## When to Use

- A team files one or more requests (`dev`, `staging` or `prod`: region, size, service class, replicas, cpu, memory, storage) through the platform portal, with an `environment.record` (A11) of what exists and a `cost.report` (A20).
- Changing the guardrail policy itself is `skill://a17-platform-engineer--self-service-guardrail-review`; changing a team's quota is `skill://a17-platform-engineer--tenant-quota-allocation`. You decide requests; A11 provisions them and you never apply infrastructure.
- Deciding and writing a claim is L2; any `prod` request that is not plain `allow` stays with a human.

## Procedure

1. `read` the policy file named in the task and the request set. Policy inputs: `allowed_regions`, `allowed_classes`, `size_order` (smallest to largest), `auto_approve_size`, `max_size`, `max_cpu_cores`, `max_memory_gib`, `max_storage_gib`, `min_prod_replicas`. A missing policy value is BLOCKED `needs=input`; never use a default you made up.
2. Call `plat_request_guardrails` with that policy and `requests` (`id`, `environment`, `region`, `size`, `service_class`, `replicas`, `cpu_cores`, `memory_gib`, `storage_gib`). Read `decisions[]`, `allowed_count`, `needs_approval_count`, `denied_count`.
3. Cross-check `plat_request_guardrails`: the three counts must sum to `request_count`; every `deny` has at least one major finding with id `<rule>@<request id>` (`region-not-allowed`, `class-not-allowed`, `size-unknown`, `size-above-max`, `cpu-above-cap`, `memory-above-cap`, `storage-above-cap`, `replicas-below-prod-minimum`); every `needs_approval` has only the minor `size-needs-approval`. A mismatch is E-INTERNAL.
4. For each request that is not `deny`, call `plat_quota_fit` with `capacity` from the `environment.record`, `allocations` (`team`, `resource`, `quota`, `used`) including the request added to the team's `used`, and the policy's `reserve_pct` and `overcommit_ratio` if stated. `over-quota` or `oversubscribed` (major) turns the decision into a refusal with the shortfall; never raise a quota here.
5. For a short-lived environment call `plat_ephemeral_env_policy` with `checked_at`, `max_ttl_hours`, `idle_after_hours`, `max_cost_usd` (from the `cost.report` hourly figures) and `environments` (`name`, `created_at`, `ttl_hours`, `hourly_cost_usd`, `owner`). `ttl-missing` is a blocker: the request must carry a TTL.
6. For each `allow`, build the claim as `{path, type, value}` rows (never a credential as a value: use the name of its secret reference) and validate it with `plat_claim_validate` against the platform's composition `schema`; `valid` must be true before the claim leaves this skill.
7. `write` the decisions: `needs_approval` requests are BLOCKED `needs=human-approval` for that id only, other ids proceed. `deny` requests are returned to the requester with the finding ids and the allowed values from the policy.
8. `write` one claim file per allowed request; call `shared_artifact_digest` with `{path}` for each.

## Outputs

- `platform.claim` per allowed request, a decision table (`id`, `decision`, finding ids), quota and TTL evidence.

## Failure Handling

- INVALID_INPUT from `plat_request_guardrails` (`auto_approve_size` or `max_size` not in `size_order`, auto larger than max, duplicate `id`): fix the policy field named, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; an undecided request is `needs_approval`, never `allow`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the undecided ids.
- Sizes and classes are catalogue names from the policy, not a standard: defer to tool output.

## Handover

`to_agent`: `A11` (provisions through its pipeline); `A21` when the claim lands on a cluster. Fill `goal`, `completed`, `decisions` (id to decision), `blockers` (approvals), `artifacts` (claims with digests, producer `A17`), `verification`. End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a17-platform-engineer/environment-request-intake.md; edit the source and run scripts/build_agents.py -->
