---
name: "a27-contract-audit--invariant-design"
description: "Use when an audit.invariants task must turn contract state and functions into an invariant.suite whose coverage of mutable state and funds safety is measured."
disable-model-invocation: true
---

## When to Use

- An `audit.invariants` task, or a vault, lending or AMM contract holding funds that has no invariant suite yet.
- Designing and measuring the spec. Whether the campaign ran long enough is `skill://a27-contract-audit--fuzz-campaign-review`; turning a broken invariant into a finding is `skill://a27-contract-audit--finding-writeup-poc`.
- Writing the suite is L2. You write only under `audit/invariants/`, never in `src/`.

## Procedure

1. Enumerate storage with `bash`: `forge inspect <Contract> storage-layout --json` (authoritative names and slots, includes inherited variables). Without forge, use `grep -n` on declarations and record the gap. Immutable and constant values are not state.
2. For each external or public function, `grep` the assignments to state it writes (including through internal calls and modifiers). Build `state_variables` (unique names) and `functions` = `[{name, writes}]`, where every `writes` entry is in `state_variables` (an unknown name is INVALID_INPUT naming the function).
3. Draft invariants with `write` (table in `audit/invariants/spec.md`) with ids `INV-1...`: `solvency` (assets held >= claims), `conservation` (sum of balances = totalSupply; assets in = assets out + held), `monotonic` (nonce, index, epoch never decrease), `bound` (fee <= MAX_FEE, ratio within limits), `access` (privileged state changes only by the role), `ordering` (no withdrawal before unlock). Each lists the `variables` and `functions` it constrains.
4. Call `audit_invariant_coverage` with `state_variables`, `functions`, `invariants` = `[{id, kind, variables, functions}]` and `holds_funds`. Decide: `uncovered_variables` non-empty (INV-VAR, medium) or `uncovered_functions` non-empty (INV-FN, low, medium when it writes an uncovered variable) means add invariants; `INV-SOLVENCY` (high) means `holds_funds` is true and no `conservation` or `solvency` invariant names a state variable (an invariant with an empty `variables` list does not count): the suite is not deliverable.
5. `write` the suite to `audit/invariants/<Contract>.invariants.t.sol`: a handler contract with bounded inputs (`bound(x, lo, hi)`), several actors, ghost variables for sums, and `invariant_<name>()` functions with `targetContract(handler)`. Reentrant and fee-on-transfer token mocks belong in the handler set.
6. Run with `bash` `forge test --match-path "audit/invariants/*"` on the clean `scope_commit`. A failing invariant is either a wrong spec (fix the invariant, with the reason) or a real break (keep the shrunk call sequence as evidence and go to the PoC skill). Never weaken an invariant to make it pass.
7. Call `audit_invariant_coverage` again on the final spec. Cross-check: `covered_variables` + `uncovered_variables` length = `mutable_variables`, and `mutable_variables` equals the variables that have at least one writer in your own `grep`; disagreement is E-CONTRACT.
8. Call `shared_verdict` with `severities` = the remaining coverage finding severities mapped to the gate scale (info to `info`, low and medium to `minor`, high to `major`, critical to `critical`); a `fail` (INV-SOLVENCY) means the suite may not be reported as complete. `shared_artifact_digest` the suite file and the coverage result file.

## Outputs

- `invariant.suite`: Foundry invariant tests, the invariant table (id, kind, variables, functions) and the coverage result (`variable_coverage`, `function_coverage`, uncovered lists).
- Any invariant that breaks on the clean commit is evidence for `audit.findings`.

## Failure Handling

- INVALID_INPUT (non-unique names, unknown variable or function): fix the named field in your table, never delete the reference silently.
- DEPENDENCY_UNAVAILABLE for `forge`: retry once, then `skipped:tool-missing`; the suite is delivered as source only, marked not executed.
- PARTIAL_SUCCESS: name the contracts or inherited storage you could not enumerate.
- Coverage is structural (names), not proof the property is strong: say so; strength is judged in the fuzz review.

## Handover

- `to_agent`: `A08` (consumer of `invariant.suite`). `goal`: execute and maintain the suite. `artifacts`: suite file and coverage result with digests. `open_questions`: uncovered items you accepted as out of scope (needs a human reason). `verification`: partial when forge did not run.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/invariant-design.md; edit the source and run scripts/build_agents.py -->
