---
name: "a26-smart-contract--pausable-circuit-breaker"
description: "Use when a contract holding user funds needs an emergency stop (Pausable) with asymmetric roles, events, tests and a human-only runbook to trigger it."
disable-model-invocation: true
---

## When to Use

- The blueprint or an `audit.findings` item asks for an emergency stop, rate limit or guardian role. Code is L2. Pausing or unpausing a live contract, or calling any rescue function that moves funds, is fund-moving or live-state control: L4 for a human with the signer, never this agent.
- Not for the general role model (`skill://a26-smart-contract--access-control-roles`) or incident response (A13). The question "should this contract be pausable at all" is answered here by step 1.

## Procedure

1. `read` the blueprint and decide the scope per function, written as a table `function -> paused behaviour`. Deposits, minting, swaps, new loans: blocked when paused (`whenNotPaused`). Exits (withdraw, repay, redeem): stay open unless the blueprint proves a paused exit is the only way to stop an active drain; a pause that traps user funds is a centralization risk that A27 will flag. Governance and the pause functions themselves are never paused. A contract with no user funds and no value at risk is not made pausable.
2. `read` the OpenZeppelin version (`grep -n openzeppelin remappings.txt`) and choose the mechanism: OpenZeppelin 5 `Pausable` (`utils/Pausable.sol`: `paused()`, `_pause()`, `_unpause()`, events `Paused(address)` and `Unpaused(address)`, errors `EnforcedPause()` and `ExpectedPause()`), optionally an expiry (pause lapses after N days so a lost key cannot freeze forever) and per-function caps. Roles are asymmetric: `PAUSER_ROLE` held by a guardian multisig with a low threshold for speed, `unpause` held by the admin multisig or timelock with the higher threshold. The holders come from the `custody.policy.set` (A30); do not invent addresses.
3. `edit` the contract. `pause()` and `unpause()` carry their role modifiers, are `external`, and emit via `_pause()`/`_unpause()`; no other state change hides inside them. Upgradeable contracts: `PausableUpgradeable` and a `__Pausable_init()` call in `initialize`, with its storage left to the library (the layout stays untouched: `skill://a26-smart-contract--storage-layout-migration`).
4. Call `sol_access_matrix` with `source` and `public_by_design` for the user-facing functions. `roles` must show `pause` and `unpause` under different roles; `unprotected` must be 0 and `verdict` `pass`; `ACCESS-UNPROTECTED-ADMIN` on `pause`/`unpause` is a blocker for this task. Cross-check against step 1: every function in the table marked "blocked when paused" has its modifier (read with `grep -n "whenNotPaused"`).
5. Call `sol_event_coverage` with `source` and `min_coverage_pct` 100 over the pause-related functions; `EVENT-MISSING` on `pause`/`unpause` blocks (monitors from A13/A28 alert on the `Paused(address)` topic). Call `sol_selector_check` with `events` `["Paused(address)", "Unpaused(address)"]` and `functions` `["pause()", "unpause()", "paused()"]`: `verdict` `clean`, and the `topics` list is what A28 puts into its decoding schema; the `Paused(address)` `topic0` starts `0x62e78cea`.
6. Write the tests first (`skill://a26-smart-contract--foundry-test-first`): pause by the guardian succeeds, by a stranger reverts; `unpause` by the guardian reverts; each blocked function reverts with `EnforcedPause` while paused; each exit still works while paused; the expiry works with `vm.warp`; state survives a pause/unpause cycle. Fuzz the paused flag against the user-facing functions with an invariant "no user balance changes while paused except through an exit".
7. Call `sol_test_gate` with the lcov `files`, `min_line_pct` 95, `min_branch_pct` 90, `tests_failed` 0, `fuzz_tests` at least 1, `min_fuzz_runs` 256, `invariant_tests` at least 1 and `require_invariants` true; blockers or majors stop the handover.
8. `write` `docs/runbooks/<contract>-pause.md` for the human: who may call `pause()`, the exact `cast send` template with a hardware signer placeholder and no key, how to confirm `paused()` is true, what to do next (A13 incident, A12 release freeze). Mark it L4. Call `shared_artifact_digest` for the patch and the runbook.

## Outputs

- `code.patch` (pausable code, tests) and a pause runbook for the `contract.deploy.plan` owner section; the ABI gains `pause`, `unpause`, `paused`, `Paused`, `Unpaused`.

## Failure Handling

- INVALID_INPUT: a `min_coverage_pct` outside 0 to 100, a non-canonical signature in `events`: fix or BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The access tool recognises an imported modifier by its name and a modifier defined in the source by its body (it must check the caller); a custom `onlyGuardian` it does not classify is verified by the negative test, and noted.
- Triggering a pause on a live network is not retried and not done here: BLOCKED needs=human-approval (L4).

## Handover

- `to_agent`: `A09` with `A28` (event topics) and `A13` (alerting) named in `open_questions`. `decisions`: what is pausable and what is not, role split, expiry; `artifacts`: patch and runbook with digests.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a26-smart-contract/pausable-circuit-breaker.md; edit the source and run scripts/build_agents.py -->
