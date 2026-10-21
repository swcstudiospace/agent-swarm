---
name: a03-architect--capacity-model
description: Use when A13 sends a capacity.forecast and per-component load budgets must be derived, checked against NFR limits and handed to A11 for provisioning.
owner: a03-architect
tools: [shared_handover_read, arch_nfr_allocation, arch_nfr_budget, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- A13 delivers a `capacity.forecast` (load and growth numbers), or a forecast changes enough to move a budget.
- Output is `capacity.model` (03-agents/A03 section 3). It maps load to component budgets; `skill://a03-architect--nfr-allocation` maps NFRs to clauses. Do not mix the two.
- A03 never changes infrastructure: sizing goes to A11 as a proposal.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take `capacity.forecast`, the blueprint containers and the NFR bindings from `artifacts`. A `status` of `stale` or `malformed` is a `shared_escalate` with `reason_code` `stale-handover`.
2. Use `read` on the forecast; list per-container peak requests, data volume and growth, with units.
3. Use `write` to derive a budget per container for each NFR metric (latency, availability, error rate, throughput). Apply the headroom factor `infra.constraints` states; when it states none, mark the factor `assumed: true` and record it as an open question.
4. Call `arch_nfr_allocation` with `{nfrs: [{id, kind}], bindings: [{nfr_id, target_kind: component_budget, target_ref: <container>, limit, unit}]}`. A quantitative NFR counts only with a numeric `limit`; ids, and (nfr, kind, ref) triples, must be unique. Need `fully_allocated` true.
5. Call `arch_nfr_budget` with `{budgets: [{metric, limit, direction: max|min}], measurements: [{metric, value}]}` using the forecast values under the same metric names. `breaches` and `unmeasured` (which also make `within_budget` false) are the capacity risks.
6. Each breach needs a decision: raise the budget with a cost note, change the design, or ask A02 for a lower target. Use `write` to record it in the model; record material ones with `skill://a03-architect--adr-record`.
7. A breach you cannot resolve: call `shared_escalate` with `{task_id, reason_code: budget, summary, needs}`.
8. Call `shared_artifact_digest` with the capacity-model `path`, then `shared_handover_validate` (`draft` true) and `shared_handover_write`.

## Outputs
- `capacity.model`: per-container budgets, forecast inputs, headroom factor, breach list, proposals for A11.

## Failure Handling
- Forecast older than the design or in different units: escalate as `stale-handover`; do not convert silently.
- `unmeasured` metric: keep it open; never fill it with a guess.
- `INVALID_INPUT` names the field (duplicate metric, duplicate binding): fix it, retry once.

## Handover
Fill `goal`, `completed`, `decisions` (headroom and breach choices with rationale), `artifacts` (kind `capacity.model`, digest, `producer` A03), `constraints` (budgets later tasks must keep), `open_questions`, `blockers` (open breaches) and `verification` (`status`, `confidence`, `checks`). Write once to A11 (provisioning proposals), once to A13 (budgets to monitor) and once to A01: neither accepts `capacity.model` as a routed message type, so A01 creates the follow-up tasks.
