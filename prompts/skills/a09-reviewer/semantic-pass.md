---
name: a09-reviewer--semantic-pass
description: Use when mechanical results exist and the LLM is available to review the diff against the standards, producing rule-tagged findings with path:line evidence.
owner: a09-reviewer
tools: [shared_handover_bundle, shared_open_findings, shared_verdict]
---

## When to Use
- The mechanical preview is done and the LLM is not degraded.
- You need correctness, standards and architecture-fitness findings that no script can produce.
- Not for contract, test or maintainability lenses: each has its own skill and reads those inputs.

## Procedure
1. Call `shared_handover_bundle` with `task_id` for the goal, the `acceptance` criteria and the `artifacts[]` (the `review.standards` and `architecture.blueprint` files, when A03 supplied them).
2. Call `shared_open_findings` with the `task_id` of each gated target (verdict rows are stored per `gate_for` target, not on your gate task). Failing A08 or A10 verdicts appear with their `gate` and `agent`; use them as context to avoid duplicating a finding, and never override them.
3. Use `read` on the diff (`git diff <base>` through `bash`; `<base>` is the one the gate uses, `HEAD~1` else `origin/main`) and on the standards and blueprint files; use `grep` to follow callers. A09 has no write or edit tool and edits no product code.
4. From the diff you `read`, record each finding as an object with only the fields `swarm_gate` accepts (it refuses any other key): `severity` (`info`, `minor`, `major`, `critical`, `blocker`), `summary`, `kind` (the rule ID, by convention of this skill, for example `ARCH.layering`), `location` (`path:line`), `evidence` (short, quote-free; never a secret or PII value), optional `ac_ref` and `owner_suggestion` (the producer agent, for example A05). A finding with no rule ID or no location is not recorded. Keep the list for `a09-reviewer--verdict-assembly`.
5. Call `shared_verdict` with `severities` (one per finding). Compare the derived `verdict` with the one you planned; a mismatch means fix the severities, not the verdict (any finding at `major` or above fails).

## Outputs
- The finding list, the severity list, the `shared_verdict` result and the mode (`rules+semantic`).

## Failure Handling
- LLM unavailable or degraded: skip this skill and set `mode: rules-only` (the verdict block's `mode` is then `rules-only`); a diff over 400 lines is pended, never guessed.
- `shared_verdict` DEPENDENCY_UNAVAILABLE: retry, then report the cross-check as skipped, not passed.
- Never quote a secret or PII; reference `path:line` only (spec section 7).
- Missing analysis fails closed: the verdict is `fail`.

## Handover
- No handover of its own: the findings feed `a09-reviewer--verdict-assembly`.
- On cancel or pause, hand over per `swarm-cooperative-cancel`: `completed` (files and rules examined), `decisions` (each blocking finding with its rule ID), `open_questions` (anything unverified), `verification` (`partial` when a lens was skipped).
