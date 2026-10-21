---
name: a09-reviewer--verdict-assembly
description: Use when all findings are final and the signed review verdict must be recorded with swarm_gate, risk-tiered, self-validated and handed to A01.
owner: a09-reviewer
tools: [shared_handover_bundle, rev_severity_histogram, shared_verdict, rev_risk_tier, rev_verdict_shape, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- The semantic and any lens findings are complete and no more will be added.
- You must record the gate and emit the result block with the task state (IN_REVIEW, BLOCKED or FAILED).
- Not after a disagreement with A08 or A10 (`a09-reviewer--conflict-escalation`).

## Procedure
1. Gather your own findings per gate target (the `gate_for` ids named in the assignment) from the stage skills, with `categories`, `co_sign_required` and `diff_lines` from `a09-reviewer--risk-routing`. Call `shared_handover_bundle` with `task_id` for the goal and `acceptance`. Missing analysis (LLM degraded, a lens skipped) fails closed: add one `major` finding `ANALYSIS.incomplete` to the affected target, because only a finding at `major` or above makes `swarm_gate` fail a target.
2. Plan the verdict: call `rev_severity_histogram` and `shared_verdict` with the severities of your findings. Any `major` or above fails.
3. Record the gate once: call `swarm_gate` with `gate` "review", `task_id` (your gate task), `correlation_id` and `per_target_findings` `{target: [finding]}`, listing every target (an empty list adds nothing; a target you omit gets every finding). Findings carry only `severity`, `summary`, `id`, `kind`, `evidence`, `ac_ref`, `owner_suggestion`, `location`. The script adds its own mechanical findings and signs; a failing verdict returns text starting `FAIL:`. Never run `scripts/rev_gate.py` through `bash` in omp (the guard blocks it) and never pass `dry_run`. Outside omp, run it as a non-recording preview and report the result as advisory.
4. Read the recorded result with `read` on `.swarm/verdicts/<gate task_id>.review.json`: `payload.verdict`, `payload.findings` (script and yours), `payload.mode` (`rules+semantic` or `rules-only`), `payload.expires_s` (172800). Call `rev_severity_histogram` and `shared_verdict` over every `payload.findings` severity; the derived verdict must equal `payload.verdict`. A mismatch is reported as E-INTERNAL naming both values, and the stricter verdict stands.
5. Call `rev_risk_tier` with `categories`, `diff_lines`, the real `blocking_count`, `max_severity` and `co_sign_required`. This replaces the provisional tier from routing.
6. Build the block from step 4: `gate` "review", `task_id`, `correlation_id`, `pr`, `verdict`, `blocking` (each `{file, line, rule, md}` from `location`, `kind`, `summary`; by convention use line 1 when a finding has no line), `non_blocking` (each `{rule, suggestion_md}`), `risk_tier`, `co_sign_required`, `mode`, `expires_s`. Call `rev_verdict_shape` with `block`. When `valid` is false, fix the fields it lists once and call again; a second failure is FAILED E-CONTRACT (REPAIR_PROTOCOL). A `fail` needs at least one blocking item and a `pass` none.
7. A request to waive is L3: call `shared_escalate` with `task_id`, `reason_code` "policy", a `summary` and `needs` ["human-approval"]; the result is BLOCKED, never a self-waiver.
8. Call `shared_artifact_digest` with `path` `.swarm/verdicts/<gate task_id>.review.json`, then `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`. Finish with `yield` carrying the block (a fenced `json` block outside omp).

## Outputs
- A short summary (diff size, checks, reason) and the block: `gate`, `task_id`, `correlation_id`, `pr`, `verdict`, `blocking`, `non_blocking`, `risk_tier`, `co_sign_required`, `mode`, `expires_s`.

## Failure Handling
- Any tool disagreement or cross-check failure is reported, not hidden; the cross-check is "skipped", never "passed".
- No signing key in an agent session: `swarm_gate` records nothing and the verdict is advisory; report it as unsigned.
- `swarm_gate` E-INPUT (a key that is not a target): fix the target id once.
- Uncited claims go under UNVERIFIED and do not change the verdict.

## Handover
- Fill `decisions` (verdict, tier and why), `artifacts` (kind `gate.verdict`, the verdict path, digest from step 8, producer A09), `blockers` (a waiver request), `verification` (`verified` when the shape check passed).
- Call `shared_handover_write` with `to_agent` A01 (A01 accepts `gate.verdict`).
