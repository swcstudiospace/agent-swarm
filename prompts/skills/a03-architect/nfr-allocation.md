---
name: a03-architect--nfr-allocation
description: Use when a requirements.spec arrives or an NFR changes and every NFR must be bound to a measurable contract clause or component budget.
owner: a03-architect
tools: [shared_handover_read, arch_nfr_allocation, arch_nfr_budget, shared_escalate, shared_handover_validate, shared_handover_write]
---

## When to Use
- A02 delivers `requirements.spec` or `acceptance.criteria`, or an NFR in them changes after PLANNED.
- Run before `skill://a03-architect--blueprint` and `skill://a03-architect--contract-publish`: they consume the bindings this skill produces. This skill only allocates; it does not design.
- Spec rule (03-agents/A03 section 4.3): every NFR is bound to a measurable contract clause or component budget; an unallocatable NFR is escalated to A02, never silently dropped. Coverage target is 100 %.

## Procedure
1. Call `shared_handover_read` with `{task_id}`. Take `requirements.spec` and `acceptance.criteria` from `artifacts`. A `status` other than `ok` means stop and call `shared_escalate` (`reason_code` `stale-handover`).
2. Use `read` on both artifacts and list every NFR with a unique id. Mark each `quantitative` (has or needs a number: latency, availability, rate limit) or `qualitative` (for example the auth scheme).
3. Use `write` to record `assumed: true` on any NFR with no number after applying the documented policy default (for example p95 < 300 ms on a public API). A02 is told through the handover `open_questions`.
4. Use `write` to bind each NFR to one contract clause (an OpenAPI/AsyncAPI pointer) or one component budget, putting the number and unit next to the reference.
5. Call `arch_nfr_allocation` with `{nfrs: [{id, kind}], bindings: [{nfr_id, target_kind: clause|component_budget, target_ref, limit, unit}]}`. A quantitative NFR is allocated only when a binding carries a numeric `limit`; a binding for an id not in `nfrs` lands in `unknown_nfr_ids` and blocks `fully_allocated`. Need `fully_allocated` true.
6. For `unallocated`, `unmeasurable` or `unknown_nfr_ids`, rebind once and call `arch_nfr_allocation` again. Anything still open goes to `shared_escalate` with `{task_id, reason_code: ambiguous-input, summary, needs}`, `needs` naming each NFR id for A02.
7. Where measurements already exist (capacity.forecast or CI numbers in the handover), call `arch_nfr_budget` with `{budgets: [{metric, limit, direction: max|min}], measurements: [{metric, value}]}`. A breach, or an `unmeasured` metric (it also makes `within_budget` false), becomes a `blockers` entry.
8. Call `shared_handover_validate` with `draft` true, then `shared_handover_write` (see Handover).

## Outputs
- Binding table: NFR id, kind, clause or component budget, limit, unit, `assumed` flag.
- `coverage_pct` and the lists `unallocated`, `unmeasurable`, `unknown_nfr_ids` from the last `arch_nfr_allocation` call.
- The A02 escalation, when one was needed.

## Failure Handling
- `INVALID_INPUT` from `arch_nfr_allocation` names the field (duplicate id, duplicate binding, blank `target_ref`): fix it once, then escalate with `reason_code` `blocked`.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry at most twice; never report coverage you did not obtain.
- `PARTIAL_SUCCESS`: treat as not allocated.
- Never invent an NFR or a number to reach 100 %.

## Handover
Fill `goal` (allocate NFRs for the contract and blueprint tasks), `completed` (bound ids), `decisions` (each assumed default, rationale), `artifacts` (binding table file, digest, `producer` A03), `open_questions` (A02 queries), `blockers` (breaches), `constraints` (limits later tasks must keep) and `verification` (`status` partial while any NFR is open, `confidence`, `checks`). Write to A01 so the planner releases the design tasks, and to A02 when `open_questions` hold its queries.
