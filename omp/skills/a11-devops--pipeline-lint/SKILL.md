---
name: "a11-devops--pipeline-lint"
description: "Use when a CI workflow, Dockerfile, compose, Terraform or Kubernetes manifest is edited or reviewed and needs a lint, fix and verdict."
disable-model-invocation: true
---

# Pipeline lint

## When to Use

- A task changes or reviews pipeline definitions, Dockerfiles, compose files, `*.tf` or Kubernetes manifests (capability `ci.pipeline`).
- Not for plaintext credentials (use `skill://a11-devops--secret-block`), not for image tag policy at deploy time (use `skill://a11-devops--image-supply-chain`).

## Procedure

1. Call `devops_ci_kind` with `{"rel": "<path>"}` for every path in scope. A returned `kind` labels the path; INVALID_INPUT means the path is not a CI or IaC manifest, so do not lint or edit it as pipeline code (prompt, step 2).
2. Run `python3 scripts/devops_ci_check.py --task-id $TASK --correlation-id $CORR --json` and add `--path <dir-or-file>` to narrow it; run the `bun scripts/ts/devops_ci_check.ts` twin with the same flags. Read `findings[]` (`id`, `severity`, `kind`, `location`), `configs[]` (`kind`, `path`) and the script's own `verdict`; `counts` is the number of configs per kind, not findings.
3. If any finding in the `scripts/devops_ci_check.py` output has `kind` `secret`, stop this skill for that finding and switch to `skill://a11-devops--secret-block`. Never copy its `evidence` text anywhere.
4. For each remaining finding in a file you own, `edit` the file: pin `uses:` refs to a version or commit (not `@main`/`@master`), pin base images (no `:latest`), add a non-root `USER` and a `HEALTHCHECK`, and give Deployment, StatefulSet and DaemonSet `resources.limits` and a `readinessProbe` (checks listed in the `scripts/devops_ci_check.py` docstring). Edit only pipeline definitions and non-prod IaC.
5. Re-run `python3 scripts/devops_ci_check.py --json` on the same `--path`. A finding that is still present after one fix attempt is reported, not hidden.
6. Collect the `severity` of every remaining finding and call `shared_verdict` with `{"severities": [...]}`. Record `verdict` verbatim; the script derives `fail` for any finding at `major` or above.
7. Call `shared_open_findings` with `{task_id}` to see failing gate findings from other agents (A09, A10) on this task; do not report the pipeline done while `blocking` is above zero.
8. Call `shared_artifact_digest` with `{path}` for each file you changed, then see Handover.

## Outputs

- `ci.pipeline` summary: paths with kinds, findings before and after, `verdict`.
- Edited manifest files with digests; a `ci_findings` array in the final JSON block (`id`, `severity`, `kind`, `location`).

## Failure Handling

- Script binary missing: record `skipped:tool-missing` for that twin; never invent findings.
- `shared_verdict` DEPENDENCY_UNAVAILABLE: retry twice, then report the verdict as `fail` (fail closed).
- `devops_ci_kind` INVALID_INPUT twice: BLOCKED `needs=input` naming the path.
- Production infrastructure paths: do not edit; hand over to `skill://a11-devops--prod-infra-change`.

## Handover

Fill `goal` (lint result for the paths), `completed` (fixed findings), `remaining` (unfixed findings with `id`), `artifacts` (changed files with digests, producer A11), `constraints` (autonomy L2, no prod edits), `verification` (`partial` when a twin was skipped). Call `shared_handover_validate`, then `shared_handover_write` to A09 for review, or to A10 when security-relevant findings remain.

<!-- generated from prompts/skills/a11-devops/pipeline-lint.md; edit the source and run scripts/build_agents.py -->
