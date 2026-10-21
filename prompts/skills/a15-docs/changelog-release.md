---
name: a15-docs--changelog-release
description: Use when a release.record is available and a Keep-a-Changelog section must be built from its conventional commits, with counts and order verified.
owner: a15-docs
tools: [shared_handover_bundle, shared_artifact_check, docs_conventional_groups, docs_changelog_section_order, docs_heading_order, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- A12 publishes a `release.record` and capability `docs.changelog` is assigned (prompts/A15-docs.md decision_logic 6: one section per release record).
- Not for coverage metrics: use `a15-docs--coverage-report`. Not for release decisions: A15 never makes them.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; take the release record artifact and the release window start (a date) from it. When the record names only a tag, get its date with `bash` `git log -1 --format=%cI <tag>`.
2. Call `shared_artifact_check` with `task_id`, `outputs` (the release record, `producer` included) and `agent` `A12`. Read `results[].status`: only `ok` continues; `missing` goes to `a15-docs--missing-artifact-stub`.
3. Run `python3 scripts/docs_bundle.py --task-id $TASK --correlation-id $CORR --json --changelog --since <window start>` (and the bun twin, same flags). Read `changelog_md`; the value `skipped:tool-missing` means git is absent: report it and stop the changelog.
4. Use `bash` with `git log --no-merges --format=%s --since=<window start>` to list the subjects the script used, then call `docs_conventional_groups` with `subjects`. Check parity on totals only: the bullets over all sections of `changelog_md` equal `total`, and the `**BREAKING**` markers equal `breaking`. Per-section tallies differ by design (the script files every breaking subject under Changed and sends revert, docs and security subjects to Removed, Documentation and Security, where the tool counts them as `other`). A total mismatch is a defect in your block, not in the commits.
5. Call `docs_changelog_section_order` with `sections` (the `###` headings of the block in order). The script emits Added, Changed, Fixed, Removed; Keep-a-Changelog puts Removed before Fixed, so expect `out_of_order` to name `Removed`: reorder with `edit`. A script group outside the six Keep-a-Changelog names (`Documentation`, `Other`) shows in `unknown`: keep it (prompt rule: docs/chore/other go to Other) and record it as a minor finding.
6. Replace the script's `## [Unreleased] - since <date>` heading with the version and date from the release record, then call `docs_heading_order` with `markdown` set to the block: `h1` 0 and `skips` 0 are required for a fragment.
7. List every `unparsed` subject from the `docs_conventional_groups` result of step 4 for human review in `open_questions`; the script shows them under Other as raw text, so never move them to Added or Fixed.
8. Use `write` to save the block at the path the task `outputs[]` names (with none named, `CHANGELOG.md`, a convention of this skill), then call `shared_artifact_digest` with that `path`.

## Outputs
- `changelog_md` for the release with `breaking` count, `unparsed` list and section-order verdict.

## Failure Handling
- Build only from script output and the release record, never from memory of commits.
- Tool INVALID_INPUT: fix `subjects` or `sections` once; else BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing` for that check; keep the script block unverified.

## Handover
Fill `goal`, `completed`, `remaining` (unparsed subjects), `artifacts` (changelog file with digest, producer `A15`), `open_questions` (unparsed subjects, unknown sections), `verification` (`verified` only with count parity and valid order). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent the assignment routes to (normally `A12`, the release owner), A01 when unknown.
