---
name: a06-frontend--design-deviation
description: Use when the ux.spec is ambiguous or undefined for a state or token, or the UI needs a new pattern or dependency; ask A04 one question and stop at L3.
owner: a06-frontend
tools: [shared_handover_read, shared_open_questions, shared_escalate, shared_handover_write]
---

## When to Use
- The spec is ambiguous, a state or token is not defined, the design system lacks a component, or the work needs a new third-party dependency or UI pattern (A06 spec section 5: "<= 1 async question to A04; fall back to documented interaction heuristics + flagged TODO tests"; prompt autonomy: L3 for a new dependency, a new pattern outside the design system, any deviation from `ux.spec` states or tokens).
- Owns the request protocol. Detecting a state mismatch is `a06-frontend--state-parity`; detecting a literal with no token is `a06-frontend--token-binding`.

## Procedure
1. Call `shared_open_questions` with `{task_id}` to see unresolved questions, then `shared_handover_read` with `{task_id}`. A question to A04 is already open when `handover.from_agent` is `A06`, `handover.to_agent` is `A04` and `handover.open_questions` is not empty: do not ask a second, extend the first (at most one open question). The same call shows whether an earlier handover already carries the answer in `decisions` or `constraints`; an answer in the record is not a question.
2. Classify the trigger with `read` of the spec section in question. Ambiguity or an undefined interaction is L2: continue. A new dependency, a new UI pattern outside the design system, or a deviation from spec states or tokens is L3: do not implement it and do not import the library (prompt example `l3-stop`).
3. Draft the deviation request for step 4's `shared_handover_write`. No schema exists in the repository and no file zone is assigned for it, so this skill's convention is one JSON string in the handover `completed` with the keys `screen_id`, `spec_ref`, `gap`, `proposal`, `heuristic_used`, `impact`, `reversible`. For L2, apply the documented interaction heuristic you named, keep the implementation behind it, and mark the affected tests with a flagged TODO that cites the question.
4. Call `shared_handover_write` with `{task_id, to_agent: "A04", goal, completed: [<request string>], open_questions: [<the single question>], decisions: [{decision: "<heuristic or stop>", rationale}], constraints: [<spec and token pins>], remaining: [<work waiting on the answer>], verification: {status: "unverified", confidence, checks: []}}`.
5. For L3 call `shared_escalate` with `{task_id, reason_code: "policy", summary, needs: ["human-approval: <dependency.request or deviation>"]}` and use the returned `result_patch` as the BLOCKED `task.result`; continue only with parts that do not depend on the gap.
6. A session cannot wait for the answer. On a re-assignment, call `shared_handover_read` again; if A04's newer handover answers, apply it and remove the flagged TODOs and the heuristic.

## Outputs
- One deviation request, one open question, a named heuristic with flagged tests (L2), or a BLOCKED result with `needs` (L3).

## Failure Handling
- The session must end with no answer: keep the heuristic, leave the TODOs flagged, and say so in `decisions`; never delete the flags.
- `shared_escalate` returns `INVALID_INPUT`: fix the named field once, otherwise put the blocker in the result text and report the tool failure.

## Handover
Fields filled: `completed` (request string), `open_questions` (exactly one), `decisions` (heuristic or stop), `blockers` (the L3 gap), `remaining`, `constraints`, `verification`. Written with `shared_handover_write` to A04; the BLOCKED result reaches A01 through `shared_escalate`.
