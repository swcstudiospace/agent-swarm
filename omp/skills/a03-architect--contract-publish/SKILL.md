---
name: "a03-architect--contract-publish"
description: "Use when a design.contract task needs a non-breaking OpenAPI or AsyncAPI revision validated, versioned, digest-pinned and sent to its consumers."
disable-model-invocation: true
---

## When to Use
- `design.contract` with a contract draft that you expect to be additive or compatible.
- If any step below shows a breaking change, stop and follow `skill://a03-architect--breaking-change`; this skill never reaches L3.
- Rules (prompts/A03 GUARDRAILS and constraints): run the contract check before any publication; semver every contract; pin consumers to digests; keep N-1 compatibility for the 30-day deprecation window.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take the NFR bindings, the previous contract path and `consumers` from it. When the previous revision exists only in git, extract it with `bash` (`git show <rev>:<path>`) to a scratch file.
2. Use `write` to draft the revised contract; put the NFR clause values into it (vendor extension such as `x-p95-ms` is your convention, not a requirement).
3. Parse the contract to an object (JSON as is; YAML with `python3 -c` and `yaml.safe_load`, which needs PyYAML) and call `arch_contract_ops` with `{document}`. Record `operations`; it must equal `contracts[].operations` from step 4.
4. With `bash` run `python3 scripts/arch_contract_check.py --file <contract> --base <previous> --task-id <task_id> --correlation-id <correlation_id> --json` (ids from the assignment). Read `breaking`, `findings[].severity`, `verdict`, `skipped`.
5. For each changed request or response shape call `arch_field_breaks` with `{side, base_fields, head_fields}` (`name`, `type`, `required`). `breaking_change` true, or a non-empty `breaking` in step 4, means leave this skill.
6. Call `arch_semver_bump` with `{base_version, head_version, breaking_change: false, additive_change}` (versions from `info.version`; `additive_change` true when `additive` is non-empty or operations were added). Need `compliant` true.
7. Call `shared_verdict` with `{severities}` taken from the script's `findings`. `fail` blocks publication.
8. Call `shared_artifact_digest` with the contract `path`; consumers are pinned to this digest.
9. Call `shared_handover_validate` (`draft` true), then `shared_handover_write` once per consumer.

## Outputs
- Contract file, version, digest, operation count, `consumers` list.
- Script verdict and `arch_semver_bump` result.

## Failure Handling
- Script not runnable, `python3` missing, or a non-empty `skipped` (YAML without PyYAML): FAILED `E-DEP`; the check is required for every `api.contract` and is never inferred.
- `compliant` false: correct `info.version`, rerun steps 4 to 6.
- `INVALID_INPUT` names the field; fix once, then `shared_escalate` with `reason_code` `blocked`.

## Handover
Fill `goal`, `completed`, `decisions` (version choice, rationale), `artifacts` (kind `api.contract`, `uri` the file, `digest`, `producer` A03), `constraints` (pin to digest, N-1 for 30 days), `open_questions` and `verification` (`status`, `confidence`, `checks`). One write each for A05, A06, A07 and A08 that appears in `consumers` (the receiver's downstream `task_id` when the plan names it, else yours).

<!-- generated from prompts/skills/a03-architect/contract-publish.md; edit the source and run scripts/build_agents.py -->
