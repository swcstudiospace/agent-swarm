---
name: a05-backend--contract-deviation-request
description: Use when an A03 contract cannot be met or is ambiguous; prove the gap, ask A03 one question, send a change request, go provisional after 4 h.
owner: a05-backend
tools: [shared_handover_read, shared_open_questions, be_method_status_check, be_route_norm, shared_handover_write]
---

## When to Use
- The pinned contract demands something the implementation cannot do (for example a status or method outside its own allowlists) or leaves a behaviour undefined (A05 spec sections 4 rule 1 and 5).
- Not for finding gaps (`a05-backend--contract-gap-baseline`). A contract change is L3: never silently diverge and never add a private route.

## Procedure
1. Call `shared_handover_read` with `{task_id}`. A question to A03 is already outstanding when `handover.from_agent` is `A05`, `handover.to_agent` is `A03` and `handover.open_questions` is not empty. Then do not ask a second one: at most 1 clarifying question per task. Call `shared_open_questions` with `{task_id}` to see upstream needs that are still unresolved.
2. Prove the gap. Call `be_method_status_check` with `{method, status, allowed_methods, allowed_statuses}` taken from the contract path; `verdict: FAIL` with `method_allowed` or `status_allowed` false is evidence. Call `be_route_norm` on the contract path and the source path and compare `normalized`, to show the mismatch is not only parameter naming.
3. Draft the request text that step 4 sends through `shared_handover_write`. No `contract.change.request` schema exists in the repo and no ACP route carries it (A05 does not produce that type in agents.json), so this skill's convention is one JSON string in `completed` with the keys `contract` (pin `Name@x.y.z`), `path`, `method`, `observed`, `required_by_contract`, `proposal`, `breaking` (yes or no), `impact`.
4. Call `shared_handover_write` with `{task_id, to_agent: "A03", goal, completed: [<request string>], open_questions: [<the single question>], blockers: [<the gap>], decisions: [{decision: "stop implementing the deviating part", rationale}], constraints: [<pins>], verification: {status: "unverified", confidence, checks: [<step 2 evidence>]}}`.
5. Keep working on parts of the task that do not depend on the gap. Do not code the deviating behaviour. A session cannot wait 4 h: if nothing else is doable, end BLOCKED needs=input naming the pin and the question.
6. On resume (A01 re-assigns the task), call `shared_handover_read` with `{task_id}`. If the newest handover is still the A05-to-A03 one and `bash` `date +%s` minus `handover.created_wall` is at least 14400 (4 h), implement against the contract's `provisional` annotation and put `provisional` in those test names; record it in `decisions`.

## Outputs
- One change request string, one question, the step 2 evidence, and after 4 h a provisional-implementation decision.

## Failure Handling
- No `provisional` annotation in the contract after 4 h (A03's own spec does not define one; only A05's does): stay BLOCKED needs=input; do not invent behaviour.
- A newer handover from A03 answers: apply the answer and drop the provisional marks.
- INVALID_INPUT from a tool: correct the named field once, else report the check as unrun.

## Handover
Fields filled: `completed` (request string), `open_questions` (at most 1), `blockers` (the gap), `decisions` (what stopped, what is provisional), `constraints` (pins), `verification`. Written with `shared_handover_write` to A03; follow `skill://swarm-handover-emit` for validation.
