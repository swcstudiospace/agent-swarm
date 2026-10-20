---
name: a22-gitops--env-promotion-pr
description: Use when a gitops.promotion task moves a build to the next stage; check values drift and gate preconditions, then open the PR without merging.
owner: a22-gitops
tools: [gitops_helm_values_drift, gitops_kustomize_check, gitops_image_policy, gitops_promotion_gate, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Environment promotion PR

## When to Use

- A `gitops.promotion` task promotes one workload version (a `build.artifact` from A11) from `source_env` to the next environment, as a change to the target overlay in Git.
- A12 decided that it ships (`release.plan`); this skill does not re-decide it. Calendar freezes: `skill://a22-gitops--change-freeze-window`. Undoing a promotion: `skill://a22-gitops--rollback-by-revert`.
- Autonomy: promotion into non-prod is L2, into production L3. You open the PR; humans review and merge it.

## Procedure

1. Read the `release.plan` and `build.artifact` with `read`: the image digest (`sha256:`), `env_order`, `source_env`, `target_env`, required checks, `min_soak_minutes`, approvals needed. A missing soak time or approval count is BLOCKED needs=input; never default it.
2. Call `gitops_image_policy` with `image` (the reference as it will be written to the target overlay), `candidate_tags`, `require_digest: true` for production. `digest_pinned` must be true for production and `floating` false; `verdict: fail` stops the promotion.
3. Call `gitops_helm_values_drift` with `environments` (name plus parsed values per stage), `required_keys`, `invariant_keys` (image repository, probes, security context) and `reference` set to `source_env`. Only deliberate per-environment keys (replicas, hosts, resources) may diverge: `diverging_value_count` is reported, any finding on an invariant key is `major` or worse and blocks.
4. Change only the image reference (and values the plan names) in the target overlay with `edit`, then call `gitops_kustomize_check` with the overlay `kustomization` and `resources`: `unresolved_patch_count` must be 0 and no duplicate id or namespace collision may appear.
5. Collect PR facts with `bash` (`gh pr view <n> --json statusCheckRollup,reviews,author,headRefOid`) and call `gitops_promotion_gate` with `now` (UTC from `date -u`), `env_order`, `source_env`, `target_env`, `deployed_at` (when the source became healthy), `min_soak_minutes`, `checks`, `required_checks`, `approvals`, `required_approvals`, `pr_author`, `head_sha`, `target_is_production`, and `freeze_start`/`freeze_end` when a freeze exists.
6. Decision rule on `verdict`: `ready` opens or marks the PR ready; `waiting` (a `major` such as `soak-incomplete`, `approvals-missing`) is reported with `soak_remaining_minutes` and a re-check time; `blocked` (`stage-skipped`, `required-check-failed`, `change-freeze`, `changes-requested`) stops. Cross-check `autonomy_level` against the target: production must read L3, otherwise E-CONTRACT.
7. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <overlay-dir> --json --require-digest`. A `fail` status (exit 1) on the touched overlay blocks the PR even when the gate said `ready`.
8. Push `<seat-prefix>/<task_id>`, open the PR with `gh pr create` stating digest, soak, checks and rollback commit; never merge. For production call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]`. Call `shared_artifact_digest` on the changed overlay files.

## Outputs

- `promotion.record`: source, target, digest, gate `verdict`, `valid_approval_count`, soak minutes, PR URL, autonomy level.
- Evidence: tool verdicts and script counts.

## Failure Handling

- INVALID_INPUT (`target_env` not after `source_env`, `deployed_at` later than `now`, one-sided freeze pair): fix the field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; a gate not fully evaluated is `waiting`, never `ready`.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and treat the gate as not passed.
- Reviews, checks and timestamps come from the forge; if `gh` is unavailable, BLOCKED needs=tool. Never infer an approval.
- L3: BLOCKED needs=human-approval. Never retry `gh pr create` after a PR exists.

## Handover

`to_agent` A15 (docs record the promotion; A12 reads the same `promotion.record`). Fill `goal`, `completed`, `decisions` (gate verdict, digest), `artifacts` (overlay files with digests, producer A22), `open_questions` (pending approvals), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
