---
name: "a18-supply-chain--dockerfile-hardening"
description: "Use when a Dockerfile fails lint for root user, secrets in ARG/ENV, pipe-to-shell, ADD misuse or package caches and needs a hardened rewrite checked by lint."
disable-model-invocation: true
---

# Dockerfile hardening

## When to Use

- A `code.patch` that adds or changes a Dockerfile or Containerfile, or a failing `scripts/supply_image_lint.py` run, needs the file brought to policy. Output: `image.policy.report` and a rewritten Dockerfile as a proposed patch.
- Not for choosing or aging the base image: `skill://a18-supply-chain--base-image-refresh`. Not for workflow or dependency pinning: `skill://a18-supply-chain--pin-and-update`.
- Autonomy: L2 (`image_policy`). A secret that was already built into an image is an A10 secret incident (`skill://a10-security--secret-triage`); rotating it is not yours.

## Procedure

1. Run `python3 scripts/supply_image_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <Dockerfile> --json`. Read `status`, `blocking_count` and each finding `id` with its `location` (`<file>:<line>`). The script lints with `require_digest_pin` false, so tag-only bases are minor there.
2. Use `read` to get the Dockerfile text and call `supply_dockerfile_lint` with `content`, `require_healthcheck` true and `require_digest_pin` true (tag-only becomes major). Cross-check: every script finding for that file (same `id`, same line) must appear in `findings`; the only expected difference is `docker.base-not-digest-pinned` major versus minor. Any other mismatch is E-CONTRACT.
3. Triage by severity from the `supply_dockerfile_lint` output. `blocker`: `docker.secret-in-env`, `docker.credential-in-url` in `ENV`/`ARG` (major in `RUN`): stop and tell A10, do not keep the secret anywhere in the rewrite, and never copy a value into the report (the tool reports names and lines only). `major`: `docker.final-user-missing`, `docker.final-user-root`, `docker.secret-in-arg`, `docker.pipe-to-shell`, `docker.add-remote-unverified`, `docker.copy-secret-file`, `docker.latest-tag`, `docker.base-unresolved-arg`. `minor`: `docker.pkg-cache-*`, `docker.pip-cache`, `docker.add-instead-of-copy`, `docker.single-stage-build-tools`, `docker.no-healthcheck`.
4. Rewrite with `edit`, one fix per finding: numeric non-root `USER` in the final stage (for example `USER 10001:10001`) after creating the user; build secrets via `RUN --mount=type=secret,id=<name>` instead of `ARG`/`ENV`; downloads with a pinned version and verified checksum (`ADD --checksum=sha256:<hex>` or `sha256sum -c`), never `curl | sh`; `COPY` instead of `ADD` unless extracting a local archive; `apt-get install --no-install-recommends` with `rm -rf /var/lib/apt/lists/*` in the same `RUN`, `apk add --no-cache`, `pip install --no-cache-dir`; multi-stage so only the artifact reaches the final stage; `HEALTHCHECK` (or state that the orchestrator probes it). Digests come from the registry, never invented.
5. Call `supply_dockerfile_lint` again on the new `content`. Accept only when `verdict` is `pass` and `blocking_count` is 0, and `findings_total` has not grown. Then call `supply_pin_audit` with `files` `[{path, content}]` for the Dockerfile: `unpinned` must be 0 for its image references (the same rule, the second tool must agree with the lint).
6. Re-run `scripts/supply_image_lint.py` on the changed path; `status` must be ok. A fix that removes one finding and adds another is not a fix: list both.
7. Call `shared_verdict` with the final finding severities as evidence for A11 (it is not the gate verdict). Call `shared_artifact_digest` for the Dockerfile and the report.

## Outputs

- `image.policy.report`: findings before and after (ids, lines), the rewritten Dockerfile as `code.patch` with digest, and any rule left open on purpose with its reason.

## Failure Handling

- INVALID_INPUT on `content` (empty or no `FROM`): BLOCKED needs=input; the script reports it as `docker.no-from`.
- Dockerfile over 100000 characters or lint PARTIAL_SUCCESS: report `completed_fraction` and the unread part; do not claim pass.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- A `blocker` that cannot be removed without a design change (an installer that needs a credential at build time): BLOCKED needs=human-approval for an L3 exception.
- Rules here come from the tool; where Docker or BuildKit syntax differs by version (`--checksum`, `--mount`), defer to `docker buildx build` errors and report them.

## Handover

- `to_agent`: A11. `goal`: build the hardened Dockerfile; `completed`: lint before and after; `decisions`: each finding fixed or kept with reason; `artifacts`: report and patch with digests (producer A18); `open_questions`: design-level blockers; `verification`: verified only when step 5 and 6 passed.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a18-supply-chain/dockerfile-hardening.md; edit the source and run scripts/build_agents.py -->
