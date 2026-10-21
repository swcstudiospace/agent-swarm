---
name: a15-docs--terminology-align
description: Use when the monthly glossary-alignment task runs or a new term is introduced, and deprecated or variant terms must be replaced across the docs set.
owner: a15-docs
tools: [shared_handover_bundle, docs_terminology_drift, docs_anchor_check, shared_open_questions, shared_handover_validate, shared_handover_write]
---

## When to Use
- The monthly glossary-alignment task (agents/A15-docs.md section 5: linter violations are batched into it) or a newly introduced preferred term.
- Vocabulary only. Sentence difficulty is `a15-docs--readability-pass`.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; the glossary (each `preferred` term with its `deprecated` variants) comes from the task `goal`, `handover.document` or a listed artifact that you `read`. No glossary means BLOCKED `needs=input`: never infer terms.
2. Call `shared_open_questions` with `task_id` so terms already raised as ambiguous are not re-raised.
3. Use `glob` with `**/*.md` for the scope, `read` each file, and call `docs_terminology_drift` with `glossary` and `files` (`{path, text}`, at most 500 files per call, at most 200000 characters of text per file). Read `violations` (`path`, `line`, `found`, `preferred`), `by_term` and `pass_pct` (the share of files without a violation).
4. `edit` each violation to its `preferred` term, one preferred term at a time using `by_term` as the work list. The tool skips fenced code and inline code, so quoted commands and API names are never touched; do not edit them either.
5. `read` around each violation that might be legitimate (a quotation, a product name, a third-party API). Keep such a hit as written and add it to `open_questions` for the glossary owner instead of editing.
6. If a heading text changed, call `docs_anchor_check` with `headings` (that file's heading texts, in order) and `references` (the `#fragment` links that point at it, found with `grep`); `broken` anchors are fixed in the same edit.
7. Re-run `docs_terminology_drift` until `pass_pct` is at least 99 (section 6: terminology lint pass at least 99 percent) or only listed exceptions remain.

## Outputs
- Violations fixed per term, final `pass_pct`, and the list of kept exceptions with reasons.

## Failure Handling
- INVALID_INPUT naming `glossary` (one term deprecated for two preferred terms, a term equal to its own preferred, duplicates): the glossary is ambiguous; send it to its owner as an open question and stop.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; do not edit by memory of the glossary.
- Over 500 files: split into several calls; the overall pass rate is the sum of clean files (`files_checked` times `pass_pct`) over the sum of `files_checked`. A file over 200000 characters is reported as not checked.

## Handover
Fill `goal`, `completed` (terms aligned, final `pass_pct`), `remaining` (files not yet clean), `open_questions` (ambiguous terms), `constraints` (kept exceptions), `verification` (`verified` at `pass_pct` 99 or more). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent named in task routing, A01 when unknown.
