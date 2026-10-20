---
name: a22-gitops--rollback-by-revert
description: Use when a failed promotion or canary must be undone through Git; build a revert PR, verify the reverted tree and gate, never roll back imperatively in prod.
owner: a22-gitops
tools: [gitops_canary_verdict, gitops_promotion_gate, gitops_app_sync_policy, gitops_kustomize_check, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Rollback by revert

## When to Use

- A promotion merged into an environment overlay is judged bad (canary `Failed`, A13 burn alert, A12 order) and the previous known-good state is in Git history.
- Not for a canary that is still in flight (abort it in the Rollout and follow `skill://a22-gitops--canary-rollout-design`) and not for live changes made outside Git (`skill://a22-gitops--drift-remediation`).
- Non-prod revert PR and sync: L2. Production revert sync: L3 (human approval, even when urgent). Reverting a commit that deleted data-bearing objects is L4 territory: see `skill://a22-gitops--prune-safety-review`.

## Procedure

1. Identify the bad change with `bash`: `git log --first-parent --oneline -n 20 -- <overlay-dir>`; note the merge or commit SHA, the previous good SHA and the environment. Only a change that touches one overlay is reverted whole; mixed commits are split by hand, never reverted blindly.
2. Confirm the evidence for reverting. When metric samples exist call `gitops_canary_verdict` with `metrics` (`name`, `direction`, `aggregation`, `threshold`, `failure_limit`, `canary_samples`) and `min_samples`. `verdict: Failed` (`action: abort`) justifies the revert; `Inconclusive` is not evidence either way: ask A13 for more data unless the task orders the rollback.
3. Create `<seat-prefix>/<task_id>` and revert: `git revert <sha>` (for a merge commit `git revert -m 1 <sha>`). Resolve conflicts by taking the previous good content; never hand-edit around them. Do not use `argocd app rollback` or `kubectl rollout undo` on production: they change the cluster without Git, and Argo CD refuses `app rollback` while automated sync is on.
4. Call `gitops_kustomize_check` with the overlay `kustomization` and `resources` after the revert: it must be `pass` with `unresolved_patch_count` 0; a revert that leaves patches pointing at removed resources is a new outage.
5. Call `gitops_app_sync_policy` with `resource` (the Application or Flux object) and `environment`. A production Application with `auto_sync` true will deploy the revert the moment it merges: state that, and treat the merge itself as the L3 action. `revision_class` `floating` makes the revert unpredictable: fix before proceeding.
6. Call `gitops_promotion_gate` only for the review facts of the revert PR: `source_env` is the stage before the affected one and `target_env` the affected one (the tool needs target after source), `deployed_at` the time the previous stage turned healthy, `min_soak_minutes: 0` only for an ordered emergency revert (record that decision), `required_approvals` at least 1 for production, plus `checks`, `approvals`, `head_sha`, `pr_author` and any freeze. `blocked` for `changes-requested` or `required-check-failed` is escalated, never bypassed; ignore `soak` findings you zeroed on purpose. The tool never merges.
7. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <overlay-dir> --json --require-digest`; a `fail` status (exit 1) on the reverted tree stops the PR.
8. Open the PR with `gh pr create` naming the reverted SHA, the cause and the expected image digest after revert; never merge. Production: `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]`. Call `shared_artifact_digest` on the PR body file and the changed overlay.

## Outputs

- `promotion.record` of kind rollback: reverted SHA, restored digest, gate `verdict`, `action`, PR URL, autonomy level.

## Failure Handling

- INVALID_INPUT: fix the named field (`deployed_at` later than `now`, `target_env` not in `env_order`) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; name samples or checks missing.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Revert conflict or a migration already applied (database schema) cannot be undone by Git: BLOCKED needs=human-approval with A07 named.
- Never retry a push or PR creation that succeeded; never force-push shared branches.

## Handover

`to_agent` A15 (records the rollback; A12 is told through the same `promotion.record`). Fill `goal`, `completed`, `decisions` (why revert, which SHA), `artifacts` (revert diff file with digest, producer A22), `open_questions` (root cause owner), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
