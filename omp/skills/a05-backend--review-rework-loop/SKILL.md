---
name: "a05-backend--review-rework-loop"
description: "Use when A09 returns CHANGES_REQUESTED or a gate verdict fails your patch; count the loop, fix by finding, rerun checks, ask for arbitration at loop 2."
disable-model-invocation: true
---

## When to Use
- A `review.verdict` of CHANGES_REQUESTED or a failing `gate.verdict` arrives for your `code.patch` (A05 spec section 5 review loop).
- Run `skill://swarm-rework-intake` first: it owns the loop count (`shared_attempt_history`), the finding list, verdict provenance, `shared_dod_check` and progress reports. This skill adds only the A05 delta below.
- Findings from the `security` gate (A10) are `a05-backend--security-remediation`.

## Procedure
1. Call `shared_open_findings` with `{task_id}` and keep findings whose `gate` is `review` or `quality`; findings of gate `security` go to the security skill. The call returns an empty list for the verdicts that caused the rework (known registry defect), so an empty list is not "no findings". Call `shared_handover_read` with `{task_id}` and take the comments from `handover.remaining` and `handover.blockers`, plus the `review.verdict` or `gate.verdict` entries of `inputs[]`; match each to a finding `id` and `location`.
2. Fix in your own backend files only. Never edit the reviewer's tests, never weaken or skip a test to satisfy a finding. Rerun the failing check of each finding with `bash`.
3. Rerun `python3 scripts/code_checks.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --json` and `python3 scripts/be_contract_conformance.py` with the same ids and `--contract <contract file>`. Update `digest` (re-digest the new diff as in the self-gate) and `checks` in the patch and call `be_patch_shape_check` with `{patch}`. `ready_for_review` must be true.
4. Second CHANGES_REQUESTED (A05 spec section 5), or a disagreement that persists: call `shared_escalate` with `{task_id, reason_code: "conflict", summary, needs: ["synchronous arbitration of the review loop: finding ids"]}`. The spec names A01 as arbiter; the returned `owner` is who is accountable. End BLOCKED with the returned `result_patch`.
5. Otherwise call `shared_handover_write` to the gate agent that issued the verdict (`gate` `review` is A09, `quality` is A08).

## Outputs
- A new patch revision with fresh digest, a list `finding <id>: fixed|contested`, the loop number.

## Failure Handling
- A finding contradicts the contract: do not obey it silently; use `a05-backend--contract-deviation-request`.
- `ready_for_review: false` after fixes: continue fixing; do not resubmit.
- Tool unavailable: retry twice, then report the step as `skipped:tool-missing`.

## Handover
`completed` = fixed finding ids, `remaining` = contested ids with a reason, `decisions` = why a finding was declined, `artifacts` = new patch digest, `verification.checks` = step 3 results. Sent with `shared_handover_write` to the issuing gate agent.

<!-- generated from prompts/skills/a05-backend/review-rework-loop.md; edit the source and run scripts/build_agents.py -->
