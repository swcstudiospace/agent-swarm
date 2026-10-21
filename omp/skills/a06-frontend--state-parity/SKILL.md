---
name: "a06-frontend--state-parity"
description: "Use when a screen or component is complete; prove its implemented states equal ux.spec states exactly and close every missing or extra state."
disable-model-invocation: true
---

## When to Use
- A screen or component is code-complete and before its a11y and review gates (A06 spec section 4 rule 2; prompt decision_logic 2: "implemented states must equal `ux.spec.states` exactly; any extra or missing state requires A04 sign-off").
- Detection and fixing only. Asking A04 for a changed spec is `a06-frontend--design-deviation`; delivering stories to A08 is `a06-frontend--component-catalog`.

## Procedure
1. Take the per-screen spec states from the bound-inputs note (`a06-frontend--bind-inputs`). After a resume call `shared_handover_read` with `{task_id}` and read `handover.constraints`; with `status` other than `ok`, `read` the `ux.spec` input itself. Confirm every state name there before you rely on it.
2. Use `grep` in the screen source for the state machine or the render branches (an enum, reducer cases, `status ===` branches) and list the implemented state names per screen id. Keep dotted sub-states such as `form.error` as written; they count as separate states.
3. Call `fe_state_parity_check` with `{screens: [{id, spec_states, implemented_states}]}`. Read `missing` and `extra`, keyed by screen id; `parity_ok` must be true.
4. For each missing state, use `edit` to implement it with its own component test. For each extra state, delete it unless the product needs it; names are matched exactly, so a casing or spelling difference shows up as one missing plus one extra: rename to the spec name.
5. A state you cannot match (the spec is wrong, or an extra state is needed): do not ship it silently. Call `shared_open_questions` with `{task_id}` to see whether a question to A04 is already open, then open `a06-frontend--design-deviation` with the screen id and the state, and mark the screen not ready.
6. Call `fe_state_parity_check` again, then write the `states_implemented` array of the output JSON from the passing check.

## Outputs
- The parity table (screen id, spec states, implemented states), `states_implemented` for the final JSON block, and the open deviations.

## Failure Handling
- `INVALID_INPUT` field `screens`: duplicate screen id, a repeated or blank state, or no spec states; fix the list, not the data it came from.
- Spec lists no states for a screen: that is a spec gap, not parity; BLOCKED `needs=input` for A04's spec.

## Handover
Fields filled: `verification.checks` (`state parity ok` per screen), `decisions` (each renamed or removed state with its rationale), `open_questions` (states awaiting A04). Written with `shared_handover_write` to A09 with the closing handover (`skill://swarm-handover-emit`).

<!-- generated from prompts/skills/a06-frontend/state-parity.md; edit the source and run scripts/build_agents.py -->
