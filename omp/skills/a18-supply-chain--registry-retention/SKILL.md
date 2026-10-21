---
name: "a18-supply-chain--registry-retention"
description: "Use when a container registry repository needs a tag retention plan that keeps deployed digests and signatures and stays under a delete-ratio limit."
disable-model-invocation: true
---

# Registry retention plan

## When to Use

- A registry repository has grown (cost, clutter, stale vulnerable images) and an `iac.change` or `ci.pipeline` task needs a plan of which tags to keep and delete. The tool only plans: nothing is deleted. Output: `image.policy.report`.
- Not for choosing which images are safe to run: `skill://a18-supply-chain--base-image-refresh`. Storage cost sizing is A20's.
- The plan is L2 (`image_policy`). Executing deletes in a registry is irreversible: this skill treats it as L4 and never runs a delete. Raising `max_delete_pct` or shrinking protection to pass is L3.

## Procedure

1. Use `read` on the task for the policy: `keep_latest`, `min_age_days`, `protected_prefixes` (release tag patterns such as `v1.` or `release-`), `max_delete_pct`. The policy gives the numbers; when it is silent use `keep_latest` 20, `min_age_days` 30, `max_delete_pct` 50 and say they are this skill's defaults. Never choose a larger `max_delete_pct` than the policy.
2. Get the tag listing read-only with `bash` (`skopeo list-tags` plus `skopeo inspect` for each digest and push time, or the registry CLI) or from the task; build `tags` as `[{tag, digest, pushed_at}]` (ISO-8601 with offset, at most 800; page larger repositories by prefix and plan each page). Names must be valid OCI tags and unique.
3. Get `protected_digests`: every digest currently deployed or referenced, from the `iac.change` and the deploy handovers of A11, A22 or A24 (read with `shared_handover_read`), or from the task. No deployed list: BLOCKED needs=input; an empty list is not a default.
4. Call `supply_retention_plan` with `tags`, `keep_latest`, `min_age_days`, `protected_prefixes`, `protected_digests`, `max_delete_pct` and `as_of` (`date -u +%Y-%m-%dT%H:%M:%SZ`). Read `tags_total`, `keep_count`, `delete_count`, `delete_pct`, `keep_reasons` (in-use-digest, protected-prefix, recent-count, min-age), `orphaned_digests` and `verdict`.
5. Decide from the `supply_retention_plan` output: `retention.delete-ratio-exceeded` (major) means `delete_pct` is above `max_delete_pct`: the circuit breaker tripped, so do not deliver a delete list as ready; investigate why (wrong prefixes, empty protected list) instead of raising the limit. `retention.deletes-everything` (blocker) means no tag is kept at all: the plan is rejected whatever `max_delete_pct` says; fix the policy inputs. `retention.shared-digest` (minor) means a tag to delete shares its digest with a kept tag: delete that tag by name only, never by digest, or the kept tag goes with it. `retention.protected-digest-unlisted` (minor) means a deployed digest has no tag in the listing: the listing is probably incomplete, rerun step 2 before trusting the plan.
6. Cosign stores signatures and attestations as separate tags named `sha256-<hex>.sig`, `.att` and `.sbom` next to the image. Cross-check: for every entry of `delete_tags` of that shape whose `<hex>` is a digest of a kept tag, the plan would orphan evidence of a kept image; exclude it by adding its exact tag to `protected_prefixes` and rerun `supply_retention_plan` (step 4). For deleted images, signature tags of a digest that has no remaining tag may go with it, but `orphaned_digests` must be reported.
7. Check the `supply_retention_plan` counts: `delete_count` plus `keep_count` equals `tags_total`, and that no tag of `delete_tags` carries a digest from `protected_digests`; any hit is E-CONTRACT.
8. Call `shared_verdict` with the plan finding severities as evidence only. Write the list as a reviewable file (`delete_tags`, digest per tag) and call `shared_artifact_digest`. The delete itself: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "registry tag delete awaiting human execution", "needs": ["human-approval"]}` and return its `result_patch` as BLOCKED.

## Outputs

- `image.policy.report`: the plan (counts, `keep_reasons`, `orphaned_digests`, `delete_tags` file with digest), parameters and their source, breaker status.

## Failure Handling

- INVALID_INPUT naming `tags`, `protected_digests` or `as_of` (bad tag, digest, `pushed_at` after `as_of`): fix that entry once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; paged plans are combined only when each page passed.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; registry CLI missing: BLOCKED needs=tool.
- Registry-specific garbage collection and immutable-tag settings are not modelled by the tool: defer to the registry docs and say so.
- Never retry the escalation or any delete.

## Handover

- `to_agent`: A11 (owns registry and pipeline). `goal`: a human executes or rejects the delete list; `completed`: plan and cross-checks; `decisions`: parameters; `artifacts`: plan file and report with digests (producer A18); `blockers`: `human-approval`; `constraints`: no delete without approval; `verification`: verified when steps 5 to 7 passed.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/registry-retention.md; edit the source and run scripts/build_agents.py -->
