---
name: "a29-dapp-wallet--error-mapping"
description: "Use when a dapp.wallet task maps provider and RPC failures (EIP-1193 4xxx, JSON-RPC -32xxx, Solidity reverts) to user messages, retries and recovery actions."
disable-model-invocation: true
---

# Provider error mapping

## When to Use

- A `dapp.wallet` task writes or reviews the `catch` paths around `request`, `sendTransaction`, `writeContract` or `signTypedData`, or the error copy shown to users.
- Status states are `skill://a29-dapp-wallet--tx-lifecycle-ux`; pre-sign simulation failures use this mapping but are gated in `skill://a29-dapp-wallet--simulation-before-sign`. Code and copy are L2.

## Procedure

1. `grep` the sources for `catch`, `.code ===`, `error.message` and toast calls; list each handler and what it shows. Collect `contract.abi` (A26) for custom errors.
2. Call `dapp_provider_error_map` with `code` for each of 4001, 4100, 4200, 4900, 4901, 4902, -32002, -32005, -32601, -32602, -32603, -32000 and 3. Record `category`, `standard`, `message_class`, `retryable`, `suggested_action`, `show_error_toast`, `matched_by` per code (EIP-1193 4xxx, EIP-1474 and JSON-RPC 2.0 -32xxx, 3 for a revert; 4902 is MetaMask's code, not an EIP's).
3. `edit` each handler to apply the `dapp_provider_error_map` result as its rule: `message_class: info` (4001 `user_rejected`, -32002 pending request) shows no error toast and returns the UI to the confirm step; `warning` and `error` show a message. `retryable: true` allows one automatic retry with backoff, capped at 3 attempts, then the `suggested_action`; `retryable: false` never retries.
4. Message-only providers: call `dapp_provider_error_map` with `code: -32000` and each `message` the app sees (`insufficient funds`, `nonce too low`, `replacement transaction underpriced`, `intrinsic gas too low`, `already known`, `execution reverted`). `matched_by: "message"` is a substring heuristic used only for generic codes (-32000..-32099, -32602, -32603, 3) and unknown ones: a specific code (4001, 4900, -32005) always wins, `execution reverted` in a message always means a revert (a Solidity reason such as `insufficient funds` is contract text, never a node error), and `matched_by: "none"` becomes a generic message plus a logged code, with no raw provider text in the UI.
5. Reverts: call `dapp_provider_error_map` with `code: 3`, `message` and `revert_data`. `revert_kind` and `revert_reason` decode `Error(string)` (selector `0x08c379a0`) and `Panic(uint256)` (selector `0x4e487b71`, for example `0x11` overflow, `0x12` division by zero). A custom error stays `custom error 0x<selector>`: resolve the name from `contract.abi`, and if the ABI lacks the selector show the selector, never a guess. Reason text is attacker-controlled: render as text, never as HTML.
6. Cross-check with the lifecycle: 4001 must end in `rejected`, an `execution_reverted` after mining in `reverted`, `already_known` must track the existing hash, not resubmit. Run `dapp_tx_state_machine` on one fixture per category and require `valid: true`.
7. `edit` one error table module (code to category to copy key to action) so no handler keeps its own string matching; write the table to `dapp/wallet-flow/errors-<task_id>.md`. Provider messages are never logged together with addresses and signed payloads.
8. Call `shared_artifact_digest` on the patch and the table, then hand over.

## Outputs

- `code.patch` (error table, handlers) and `wallet.flow.spec` section (code to message class and action), citing `category`, `retryable`, `matched_by` per code.

## Failure Handling

- INVALID_INPUT (`message` over 512 chars, `revert_data` odd length): truncate or fix the named field, else BLOCKED needs=input.
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; do not hand-write a mapping and call it checked.
- Wallets word messages differently and codes outside the table map to `unknown`: defer to tool output and say which codes were unmapped.
- Never retry a non-idempotent submit on a vague error; check for the existing hash first.

## Handover

- `to_agent`: `A04` (writes copy for each message class). `goal`: copy for the categories in the table; `completed`: codes mapped; `artifacts`: table and patch with digests (producer `A29`); `open_questions`: unmapped codes, custom errors missing from the ABI; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/error-mapping.md; edit the source and run scripts/build_agents.py -->
