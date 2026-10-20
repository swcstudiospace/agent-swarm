---
name: "a22-gitops--change-freeze-window"
description: "Use when a release freeze, blackout or upgrade window limits production syncs; encode it as Argo CD sync windows or Flux suspend and prove the gate honours it."
disable-model-invocation: true
---

# Change freeze window

## When to Use

- A12 `release.plan` or A21 `cluster.upgrade.plan` declares a window in which production must not change (end-of-quarter freeze, an upgrade, an incident), and the GitOps setup must enforce it, not just announce it.
- Not for choosing release dates (A12) and not for emergency fixes: a revert during a freeze is `skill://a22-gitops--rollback-by-revert` with a human approval. Promotion mechanics remain `skill://a22-gitops--env-promotion-pr`.
- Encoding a window in non-prod is L2; production windows are L3 (they change production sync behaviour). Lifting a freeze early is a human decision.

## Procedure

1. Read the windows with `read` from `release.plan` and `cluster.upgrade.plan`: start and end in ISO-8601 with an offset (UTC preferred), scope (environments, clusters, applications) and the exceptions named. Missing offset or scope is BLOCKED needs=input: never assume a time zone.
2. Get the instant with `bash` (`date -u +%Y-%m-%dT%H:%M:%SZ`) and call `gitops_promotion_gate` with `now`, `freeze_start` and `freeze_end` (always together; start inclusive, end exclusive), plus the stage fields of a pending promotion: `env_order`, `source_env`, `target_env`, `deployed_at`, `min_soak_minutes`, `checks`, `required_checks`, `approvals`, `required_approvals`, `pr_author`, `head_sha`, `target_is_production`. Inside the window `findings` contains `change-freeze` (blocker) and `verdict` is `blocked`; one second after `freeze_end` it must be gone. Test both instants and record them.
3. Encode the window with `edit` where it is enforced. Argo CD: an `AppProject` `spec.syncWindows` entry with `kind: deny`, a cron `schedule`, `duration`, `applications` or `clusters` selectors, `timeZone` and `manualSync: false` (a deny window with `manualSync: true` still lets a human sync). Flux: no native calendar; use `spec.suspend: true` on production Kustomizations and HelmReleases from a reviewed PR, and a second PR to lift it. Argo `syncWindows` and `suspend` semantics depend on version: defer to its docs.
4. Call `gitops_app_sync_policy` with `resource` (each production Application or Flux object) and `environment: "prod"`. A suspended Flux object reports `suspended` (minor, "drift accumulates"): expected during the window, noted with end time. An Argo app with `auto_sync` true in production remains a `major` finding: a deny window is the only thing stopping it.
5. Check the repo can express the scope: call `gitops_repo_layout` with `paths`, `expected_envs`, `production_envs`, `clusters`, `engine` and `codeowners`; `codeowners_evaluated` must be true and production must be owned, so a freeze change needs the owners' approval, not just any reviewer.
6. Build the lift: write the opposite PR (remove the deny window or set `suspend: false`) as a prepared, unmerged branch `<seat-prefix>/<task_id>` with the end time in its title. Call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]` for both PRs when production is in scope.
7. Run `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json`. Its `apps` findings after the change must not contain more production findings than before: a window must not weaken sync policy elsewhere. Exit 1 blocks the PR.
8. Write the window record (scope, both instants checked, files, PR URLs) with `write` and call `shared_artifact_digest` on it and on the changed files.

## Outputs

- `promotion.record` entry of kind freeze: window, scope, gate results at both instants, enforcement mechanism, PRs.

## Failure Handling

- INVALID_INPUT: fix the named field (`freeze_end` not after `freeze_start`, timestamp without offset) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; a scope not covered is stated as unenforced.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never report a window "enforced" from the announcement alone.
- Cron schedules in `syncWindows` are evaluated in the controller's configured time zone semantics: verify the zone with a human.
- L3: BLOCKED needs=human-approval. Never merge either PR.

## Handover

`to_agent` A15 (publishes the window in the release notes and runbooks; A12 reads the same `promotion.record`). Fill `goal`, `completed`, `decisions` (mechanism, scope), `artifacts` (record and files with digests, producer A22), `open_questions` (exceptions), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a22-gitops/change-freeze-window.md; edit the source and run scripts/build_agents.py -->
