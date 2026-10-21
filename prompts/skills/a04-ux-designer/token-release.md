---
name: a04-ux-designer--token-release
description: Use when token values change or tokens are removed and the semver bump, deprecation aliases and the L3 approval gate must be decided before release.
owner: a04-ux-designer
tools: [ux_token_semver_impact, ux_token_name_check, shared_escalate, shared_verdict, shared_artifact_digest, shared_handover_write]
---

# Token release

## When to Use
- A token value changes, a token is removed or renamed, or any token is added to a published `design.system.tokens`.
- This is diff-based (old against new). A static lint of one file is `skill://a04-ux-designer--token-lint`.

## Procedure
1. `read` the last published tokens file and the proposed one. Resolve `{path}` aliases to final values, then flatten both to `{dotted.path: scalar}` (string, number or boolean values only); flatten composite tokens (typography, shadow) to leaf paths such as `type.body.size`. The published version is the previous `tokens_version`.
2. Decide a replacement for every token you intend to remove. Compute the diff WITHOUT any alias entries in `new`: call `ux_token_semver_impact` with `{old, new, current_version: "<published MAJOR.MINOR.PATCH>", aliases: {"<removed.path>": "<replacement.path>"}}`.
3. Read the result. `required_bump` and `next_version` are authoritative; a value change (`changed`) or a removal (`removed`) is `major` (`breaking`), an addition is `minor`, an empty diff (`diff_empty`) means there is nothing to release. Every `unaliased_removals` entry has no live replacement in `new`: breaking consumers with no alias is not shippable, so go to step 6.
4. For each aliased removal, use `edit` to add a token at the OLD path whose `$value` is `{replacement.path}` (the alias form `scripts/ux_tokens.py` resolves). Keep it for one release cycle (prompt `error_handling`, 03-agents section 5).
5. Call `ux_token_name_check` over every path of the released file, aliases included, to catch a duplicate or a bad name. Then with `bash` run `python3 scripts/ux_tokens.py --file <released.json> --task-id <task_id> --correlation-id <correlation_id> --json` to confirm each alias resolves (an unresolved alias is a `major` finding).
6. If `l3_required` is true (any `breaking`), or `unaliased_removals` is not empty, call `shared_escalate` with `{task_id, reason_code: "policy", summary: "breaking token release <next_version>", needs: ["human-approval: breaking token version"]}` and return its `result_patch` as BLOCKED. Do not publish.
7. Severities: each unaliased removal is `major`; everything else `info`. Call `shared_verdict` with `{severities}`, then `shared_artifact_digest` with `{path}` of the released file for the handover.

## Outputs
- `next_version`, the lists `added`, `removed`, `changed`, the alias table with its removal release, a release note (what changed, who must migrate), the approval state.

## Failure Handling
- INVALID_INPUT naming `current_version`: take the exact published version from the repo; prerelease suffixes are not accepted. Naming `old` or `new`: flatten the named composite value and retry once, then BLOCKED needs=input.
- Re-running on the same inputs must give the same `next_version` (idempotent); if it differs, a file changed underneath you: re-read both files.
- DEPENDENCY_UNAVAILABLE: retry twice, then BLOCKED needs=tool. TIMEOUT: retry once.

## Handover
Fill `decisions` (bump with its cause), `artifacts` (released file, digest from `shared_artifact_digest`, `producer` A04), `blockers` (L3 approval), `constraints` (alias removal one release later), `verification` (`status`, `confidence`, `checks`). `shared_handover_write` to A06 when released, A01 when blocked.
