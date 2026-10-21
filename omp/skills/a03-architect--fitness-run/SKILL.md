---
name: "a03-architect--fitness-run"
description: "Use when a design.fitness task or a CI violation on main requires re-auditing layering, cycles, coupling and NFR budgets with a bounded fix loop."
disable-model-invocation: true
---

## When to Use
- `design.fitness`, or a gate finding on main from a dependency-direction, cycle or coupling check (03-agents/A03 section 4.4: a CI violation on main blocks further downstream CLAIMED tasks).
- The blueprint already exists. Authoring a blueprint is `skill://a03-architect--blueprint`.

## Procedure
1. Call `shared_handover_read` with `{task_id}`, then `shared_open_findings` with `{task_id}` to list the unexpired failing gate findings (`count`, `blocking`).
2. Use `read` on the blueprint for `layer_order` (outermost layer first; a module may depend on its own or a later layer), `assignments`, containers, relations and the fan-out budget.
3. Collect actual module edges from source with `grep` (import statements) and normalise them to `{from, to}` pairs.
4. Call `arch_layer_violations` with `{layer_order, assignments, edges}`. Note `violations` and `unassigned`.
5. Call `arch_cycle_check` with `{edges}`. Note `cyclic_components`.
6. Call `arch_c4_fanout` with the blueprint `{containers, relations, fan_out_budget}`. Note `over_budget`.
7. Where CI or telemetry supplied numbers, call `arch_nfr_budget` with `{budgets, measurements}`; `breaches` and `unmeasured` count as findings.
8. Map each finding to `major` (blocks) or `minor` and call `shared_verdict` with `{severities}`.
9. On `fail`, fix loop (maximum 2): change only owned artifacts (blueprint, layer assignments). A violation in product code goes to A05 as a `blockers` entry; you never edit product code. Repeat steps 4 to 8.
10. After the second failed loop, de-scope the rule through an ADR (`skill://a03-architect--adr-record`) or call `shared_escalate` with `reason_code` `blocked`.
11. Call `shared_handover_validate` (`draft` true) and `shared_handover_write`.

## Outputs
- Violation report: counts and names per check, verdict, loops used.

## Failure Handling
- `unassigned` modules: assign a layer; never ignore them.
- Tool outage: retry twice, then `skipped:tool-missing`; a skipped check is not a pass.
- Never waive a gate or lower a budget to turn the verdict green.

## Handover
Fill `goal`, `completed` (checks run), `blockers` (violations needing A05 or A07), `decisions` (de-scope or fix rationale), `open_questions`, `constraints` and `verification` (`status`, `confidence`, `checks` per tool). Write once to A05 for code-side fixes and once to A01 for the verdict.

<!-- generated from prompts/skills/a03-architect/fitness-run.md; edit the source and run scripts/build_agents.py -->
