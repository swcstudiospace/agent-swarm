---
name: a18-supply-chain--base-image-refresh
description: Use when base images in Dockerfiles are stale, near end of life, off the allowed registries or tag-only and need a policy check with a proposed refresh.
owner: a18-supply-chain
tools: [supply_base_image_policy, supply_dockerfile_lint, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Base image refresh

## When to Use

- A `build.artifact` or `ci.pipeline` task names Dockerfiles whose `FROM` images may be old, end-of-life (EOL), outside the allowed registries or pinned by tag only; the output is an `image.policy.report` plus a proposed `code.patch`.
- Not for hardening the rest of the Dockerfile (USER, secrets, caches): that is `skill://a18-supply-chain--dockerfile-hardening`. Not for CVE triage of the image: that is `skill://a18-supply-chain--vex-triage` and A10.
- Autonomy: editing a Dockerfile on a task branch and reporting is L2 (`image_policy`). A policy waiver (keeping an EOL or off-registry base) is L3 and never yours to grant.

## Procedure

1. Run `python3 scripts/supply_image_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir-or-Dockerfile> --json`. Exit 0 is `status` ok, exit 1 is `status` fail (a major or worse finding), exit 2 is an input error (`--path` missing or outside `--root`). Keep `files_scanned.dockerfiles`: zero means nothing was linted, not a pass.
2. Use `read` to list every `FROM` reference per Dockerfile (resolve `ARG` defaults; a base that depends on an ARG without default is unchecked, say so). Take `created_at` (ISO-8601 with offset) and `eol_date` (YYYY-MM-DD) from the task or from registry metadata read with `bash` (`skopeo inspect` or `docker buildx imagetools inspect`, read-only). Never guess a date: omit the field and accept the `image.age-unknown` info finding.
3. Call `supply_base_image_policy` with `images` (`ref`, `created_at`, `eol_date`), `allowed_registries` from the task policy (none supplied is BLOCKED needs=input), `require_digest` true, `max_age_days` from the policy (when the policy is silent use 90, state that it is this skill's default), `eol_warning_days` 90 and `as_of` the current instant (`date -u +%Y-%m-%dT%H:%M:%SZ`).
4. Read the decision fields: `violating_images`, `blocking_count`, `verdict` and each finding id. `image.latest-tag`, `image.registry-not-allowed`, `image.too-old`, `image.end-of-life` and `image.not-digest-pinned` (major because `require_digest` is true) block; `image.eol-soon` (minor) schedules a refresh before the date but does not block. An `eol_date` equal to the `as_of` date already counts as end of life, and an image exactly `max_age_days` old still passes.
5. Cross-check: call `supply_dockerfile_lint` with `content` of each Dockerfile and `require_digest_pin` true. Its `final_base` must equal the last `FROM` you read, and each base finding (`docker.latest-tag`, `docker.base-not-digest-pinned`) must correspond to an `image.*` finding. A base the Dockerfile lint flags but the policy call never saw is a missed `FROM`: rerun step 3.
6. Propose the refresh with `edit` on the task branch: newest supported tag of the same distribution, with its `@sha256:` digest read from the registry (never invented), keeping the tag as a comment. Switching distribution family (Debian to Alpine, glibc to musl) is a design change: list it as an open question for A03 instead.
7. Re-run `supply_base_image_policy` (step 3) and `supply_dockerfile_lint` (step 5) on the changed content. The refresh is accepted only when `verdict` is `pass` and `violating_images` is 0 for the new references.
8. Call `shared_verdict` with the severities of the final findings; it is evidence for A11, not a gate verdict. Call `shared_artifact_digest` with the report path and the patch path.

## Outputs

- `image.policy.report`: per-image `verdict`, finding ids, `as_of`, parameters used (with which came from policy and which from this skill's defaults) and the proposed `code.patch` with its digest.

## Failure Handling

- INVALID_INPUT naming `images`, `as_of` or `eol_date`: fix that field once (date format, `created_at` after `as_of`), else BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing` for that tool and mark the report partial.
- Registry metadata unreachable: report age and EOL as unchecked; never mark `pass`.
- A base with EOL passed and no supported successor, or any request to keep it: BLOCKED needs=human-approval (L3).
- The script and `supply_dockerfile_lint` disagree on a base: E-CONTRACT with both outputs.

## Handover

- `to_agent`: A11 (owns the pipeline that rebuilds on the new base). `goal`: rebuild and promote the refreshed images; `completed`: checks run; `decisions`: parameters and which findings blocked; `artifacts`: report and patch with `shared_artifact_digest` digests (producer A18); `open_questions`: distribution changes, unchecked dates; `verification`: verified only when step 7 passed.
- End with `shared_handover_validate` then `shared_handover_write`.
