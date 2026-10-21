---
name: a15-docs--readability-pass
description: Use when a user-facing doc was edited or scores above grade 9, and its wording must be simplified without changing facts, headings or links.
owner: a15-docs
tools: [shared_handover_bundle, docs_flesch_grade, docs_heading_order, docs_anchor_check, shared_handover_validate, shared_handover_write]
---

## When to Use
- A user doc changed, or the coverage report shows median grade above 9 (03-agents/A15-docs.md section 6: median grade at most 9 for user docs).
- Sentence length and word difficulty only. Vocabulary consistency is `a15-docs--terminology-align`.

## Procedure
1. Call `shared_handover_bundle` with `task_id` for the docs in scope (`goal`, `acceptance`, `artifacts`) and the grade limit the task states; the default `max_grade` is 9.
2. `read` each doc and take prose only (drop front matter, code fences, tables of identifiers). Call `docs_flesch_grade` with `text` and `max_grade`; keep `grade`, `reading_ease`, `words`, `sentences`, `syllables`. Also call `docs_heading_order` with `markdown` set to the doc and keep `h1` and `skips` as the baseline.
3. Where `within_target` is false, find the cause: words per sentence is `words / sentences`, syllables per word is `syllables / words`. Use `grep` with pattern `[^.!?]{200,}` to locate the longest sentences.
4. `edit` the wording: split long sentences, replace rare words with plain ones, prefer active voice. Do not change a number, name, command, identifier, link target or heading text.
5. Re-run `docs_flesch_grade` on the same prose and record before and after. Two edit rounds at most; if a doc stays above the limit because of required technical terms, report that and stop.
6. Call `docs_heading_order` again on the edited doc (`h1` and `skips` must equal the baseline) and `docs_anchor_check` with `headings` (the doc's heading texts, in order) and `references` (the `#fragment` links that point at it, found with `grep`); no new `broken` entries.

## Outputs
- Per doc: grade before and after, `within_target`, and the list of sentences changed; the new median grade across the set.

## Failure Handling
- A rewrite would change a fact: leave the sentence as is and list it as remaining.
- Tool INVALID_INPUT (empty `text`): the doc has no prose; skip it and say so.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; do not claim a grade.

## Handover
Fill `goal`, `completed` (docs rewritten with grades), `remaining` (docs still above the limit and why), `constraints` (facts that must not be reworded), `verification` (`verified` only with an after-score for each doc). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent named in task routing, A01 when unknown.
