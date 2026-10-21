---
name: a09-reviewer--size-intake
description: Use when a new PR is assigned for review and its diff must be sized against the 400-line split budget before any code is read.
owner: a09-reviewer
tools: [shared_handover_bundle, rev_diff_size, shared_handover_validate, shared_handover_write]
---

## When to Use
- A `gate.review` or `review.code` task has just arrived and no size decision exists for it.
- You are about to read the diff and must first know whether it is reviewable in one pass.
- Not for judging quality: `a09-reviewer--mechanical-pass` previews the checks, this skill only sizes.

## Procedure
1. Run `swarm-handover-intake` first; it decides BLOCKED on a missing, stale or malformed handover. Then call `shared_handover_bundle` with `task_id` and take the goal, `constraints` and `upstream[]`. Never guess what the producer meant.
2. Count the diff the way the gate does, with `bash`: `git diff --numstat <base>` (`<base>` = `HEAD~1`, else `origin/main`; `swarm_gate` takes no `diff_base`) summed into `added` and `deleted` per file, plus every untracked file from `git ls-files --others --exclude-standard` as all-added. A `-` in numstat is a binary file: structural checks only.
3. Call `rev_diff_size` with the summed `added`, `deleted` and `max_lines` 400, the split-request guidance of the prompt (error_handling) and spec section 5. The gate script's own `--max-lines` default of 800 is a different limit: one file over it becomes a `major` `size` finding.
4. `over_budget` false: record "size ok" and go on. `over_budget` true: write the split request (which files or concerns to separate), keep only the rules-only preview, and finish BLOCKED needs=input with the request (prompt Sequence 2). Do not guess a semantic verdict. Only an assignment that names the emergency path lets two independent REV replicas cross-review the large diff.
5. `rev_diff_size` DEPENDENCY_UNAVAILABLE: retry the same input twice (prompt Sequence 3), then compare the numstat total with 400 yourself and record `skipped:tool-missing`.

## Outputs
- Size decision: total changed lines, `over_budget`, budget used (400), the largest files from numstat.
- A split request when over budget; otherwise a "size ok" note for the next stage.

## Failure Handling
- `rev_diff_size` TIMEOUT: retry once, then E-TIMEOUT.
- INVALID_INPUT: the result names the field; fix the integer and call again once.
- Binary or huge files: structural checks only, reported as `info` findings (spec section 5).
- git missing: FAILED E-DEP; distinguish a skipped tool from a missing runtime.

## Handover
- When within budget there is no handover of its own: the size decision feeds the next stage.
- When BLOCKED, call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` with `to_agent` A01, `goal` ("split PR <pr> for review"), `completed` (diff measured, budget compared), `remaining` (the split to be made, then re-review), `decisions` (rules-only, line counts as rationale), `blockers` (the split request), `constraints` (budget 400, per-file ceiling 800), `verification` (`verified`, or `partial` when `skipped:tool-missing`).
