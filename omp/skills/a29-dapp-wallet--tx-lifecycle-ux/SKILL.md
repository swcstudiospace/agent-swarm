---
name: "a29-dapp-wallet--tx-lifecycle-ux"
description: "Use when a dapp.tx_lifecycle task builds the transaction status flow (confirm, pending, replaced, dropped, reorged) and each state history must be validated."
disable-model-invocation: true
---

# Transaction lifecycle UX

## When to Use

- A `dapp.tx_lifecycle` task defines how the UI tracks a transaction from draft to finalized, including speed-up, cancel, replacement and reorg handling.
- The pre-sign gate itself (estimates, gas headroom) is `skill://a29-dapp-wallet--simulation-before-sign`; error copy is `skill://a29-dapp-wallet--error-mapping`. Changing the confirm step is a signing-flow change (L3); the code is L2.

## Procedure

1. `read` the task, `ux.spec` and the existing status component. List the app's states and map each to the tool vocabulary: `draft`, `simulating`, `simulated`, `simulation_failed`, `awaiting_confirmation`, `user_confirmed`, `awaiting_signature`, `submitted`, `confirmed`, `finalized`, `reverted`, `rejected`, `replaced`, `dropped`, `reorged`. An app state with no mapping is BLOCKED needs=input.
2. Write fixture histories as `events` (`state`, `at` ISO-8601 with offset, `tx_hash`, `replaced_by`, `confirmations`): happy path, user rejection, simulation failure, revert, replacement, drop, reorg then re-confirmation, and a stuck pending one.
3. Call `dapp_tx_state_machine` per fixture with `events`, `now`, `stuck_after_seconds` (the tool default is 600) and `required_confirmations` (from the task per chain, never invented). Every positive fixture must give `verdict: pass` and `valid: true`.
4. Negative fixtures through `dapp_tx_state_machine` must trip the exact finding. Reaching `awaiting_signature` without `simulated` and `user_confirmed` gives blocker `TX-POLICY-NO-SIMULATION-CONFIRMATION`; a jump such as `submitted` to `finalized` gives `TX-TRANSITION-INVALID`; a `replaced` event without a 32-byte `replaced_by` gives `TX-REPLACEMENT-MISSING`; `finalized` below `required_confirmations` gives `TX-FINALIZED-EARLY`. A negative fixture that passes means the policy is not enforced: fix the code, not the fixture.
5. Read `dapp_tx_state_machine` stuck handling: when `final_state` is `submitted` or `reorged` and `pending_seconds` is at least `stuck_after_seconds`, expect `TX-STUCK`. The UI then offers speed-up and cancel with the same nonce and both `maxFeePerGas` and `maxPriorityFeePerGas` raised at least 10% (the tool's replacement rule), and keeps tracking the original hash until the replacement is `replaced_by`.
6. Cross-check the UI (`grep` its button logic) against the tool: for each fixture the buttons enabled in the final UI state must equal `allowed_next`, and `terminal` must match whether polling stops. A disagreement is E-CONTRACT.
7. Call `dapp_provider_error_map` with `code: -32000` and `message: "replacement transaction underpriced"`; the `suggested_action` must be the speed-up fee rule above, and `4001` must land in `rejected` with no error toast.
8. Call `shared_check_plan` with `scan: true` to name the lint, typecheck and test commands for A08 (do not run them). Implement with `edit`, then `shared_artifact_digest` on the patch and the fixture file.

## Outputs

- `wallet.flow.spec` (state table, transitions, fixtures and results) and `code.patch`, citing `verdict`, `final_state`, `allowed_next` and counts per fixture.

## Failure Handling

- INVALID_INPUT on `events` (timestamp without offset, unknown state): fix the fixture. PARTIAL_SUCCESS: use `completed_fraction`, list unassessed fixtures.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; the flow is then unverified, say so.
- Confirmation depth and finality are chain specific: defer to the task and to `required_confirmations`; do not assume a number.
- Never replay a submit to test it; broadcasting is a human L4 step.

## Handover

- `to_agent`: `A04` (designs the state copy and visuals). `goal`: design the states listed; `completed`: fixture results; `decisions`: `required_confirmations`, `stuck_after_seconds`; `artifacts`: spec and patch with digests (producer `A29`); `open_questions`: unmapped states; `verification`: verified only if every fixture matched.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/tx-lifecycle-ux.md; edit the source and run scripts/build_agents.py -->
