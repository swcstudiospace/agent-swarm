---
name: "a27-contract-audit--reentrancy-review"
description: "Use when external calls, token hooks or callbacks need a checks-effects-interactions review including cross-function and read-only reentrancy."
disable-model-invocation: true
---

## When to Use

- In-scope code with a value transfer, an `.call`, a token transfer of an arbitrary token, or a callback (`onERC721Received`, ERC-777 hooks, ERC-1155 receivers, flash-loan callbacks, `receive`/`fallback`).
- Writing the exploit test is `skill://a27-contract-audit--finding-writeup-poc`; economic viability is `skill://a27-contract-audit--economic-attack-modeling`.
- Review is L2. The result is a model over a trace you build from the code; the tool does not parse Solidity.

## Procedure

1. `grep -n` the interaction points: `\.call\{`, `\.transfer\(`, `\.send\(`, `safeTransfer`, `safeTransferFrom`, `_safeMint`, `onERC.*Received`, `flashLoan`, `delegatecall`. For each function, `read` it and list its steps in source order, including those in internal calls and modifiers.
2. Encode every function as `{name, non_reentrant, steps}` where `steps` is an ordered list of `{kind, target, trusted}`: `check` (a read the decision depends on, for example `balances[msg.sender]`), `effect` (a state write), `interaction` (an external call). `target` is the state variable or callee. Mark `trusted: true` only for a call to an immutable address of an in-scope audited contract; a transfer of an arbitrary ERC-20 or any user-supplied address is untrusted. `non_reentrant` is true only when a guard modifier is actually applied (`nonReentrant`, or the transient-storage guard where the compiler and chain support EIP-1153); the tool assumes all guarded functions share one lock, as OpenZeppelin's guard does. At most 12 functions and 16 steps each per call: split the contract into batches by shared state, never drop functions.
3. Call `audit_reentrancy_order` with `functions`. Read the findings: `REENT-<fn>-<var>` high when a variable checked before the call is written after it (stale check), medium when only written after, low when a guard is present; `REENT-X-<fn>-<var>` high is cross-function reentrancy: another function reads or writes the same variable during the call and is not shielded. A lock only rejects a nested call while it is held, so it shields a second function only when the calling function is guarded too: an unguarded caller leaves even a guarded second function enterable (the finding says so).
4. Cross-check `audit_reentrancy_order` counts: `functions_checked` equals the functions you sent; `cei_compliant` plus the functions named in `REENT-` findings equals `functions_checked`; `untrusted_interactions` equals your own count of untrusted `interaction` steps. Disagreement means the encoding is wrong, fix it before citing.
5. Cross-check with the analyzer: for each Slither `reentrancy-eth` or `reentrancy-no-eth` result (`skill://a27-contract-audit--static-analysis-triage`) the function must appear in the tool output, or you write why the detector is a false positive. Call `audit_classify_vulnerability` with `signal` `reentrant-external-call` for confirmed locations (default high).
6. Read-only reentrancy is not modeled by the tool: review each `view` function that other protocols read for price or share value (virtual price, `totalAssets`, `getReserves`-like values) while a state update is in flight. Record these as manual findings with the call path, labeled "manual, not tool-checked".
7. Score confirmed findings with `audit_score_severity` (`attacker_privilege` `permissionless` unless the callee must be a role, `preconditions`, `loss_usd` against `tvl_usd`, `irrecoverable`). A guard that you consider insufficient does not change the tool severity: report it as a note.
8. Recommend, never edit: apply checks-effects-interactions, a guard on every function that touches the shared state (cross-function needs the same lock), pull over push payments. `write` `audit/reentrancy-review.md` with the encoded trace table, and digest it with `shared_artifact_digest`.

## Outputs

- Reentrancy part of `audit.findings`, with the trace table as evidence so a reviewer can re-run the tool on the same input.

## Failure Handling

- INVALID_INPUT (duplicate function names, more than 12 functions or 16 steps): split or rename; never truncate a trace.
- A trace you cannot reconstruct from code (assembly, upgradeable hooks): mark the function "not modeled" and open a question.
- The tool does not know modifiers' bodies: if a custom modifier hides an external call, expand it into steps first.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and report only manual findings.

## Handover

- `to_agent`: `A26` (owner of the patch). `artifacts`: review file with digest. `open_questions`: functions not modeled, read-only paths. `verification`: verified when the step 4 counts agree.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a27-contract-audit/reentrancy-review.md; edit the source and run scripts/build_agents.py -->
