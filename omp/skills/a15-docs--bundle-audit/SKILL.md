---
name: "a15-docs--bundle-audit"
description: "Use when a docs.bundle task or post-merge docs pass must find broken links, heading skips, missing titles or broken anchors and decide if it is publishable."
disable-model-invocation: true
---

## When to Use
- A `docs.bundle` task arrives, or docs changed after a merge and you must say whether the bundle is publishable (prompts/A15-docs.md OPERATING_PROCEDURE steps 2-3 and 5).
- Not for coverage percentages against targets: use `a15-docs--coverage-report`. Not for secrets or approval: use `a15-docs--public-docs-gate`.

## Procedure
1. Call `shared_handover_bundle` with `task_id`. Missing `task_id`, capability or `inputs[]` is BLOCKED `needs=input`; no tool calls beyond this.
2. Run `python3 scripts/docs_bundle.py --task-id $TASK --correlation-id $CORR --json` and the twin `bun scripts/ts/docs_bundle.ts` with the same flags; add `--docs-dir <dir>` only to narrow the scan. Exit 0 ok, 1 finding-fail, 2 taxonomy error. Read `status`, `index` (per doc: `path`, `title`, `headings`, `links`, `broken_links`), `findings` (already numbered DF-nnn) and `coverage` from stdout.
3. For every fragment whose heading structure you cite, call `docs_heading_order` with `markdown` set to that fragment. For a whole document, `h1` other than 1 or `skips` above 0 is a `heading` finding, severity minor.
4. Use `grep` with pattern `^[\s>]*(```|~~~)` on each doc. The script blanks both fence kinds (``` and ~~~), also inside a blockquote (`> ```md`; the closing fence must sit at the same `>` depth and the quote ending ends the fence). An unclosed fence runs to the end of the document, so a file with an odd fence count has unreliable heading and link results; record a minor finding naming the file. Fences inside list items are matched by indentation only: `read` such a file before you trust its results.
5. Cross-check the script's `broken_links` with `docs_link_targets`: `files` is every `path` in `index` plus any non-markdown file a link targets (found with `glob`), `links` is each `{source, target}` found with `grep` pattern `\]\([^)]*\)` per doc. A link only one side reports needs a manual `read` of both files before you classify it.
6. For each doc that has `#fragment` links (the script skips them), call `docs_anchor_check` with `headings` (the heading texts, in order, of the page the fragment points at) and `references` (the fragments). Each entry in `broken` is a finding with `kind` `broken-link`, severity minor, summary "broken anchor".
7. Classify every finding from `docs_heading_order`, `docs_link_targets`, `docs_anchor_check` and the script per the Severity rule (prompts/A15-docs.md decision_logic 4): broken internal link is major; missing title, skipped level, coverage gap and broken anchor are minor. Number new findings after the script's last DF-nnn. Any major means the bundle is not publishable and the state is not IN_REVIEW.
8. Call `shared_artifact_digest` with `path` of the `docs_index.json` the script wrote (in the swarm state dir, `.swarm/` unless SWARM_DIR is set) so the evidence is listed with a digest.

## Outputs
- docs.bundle JSON (`output_format` in prompts/A15-docs.md: `findings`, `coverage`, `stale`); the publishable verdict goes in the markdown summary, not as a JSON field.
- Evidence list: script exit codes per twin, tool results cited per finding.

## Failure Handling
- Link or coverage check cannot run: fail closed, report the bundle `fail` with a finding naming the check; never publish.
- A twin missing: `skipped:tool-missing` for it and use the other; both missing is FAILED `E-DEP`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing` for that fragment; never estimate `h1` or `skips`.

## Handover
Fill `goal` (publish or fix the bundle), `completed` (checks run), `remaining` (minor backlog), `artifacts` (`docs_index.json` with digest, producer `A15`), `blockers` (each major finding), `verification` (`partial` if any check skipped). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent named in task routing, A01 when unknown.

<!-- generated from prompts/skills/a15-docs/bundle-audit.md; edit the source and run scripts/build_agents.py -->
