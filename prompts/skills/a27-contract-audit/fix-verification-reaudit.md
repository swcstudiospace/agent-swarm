---
name: a27-contract-audit--fix-verification-reaudit
description: Use when fixes for audit findings arrive as a code.patch or fix commits and each needs commit, regression-test and re-audit verification plus residual risk.
owner: a27-contract-audit
tools: [audit_fix_verification, shared_verdict, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A `code.patch` (from A05, A06, A19, A26 or A29) or a list of fix commits answers findings in `audit/findings.json`.
- First-time finding discovery is the review skills; the final publication is `skill://a27-contract-audit--audit-report-publish`.
- Re-audit is L2. Marking a finding `acknowledged` or `wontfix` is an L4 risk acceptance: you record a human's decision, never make one.

## Procedure

1. `read` the patch and the findings file. Map each finding to its fix commit: the author supplies `fix_commit`; a finding without one stays `open`.
2. Verify each commit with `bash`: `git cat-file -t <sha>` must print `commit`; `git merge-base --is-ancestor <scope_commit> <sha>` must succeed; `git diff --stat <scope_commit> <sha> -- src` lists the changed contract files. A changed file the finding does not explain is new code: it is new attack surface, scope it (`skill://a27-contract-audit--audit-scoping`) and say so in `open_questions`.
3. Re-run the PoC with `bash` against the fix in a scratch worktree created by the runner (`git worktree add`, local and reversible): `forge test --match-path audit/poc/<ID>.t.sol`. The exploit must now fail (the PoC's assertion or an expected revert flips). The exploit still succeeding keeps the finding `open`, whatever the author says.
4. Regression test, checked with `bash` (`forge test`) at both commits: a test that fails at `scope_commit` and passes at the fix commit and that exercises the finding's path. Check both runs. No such test means `regression_test` false.
5. Read the diff with `read` for the root cause, not the symptom (a check added on one path while a second path remains, a guard on the wrong function). Re-run the relevant triage on the changed files only. Set `reaudited` true only after this reading, and `fix_status` to `fixed` (root cause removed) or `mitigated` (harm reduced, cause remains).
6. Call `audit_fix_verification` with `findings` = `[{id, severity, fix_status, fix_commit, regression_test, reaudited}]` (`fix_commit` an empty string when none; 7 to 40 or 64 hex characters otherwise, anything else is INVALID_INPUT). A finding is verified only with commit, test and re-audit; otherwise it counts as `unverified` (weight 0.5). `verified_mitigated` carries weight 0.25; open or accepted weight 1. Severity weights are 16, 8, 4, 2, 0 for critical down to info.
7. Read `residual_pct`, `residual_level`, `release_ready` and the `FIX-<id>` findings. Decide: `release_ready` true only when no unresolved finding is high or critical; any `FIX-` finding of high or critical is `fail`. Cross-check with `python3 scripts/audit_findings_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path audit/findings.json --json`: its `by_status` open count must equal the tool's `open`; its `fix-unverified` majors correspond to the tool's `unverified` for high or critical findings; a gap is E-CONTRACT.
8. Call `shared_verdict` with `severities` of the unresolved findings mapped to the gate scale (info to `info`, low and medium to `minor`, high to `major`, critical to `critical`); it must be `fail` exactly when the tool `verdict` is `fail` (a tool `warn` maps to `pass`). Any finding the team wants `acknowledged` or `wontfix` at high or critical: call `shared_escalate` with `task_id`, `reason_code` `policy`, `summary`, `needs` `["human-approval"]` (L4), return the `result_patch`, and leave the status as `open` until a risk acceptance reference exists. Update statuses in the findings file with `write`, digest it with `shared_artifact_digest`.

## Outputs

- Updated `audit.findings` (statuses, `fix_commit`, `regression_test`, `reaudited`) and a fix-review record: counts, `residual_pct`, `residual_level`, `release_ready`.

## Failure Handling

- INVALID_INPUT (non-hex `fix_commit`, duplicate ids): fix the field from `git`, never invent a hash.
- DEPENDENCY_UNAVAILABLE (`forge`, `git`): retry once, then `skipped:tool-missing`; unverifiable fixes stay `unverified`, not `fixed`.
- Fix present but PoC cannot be re-run (environment drift): `unverified`, name the blocker.
- Never lower a severity because it was fixed: severity describes the finding as found; the fix is tracked by `fix_status`.

## Handover

- `to_agent`: `A10` (consumer of `audit.findings`; the gate decides). `decisions`: per finding status and why. `blockers`: L4 acceptances pending. `artifacts`: findings file and fix-review with digests. `verification`: verified when PoC and regression runs were executed for every high and critical.
- End with `shared_handover_validate`, then `shared_handover_write`.
