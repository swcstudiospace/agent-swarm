---
name: "a16-cloud-architect--reference-architecture-publish"
description: "Use when a reusable cloud reference architecture is ready to publish and must pass the scan, twelve-factor and scorecard gates first, with a versioned digest."
disable-model-invocation: true
---

# Reference architecture publish

## When to Use

- A `cloud.architecture` task produced a pattern others will copy (a service template, a landing zone blueprint, a multi-AZ web tier) and it is about to be published under `docs/cloud/reference/`.
- Not for reviewing someone else's repository (`skill://a16-cloud-architect--wellarch-review`) and not for recording a single decision (`skill://a16-cloud-architect--cloud-adr-handoff`).
- Autonomy: publishing a document in the repository is L2. Marking a reference as the mandated standard for all teams is a policy decision (L3) and needs a human.

## Procedure

1. Run `python3 scripts/cloud_readiness_scan.py --task-id $TASK --correlation-id $CORR --root . --path <reference sample dir> --json` against the sample code and IaC shipped with the reference. Gate: `status` is `ok` (exit 0; exit 1 means a major finding), `truncated` false, `skipped` empty. A reference that fails its own scan is not published.
2. Call `cloud_twelve_factor_audit` with the `service` descriptor of the reference workload. Gate: `conformant_factors` is 12, or every non-conformant factor has a written waiver in the document; `findings` of severity `major` or `blocker` have no waiver option.
3. Call `cloud_wellarch_scorecard` with `answers` covering all six pillars (each pillar at least one answer) and the `pillar_weights` the task sets. Gate: `verdict` `pass`, `high_risk_count` 0 and no pillar scored under 60 percent (this skill's publication floor). The scan `overall_score_pct` and the tool `overall_score_pct` may differ because the answer sets differ, but a pillar where the manual answers say `none` risk and the scan found a major finding is E-CONTRACT.
4. When the reference includes a landing zone, call `cloud_landing_zone_lint` with the documented `accounts` and `break_glass`. Gate: `verdict` `pass` and `guardrail_coverage_pct` 100. Examples with deliberately broken settings are labelled "anti-example" and excluded from this call.
5. Assemble the document with `write` at `docs/cloud/reference/<name>.md`: purpose, diagram in text, version (`<name>-v<major>.<minor>`), the tool outputs of steps 1-4 verbatim, supported providers and regions as the task states them, and the "use when / do not use when" list. Cost and carbon figures are quoted from the latest `cost.report` and `carbon.report` only; none are invented.
6. Call `shared_dod_check` with `{result: <draft result>, criteria: [<the task's acceptance criteria>]}`; every criterion must be `met`. `unknown` means evidence is missing from the document: add it.
7. Call `shared_artifact_digest` with `{path}` of the document and of each sample file it names; record `{kind, uri, digest, producer}` with producer A16.
8. Call `shared_artifact_check` with `{task_id, outputs}` listing those artifacts; `ok` must be true and `failed` empty. Then call `shared_handover_validate` (`draft` true) and `shared_handover_write`. A re-publication changes the version; a published version is never overwritten.

## Outputs

- `cloud.architecture`: the published reference document with its version and digest, and the gate evidence of each check.

## Failure Handling

- Script exit 2, `INVALID_INPUT` or a missing sample directory: fix `--path` or the named field; else BLOCKED `needs=input`.
- `PARTIAL_SUCCESS`: use `completed_fraction`; do not publish with an unevaluated gate.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`; publication waits.
- `shared_artifact_check` `failed` non-empty: re-digest after the last edit and recheck.
- The 60 percent pillar floor is this skill's rule, not a standard; the scorecard `verdict` is authoritative.
- A mandate for all teams is BLOCKED `needs=human-approval` (L3); never retried.

## Handover

`to_agent` A17 (platform team adopts the reference); further writes to A03, A19, A21, A23 and A20 as their consumption needs it. Fill `goal`, `completed`, `decisions` (waivers), `artifacts` (kind `cloud.architecture`, digest, producer A16), `open_questions`, `verification` (`verified` only when every gate passed). End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a16-cloud-architect/reference-architecture-publish.md; edit the source and run scripts/build_agents.py -->
