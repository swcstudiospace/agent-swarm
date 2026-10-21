---
name: a26-smart-contract--audit-finding-remediation
description: Use when audit.findings, audit.report or a failing review/gate verdict lists contract defects to fix with a reproducing test and a minimal patch each.
owner: a26-smart-contract
tools: [shared_open_findings, shared_escalate, sol_access_matrix, sol_upgrade_safety, sol_test_gate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A27 `audit.findings` / `audit.report`, A09 `review.verdict` or an A08/A09/A10/A12 `gate.verdict` names defects in on-chain code. Fixes are L2.
- Not for deciding whether a finding is valid or how severe it is (A27 and the gate owners decide; a disagreement is escalated, step 6), not for accepting a risk (human only), and not for a new feature (`skill://a26-smart-contract--foundry-test-first`). Re-audit of the fixes is `skill://a27-contract-audit--fix-verification-reaudit`.

## Procedure

1. Call `shared_open_findings` with `{task_id}` and read `findings`, `blocking`, `stale_count`. Stale entries come from verdicts replaced by newer ones: ignore only those flagged `stale: true`. `read` the audit report and write the table `finding id -> severity -> location -> class -> status` (`open`). A finding without a file and line is sent back as an `open_question` to its issuer, not guessed.
2. `grep -n` each finding's location, then order: blockers and criticals first, then majors, minors last; group findings in the same function so one patch fixes them together. Classes and the standard fix: reentrancy (checks-effects-interactions, then `nonReentrant` as a second layer), unprotected writer (guard with the least role, `skill://a26-smart-contract--access-control-roles`), unsafe initializer or upgrade (`skill://a26-smart-contract--upgradeable-proxy-design`), unchecked low-level call (check the returned `bool` or use a library), rounding or precision (round against the user, test the boundary), signature replay (`skill://a26-smart-contract--eip712-signature-design`).
3. For each finding write the reproducing test first (the audit PoC when present) and run it with `bash` `forge test --match-test <name> -vvv`: it must fail for the reported reason before the fix. A test that already passes means the finding is not reproduced: record that evidence and go to step 6, do not patch blindly.
4. `edit` the smallest change that fixes it; no formatting, renaming or refactoring outside the lines touched, so A27 can re-audit the diff. One commit per finding group, message naming the finding ids.
5. Re-run the checks the finding class needs: for access findings call `sol_access_matrix` with `source` and `public_by_design` (`unprotected` must be 0, `verdict` `pass`); for proxy or initializer findings call `sol_upgrade_safety` with `source` and `pattern` (`verdict` `safe`, no `UPG-*` blocker). Then run `python3 scripts/sol_contract_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <contracts dir> --json`: `counts.major` and `counts.blocker` must not exceed the count before the fix. Cross-check: the lint and the tool must not contradict each other (an `SOL-DELEGATECALL` major with `UPG-DELEGATECALL` absent is E-CONTRACT; report both).
6. A finding you believe is invalid or already mitigated is not closed by you: call `shared_escalate` with `reason_code: "conflict"`, the finding id, the test that passes without a fix, and `needs: ["input"]`; the issuer decides. A finding that needs a design change beyond the task is `blocked`, a risk acceptance is a human decision, never marked by this agent.
7. Call `sol_test_gate` with lcov `files` (fixed files not below their previous line coverage), `min_line_pct` and `min_branch_pct` at least the project's current values (not lowered), `tests_failed` 0, `tests_skipped` 0, `fuzz_tests`, `fuzz_runs` (the lowest per-test run count, not a total), `min_fuzz_runs` 1000, `invariant_tests`, `require_invariants` true for value-holding code. Call `shared_open_findings` again with `{task_id}`; every finding you list as fixed has its test, and none of the tool outputs above reproduces it.
8. `write` `reports/<task_id>-remediation.md`: for each finding the status (`fixed`, `disputed`, `blocked`), the test name, the commit, the tool evidence; call `shared_artifact_digest` for it and the patch.

## Outputs

- `code.patch` (per-finding commits with tests) and a remediation table for A27; ABI or gas changes are reported with `skill://a26-smart-contract--abi-stability-gate` and `skill://a26-smart-contract--gas-optimization-pass`.

## Failure Handling

- INVALID_INPUT: `pattern` outside the enum, `source` over 100000 characters, counts that contradict each other: fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction` and the findings not yet addressed; DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing`; a fix without its test stays `unverified`.
- The static tools are heuristic text analysis: a clean result does not prove a finding fixed; the failing-then-passing test is the evidence.
- Deploying or upgrading a fixed contract on a live network is not part of this skill: BLOCKED needs=human-approval (L3 or L4).

## Handover

- `to_agent`: `A27` (re-audit), with `A09` for review. `completed`: fixed ids; `remaining`: disputed and blocked ids with reasons; `artifacts`: patch, remediation report with digests; `verification`: test names and tool verdicts.
- End with `shared_handover_validate`, then `shared_handover_write`.
