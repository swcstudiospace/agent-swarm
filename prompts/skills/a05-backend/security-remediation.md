---
name: a05-backend--security-remediation
description: Use when A10 returns security.findings on your code; fix by severity, re-prove auth and license items, and stop at L3 for auth or data-access boundary changes.
owner: a05-backend
tools: [shared_open_findings, shared_handover_read, be_route_auth_coverage, be_license_allowlist_check, shared_escalate, shared_handover_write]
---

## When to Use
- A10 findings (SAST, secret, dependency, license, missing auth) arrive on your `code.patch` (A05 prompt `<inputs>`: `security.findings`; spec section 7 security rules).
- Reviewer comments are `a05-backend--review-rework-loop`; defect-driven fixes are `a05-backend--hotfix-minimal-patch`.

## Procedure
1. Call `shared_open_findings` with `{task_id}` and keep the findings whose `gate` is `security` (other gates are `a05-backend--review-rework-loop`). The list is already most severe first; keep each `id`, `kind`, `location` and `blocking` (`major` or worse). The call returns an empty list for the verdicts that caused a rework (known registry defect): then take the findings from A10's `handover.remaining` and `handover.blockers` (`shared_handover_read`) and the `gate.verdict` or `security.findings` entries of `inputs[]`.
2. Decide each finding from `shared_open_findings` (or its fallback) by its class from `kind` and `location`: auth, license, secret, injection or other code weakness.
3. Auth findings: list the affected routes with `grep` and, after the fix, call `be_route_auth_coverage` with `{routes: [{method, path, auth}], public: [...]}`. `unprotected` must be empty. Using an existing guard is L2; changing how authentication or data access works beyond the contract is L3 (prompt `<autonomy>`).
4. License findings: call `be_license_allowlist_check` with `{packages: [{name, license}], allowlist}`; `violation_count` must be 0. Replacing the package is a new dependency: use `a05-backend--new-dependency-request`.
5. Secret findings: with `edit` remove the value from code, load it from the runtime injection the stack uses, and list the credential as exposed in the handover `blockers` so a human rotates it. Do not rewrite git history.
6. Injection and similar code findings: fix at the `location` (parameterized queries, output encoding per spec section 7) and add a test that fails before the fix, run with `bash`.
7. Rerun `python3 scripts/code_checks.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json`; every value in `results` is `pass` or `skipped:tool-missing`. No SAST or secret scanner runs inside it, so say which finding checks were only reasoned, not rerun.
8. For any L3 change call `shared_escalate` with `{task_id, reason_code: "policy", summary, needs: ["human-approval: <change>"]}` and report BLOCKED needs=human-approval; otherwise call `shared_handover_write` to A10.

## Outputs
- Closed finding ids with the fix and test per id, the auth and license verdicts, a blocked list for L3 items.

## Failure Handling
- A finding you consider a false positive: do not suppress it; state the reason in `decisions` and leave it for A10 to rule.
- INVALID_INPUT from a tool: fix the named field once, otherwise report the check as unrun.
- Findings expire: re-run `shared_open_findings` before final handover so the closed list matches the live one.

## Handover
`completed` = closed finding ids, `remaining` = open or blocked ids, `blockers` = rotations and approvals needed, `decisions` = false-positive reasoning, `verification.checks` = step 3, 4 and 7 results. Sent to A10 with `shared_handover_write` (validation as in `skill://swarm-handover-emit`).
