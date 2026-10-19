---
name: a09-reviewer--maintainability
description: Use when a large or long-lived area of code is touched and the review needs a maintainability lens: comment density, dead code, debug leftovers, naming.
owner: a09-reviewer
tools: [shared_handover_bundle, rev_comment_density]
---

## When to Use
- The diff adds many source lines or changes code that others will keep for a long time.
- Maintainability is a review dimension (spec section 1) and no other skill covers it.
- Not for correctness (`a09-reviewer--semantic-pass`) or for test quality (`a09-reviewer--test-adequacy`).

## Procedure
1. Call `shared_handover_bundle` with the gated target's `task_id` for the goal and the producer's `completed` list: which files are new and which are refactors.
2. Collect the added lines of each source file with `bash` (`git diff -U0 <base> -- <file>`, lines starting with `+` and not `+++`; `<base>` is the one the gate uses, `HEAD~1` else `origin/main`), one array per file or per module. Skip generated and vendored files and say so.
3. Call `rev_comment_density` with `added_lines`. Read `nonblank_lines`, `comment_lines` and `density` (preprocessor and shebang lines are not counted as comments). The spec sets no numeric limit: use the figure as evidence for a remark, never as a gate.
4. Use `grep` on the added lines for commented-out code blocks of 3 or more lines, leftover debug output, TODO markers and unclear names. The gate script raises `dead-code`, `debug` and `todo` findings (minor) itself when `swarm_gate` records the verdict, so do not repeat those three; add only what it cannot see (naming, comment quality, duplicated logic). Record each remark with `severity` minor or info, `summary`, `kind` (rule ID by convention, for example `MAINT.naming`), `location` as `path:line` and a concrete suggestion in `evidence`. By convention of this skill maintainability remarks never reach `major` on their own, so they cannot fail the gate by themselves.

## Outputs
- Density figures per file and a short list of minor findings with suggestions.

## Failure Handling
- Empty `added_lines` (a pure deletion): density is 0 by definition; report "nothing added" and stop.
- Generated or vendored files: skip them and say so; do not review them.
- If `rev_comment_density` is unavailable, count comment lines by hand and mark the figure UNVERIFIED.

## Handover
- No handover of its own: the remarks and density figures feed `a09-reviewer--verdict-assembly`.
- On cancel or pause, hand over per `swarm-cooperative-cancel`: `completed` (files examined), `decisions` (remarks kept, with density as evidence), `verification` (`partial`).
