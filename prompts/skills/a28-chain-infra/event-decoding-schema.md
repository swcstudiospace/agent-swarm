---
name: a28-chain-infra--event-decoding-schema
description: Use when a chain.indexer task turns a contract.abi into event filters and a log decoding schema: topic0, indexed layout, column types, unknown-event handling.
owner: a28-chain-infra
tools: [chain_backfill_plan, shared_handover_read, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Event decoding schema

## When to Use

- A `chain.indexer` task has a `contract.abi` (produced by A26) and needs the filters and table design that decode its logs, as the `schema` part of the `indexer.spec`.
- Design only: L2 and read-only. Deploying or changing the contract is A26's and mainnet is a human L4 step.
- Window sizing and workers are `skill://a28-chain-infra--indexer-backfill`; rollback of removed logs is `skill://a28-chain-infra--reorg-handling`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` and take the `contract.abi` artifact path and its recorded `digest`. `read` the ABI JSON and list every entry of `type: "event"` with `name`, `inputs` (`name`, `type`, `indexed`, tuple components) and `anonymous`. Call `shared_artifact_digest` with the ABI path: it must equal the recorded digest, else E-CONTRACT (the ABI changed after hand-off).
2. Build the canonical signature per event: `Name(type1,type2,...)` with tuples expanded as `(t1,t2)` and no argument names or spaces, `uint` written `uint256`. Get topic0 (the keccak-256 of that string) with `bash` from `cast sig-event "<signature>"` (Foundry). Without `cast`, record `skipped:tool-missing` and take topic0 from the compiler output; never hand-compute or guess a hash. Sanity anchor: `Transfer(address,address,uint256)` is `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`.
3. From the ABI you `read`, fix the log layout per event: `topics[0]` is topic0 (absent for `anonymous` events, which may index up to 4 arguments; others at most 3); `topics[1..3]` hold the indexed arguments in declaration order, 32 bytes each; `data` is the ABI encoding of the non-indexed arguments. An indexed `string`, `bytes`, array or struct is stored as its keccak hash: the value cannot be decoded, so keep a hash column only.
4. Handle collisions (`grep` your topic0 list for duplicates): ERC-20 and ERC-721 `Transfer` share topic0 but have 3 topics (value in `data`) versus 4 (indexed `tokenId`). Decode by topic count and, when needed, the emitting address, never by topic0 alone.
5. Design the table (draft it with `write`): unique key `(chain_id, block_hash, log_index)` (`log_index` is per block, so `tx_hash` alone is not unique), plus `block_number`, `tx_hash`, `address`, `removed` (set when the log is rolled back), `abi_digest`, and one typed column per argument: `uint256` as `NUMERIC(78,0)` (2^256 has 78 digits), `address` as a 20-byte value stored lowercase, `bool` as boolean, hashes as 32 bytes. Idempotent upsert on the key.
6. Plan the unknown-event path (add it to the `write` draft): a log whose topic0 or topic count matches no entry goes to a quarantine table with its raw topics and data, and increments a counter; never drop it. For proxies, decode with the implementation ABI but filter by the proxy address; a new ABI version gets a new `abi_digest` and applies from the upgrade block.
7. Check that the filter is viable with `chain_backfill_plan`: `est_logs_per_block` per filter (address plus topic0 list), the provider's `max_block_range` and `max_logs_per_response`, and the other fields the task gives. `block-exceeds-response-cap` (blocker) means one block returns more logs than the cap: narrow by address or topic, or split filters per event. Record `chunk_size_blocks` and `expected_logs_per_chunk` in the spec.
8. Validate by decoding one recorded real log per event through the schema and comparing each field with the task's explorer value. `write` `chain/indexer/<task_id>.schema.md` with signatures, topic0 values, columns, quarantine rule and test evidence, then call `shared_artifact_digest` with it.

## Outputs

- `indexer.spec` schema section: event table, topic0 per event, key, column types, quarantine and versioning rules.

## Failure Handling

- INVALID_INPUT from `chain_backfill_plan` (`to_block` before `from_block`): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`; name events not covered. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- ABI missing, digest mismatch or an event without a usable signature: BLOCKED needs=input to A26 via the handover; never reconstruct an ABI from bytecode here.
- Provider topic-filter limits vary: defer to the task's stated limits.

## Handover

- `to_agent`: `A29` (the application reads these tables) and `A13` (quarantine counters feed alerts); write to A29, list A13 in `remaining`. `goal`: implement this schema; `artifacts`: schema with digest and the ABI digest; `decisions`: key, types, quarantine rule; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.
