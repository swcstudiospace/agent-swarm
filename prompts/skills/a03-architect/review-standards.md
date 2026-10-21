---
name: a03-architect--review-standards
description: Use when a blueprint is accepted and A09 needs coding standards, dependency-direction rules and a coupling budget derived from it.
owner: a03-architect
tools: [shared_handover_read, shared_escalate, arch_layer_violations, arch_cycle_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use
- The blueprint is accepted (gate passed) and A09 will review code against it.
- Output is `review.standards`, the coding-standards input for A09 (agents/A03 section 3). It is the only A03 skill whose consumer is A09.
- It restates decisions already taken; new decisions go through `skill://a03-architect--adr-record`.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take the accepted blueprint, ADR set and contract digests from `artifacts`.
2. Use `read` to extract the rules code can break: layer order and module-to-layer assignments, allowed dependency directions, the fan-out budget, forbidden dependencies from the allowlist, contract conformance (pinned digests, N-1 compatibility).
3. For each rule write a statement with a rule id, the rationale (ADR id) and how a reviewer checks it.
4. Prove the layering rule: call `arch_layer_violations` with `{layer_order, assignments, edges}` where `layer_order` runs outermost first and `edges` hold one allowed edge (same or later layer) and one upward edge. `violation_count` must be 1 and `violations` must name the upward edge.
5. Prove the cycle rule: call `arch_cycle_check` with `{edges}` holding a two-module loop; `has_cycle` must be true.
6. Use `write` to save the standards file at the path the task names (none named: `docs/review-standards.md`, your convention; say so in `decisions`).
7. Call `shared_artifact_digest` with that `path`.
8. Call `shared_handover_validate` (`draft` true) and `shared_handover_write` with `to_agent` A09, then once more with `to_agent` A01: the routing table does not let A09 accept `review.standards` as a message type, so A01 attaches the file to the review task.

## Outputs
- `review.standards`: numbered rules, rationale, check method, sample edge fixtures.

## Failure Handling
- A rule whose sample check does not behave as stated: fix the rule text or the assignments, not the tool input.
- No accepted blueprint in the handover: `shared_escalate` with `reason_code` `stale-handover` and stop.
- Standards that contradict an ADR: stop, resolve through the ADR first.

## Handover
Fill `goal` (review code against these rules), `completed`, `decisions` (rule sources), `artifacts` (standards file, digest, `producer` A03), `constraints` (the rules A09 must apply verbatim), `open_questions` and `verification` (`status`, `confidence`, `checks`). `to_agent` is A09.
