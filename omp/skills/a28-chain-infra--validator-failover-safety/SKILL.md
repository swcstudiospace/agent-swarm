---
name: "a28-chain-infra--validator-failover-safety"
description: "Use when a chain.node task reviews validator redundancy, standby hosts or a client migration and must prove no key can be slashed by a double signature."
disable-model-invocation: true
---

# Validator failover safety

## When to Use

- A `chain.node` task covers validator hosts (active and standby), a host migration, or a consensus or validator client swap.
- The review is L2. Every validator key operation (import, move, activate, exit) is L4: BLOCKED needs=human-approval, a human runs it. Key generation, storage and rotation policy belong to A30 (`skill://a30-custody-compliance--key-rotation-plan`).
- Never read, print or move key material, keystores or mnemonics. Keys appear only as labels or public keys. If the task holds a secret, stop and report that fact without quoting it.
- Sizing hosts is `skill://a28-chain-infra--node-sync-and-snapshot`; a fork window is `skill://a28-chain-infra--hardfork-preparation`.

## Procedure

1. `read` the task inventory and build one row per (key, host): `key_ref` (label or public key; hex keys match regardless of case or a `0x` prefix), `host`, `role` (`active` or `standby`), `slashing_protection` (`none`, `local_db`, `interchange_imported`, `shared_signer`), `doppelganger_check` and `failover_wait_epochs`. A missing field is BLOCKED needs=input.
2. Call `chain_network_guard` with `environment`, `operation: "validator_key_op"`, `target_chain_id`, `mainnet_approved`, `approved_level: "L2"`. `required_level` is always L4 for this operation, so `decision: needs_approval` is the expected result: it means the review is allowed and the execution is human-only. Anything else (`block`) means the target is wrong: stop.
3. Call `chain_validator_slashing_risk` with `instances`. A key active on two hosts without one shared signer database is `double-sign:<key>` (blocker): two signers produce slashable double proposals or attestations. `slashing_exposed` true is the same fact, listed in `double_sign_keys`.
4. Read the other findings: `no-slashing-db` (major): an active key has no slashing-protection database; `standby-unprotected` or `standby-stale-history` (major): import the active host's EIP-3076 interchange export before activation; `failover-too-fast` (major): the standby must wait at least 2 epochs (about 12.8 minutes on Ethereum) after the active host stops; `standby-no-doppelganger` (minor). `shared_signer` multi-active is `info`: the signer's database refuses conflicting messages.
5. Cross-check the `chain_validator_slashing_risk` output: `key_count` equals the distinct keys you listed (hex keys compared as `0x` plus lowercase); `instance_count` equals the rows; every entry of `double_sign_keys` (canonical `0x` lowercase for hex keys) has at least two `active` rows. Anything else is E-CONTRACT.
6. Call `chain_client_diversity` for `layer: "consensus"` with validators per consensus client (and per validator client when known). More than 1/3 on one client is minor, more than 1/2 major, 2/3 or more a blocker: a client bug at those shares can stall finality, split the chain or finalize an invalid one. Plan moves toward a minority client one host at a time, never all keys at once.
7. Write the failover runbook as a numbered list for the human: stop and disable the active service; confirm no new attestations from the key for 2 epochs; export the interchange file (EIP-3076) from the stopped host; import it on the standby; start with doppelganger detection on; verify attestation inclusion; keep the old host disabled. `write` it with the findings to `chain/plans/<task_id>.md` and call `shared_artifact_digest` with that path.
8. Call `shared_escalate` with `reason_code: "policy"`, `summary: "validator key operation needs L4 approval"`, `needs: ["human-approval"]` and use its `result_patch` as the BLOCKED result when any key operation is in scope.

## Outputs

- `chain.infra.plan` section (redundancy table, risk findings, failover runbook, `network_guard` block with `required_level: L4`).

## Failure Handling

- INVALID_INPUT (one key twice on one host, a key-shaped label): fix once, else BLOCKED needs=input; never echo the offending value.
- PARTIAL_SUCCESS: `completed_fraction`; name the keys not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Doppelganger flags and slashing database formats differ per client: the plan cites the client's documented flag and defers to its docs. Penalty sizes are set by the consensus spec and change with forks: cite none.
- Key operations are non-idempotent: never retry, never run them.

## Handover

- `to_agent`: `A30` (key policy owner) with the plan; `goal`: approve and execute the failover as L4; `blockers`: `human-approval`; `decisions`: waits and roles; `artifacts`: plan with digest; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a28-chain-infra/validator-failover-safety.md; edit the source and run scripts/build_agents.py -->
