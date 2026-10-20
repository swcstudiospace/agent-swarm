---
name: "a28-chain-infra--indexer-backfill"
description: "Use when a chain.indexer task must backfill historical logs from a deploy block to head: window size, workers, duration and a freshness acceptance check."
disable-model-invocation: true
---

# Indexer backfill

## When to Use

- A `chain.indexer` task starts or repairs an event indexer over a block range and wants the `indexer.spec` with a backfill section.
- Planning is L2 on a testnet. A backfill that reads a mainnet-grade chain is read-only (L0) but the production indexer cut-over is L3 for A11.
- Handling an indexer that already runs and is behind is `skill://a28-chain-infra--node-incident-response`; the shape of the event tables is `skill://a28-chain-infra--event-decoding-schema`; rollbacks of recent blocks are `skill://a28-chain-infra--reorg-handling`.

## Procedure

1. Call `shared_handover_read` with `{task_id: <upstream task id>}` to get the `contract.deploy.plan` (A26: contract address, deploy block) and the `contract.abi` file. `from_block` is the earliest deploy block among the addresses, never 0 unless the plan says so. No deploy block is BLOCKED needs=input.
2. Call `chain_network_guard` with `environment`, `operation: "read"`, `target_chain_id`, `rpc_chain_id` when recorded, `mainnet_approved`, `approved_level: "L2"`. Continue only on `decision: allow`.
3. Measure `est_logs_per_block` from one sample window the task provides (matching logs divided by blocks) for the address and topic filter. Without a sample, BLOCKED needs=input; a guess makes the window wrong.
4. Set `to_block` to the head minus the confirmation depth chosen with `skill://a28-chain-infra--reorg-handling` (the live tail is handled by the follower, not by the backfill). Call `chain_backfill_plan` with `from_block`, `to_block` (inclusive), `max_block_range` and `max_logs_per_response` (the provider limits the task states), `est_logs_per_block`, `requests_per_chunk`, `workers`, `request_latency_ms`, `provider_rate_limit_rps`, `block_time_s`.
5. Decision rules: `catches_up` false is `never-catches-up` (blocker): raise `workers` to `workers_needed` or the window; `block-exceeds-response-cap` (blocker) means one block alone returns more logs than the cap, so add an address or topic filter or read receipts; `limited_by: rate-limit` with `workers` above `workers_needed` only produces 429s, so use `workers_needed`. `expected_logs_per_chunk` is planned to fill at most half the cap.
6. Cross-check the `chain_backfill_plan` output: `chunks` equals `ceil(blocks / chunk_size_blocks)`; `requests` equals `chunks * requests_per_chunk`; `blocks` equals `to_block - from_block + 1`. A mismatch is E-CONTRACT. When a window errors at run time, bisect it at most `bisect_depth` times (halving) before logging a single failing block.
7. Define acceptance with `chain_indexer_lag`: samples (oldest first, strictly increasing `observed_at`, `head_block`, `indexed_block`), `block_time_s` and `freshness_slo_s`. The backfill is accepted when `within_slo` is true and `trend` is `caught-up` or `closing`; `indexer-stalled` (blocker) or `lag-growing` means the plan is wrong, not the data.
8. `write` `chain/indexer/<task_id>.md` (range, window, workers, duration `duration_s`, `time_to_head_s`, idempotency rule: upsert by `(block_hash, log_index)`, checkpoint every chunk) and call `shared_artifact_digest` with it.

## Outputs

- `indexer.spec` (backfill plan, acceptance criteria and SLO, checkpoint and bisect rules), values cited from the tools.

## Failure Handling

- INVALID_INPUT (`to_block` before `from_block`, samples out of order, `indexed_block` above `head_block`): fix the named field once, else BLOCKED needs=input.
- PARTIAL_SUCCESS: `completed_fraction`, name what was not planned. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Provider range and result limits differ per vendor and plan: use the figures given in the task and say they are unverified when no probe output backs them.
- Starting a real backfill against a metered provider is not retried and needs the task owner's approval of the request count.

## Handover

- `to_agent`: `A13` (freshness SLO and alerts) and `A29` (the app reads the index); write to A13 and list A29 in `remaining`. `goal`: implement and monitor this spec; `artifacts`: spec with digest; `decisions`: window, workers, SLO; `open_questions`: missing samples; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/indexer-backfill.md; edit the source and run scripts/build_agents.py -->
