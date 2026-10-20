---
name: "a29-dapp-wallet--ens-integration"
description: "Use when a dapp.wallet task resolves or shows ENS names and normalization, homograph characters and forward-verified reverse records must be checked."
disable-model-invocation: true
---

# ENS integration

## When to Use

- A `dapp.wallet` task lets users type an ENS name as a recipient, shows a name instead of an address, or resolves avatars and text records.
- Address validation without ENS and approvals are `skill://a29-dapp-wallet--approval-hygiene`. Code is L2; reads on mainnet or a testnet are read-only calls.

## Procedure

1. `grep` the sources for `getEnsAddress`, `resolveName`, `lookupAddress`, `getEnsName`, `namehash`, `.eth`. Note whether the code normalizes before hashing and which library does it.
2. For each name the flow accepts, call `dapp_ens_name_check` with `name`. Blockers: `ENS-EMPTY-LABEL`, `ENS-FORBIDDEN-CHARACTER`, `ENS-INVISIBLE-CHARACTER` (zero-width and control characters; a joiner inside an emoji sequence is allowed), `ENS-MIXED-SCRIPT` (homograph spoofing; Han with Kana, Hangul or Bopomofo is one script set, not a mix). Majors: `ENS-NOT-NORMALIZED` (uppercase hashes a different node), `ENS-UNDERSCORE`, `ENS-PUNYCODE`, `ENS-LABEL-EXTENSION`, `ENS-ETH-SHORT-LABEL` (a `.eth` second-level label needs 3 or more characters), `ENS-NON-ASCII`, `ENS-NOT-NFC`.
3. `read` the tool output: it normalizes ASCII only. On `ENS-NON-ASCII`, `normalization` is not authoritative: require an ENSIP-15 normalizer (for example the `@adraffy/ens-normalize` package, confirmed in the lockfile) before namehash and a confusable check, and say the tool did not clear it.
4. Cross-check hashes: compute the EIP-137 namehash for 3 names with the app's own library (`bash`, inside the repo toolchain) and compare to `namehash` from the tool; a difference means the app normalizes differently (E-CONTRACT, report both).
5. Reverse records: the claimed name of an address is self-asserted. Call `dapp_ens_name_check` with `name` (what the reverse lookup returned), `address` (the account that claims it) and `forward_address` (what resolving that name forward returns). Check `reverse_verified`, `reverse_name` and `reverse_node`. `ENS-REVERSE-SPOOF` and `ENS-FORWARD-UNSET` are blockers: show the raw checksummed address instead. `ENS-REVERSE-UNVERIFIED` (major) means forward resolution was never run: the UI must run it.
6. Call `dapp_signing_request_scan` with `hardcoded_addresses` (any literal address in the sources the task lists), `link_urls` (ENS-app links shown) and `expected_domains`. `ADDRESS-CHECKSUM` (major) is a typo or spoof; `ADDRESS-NOT-IN-REGISTRY` (minor) means the address is not in the deployment registry; `*-LOOKALIKE`/`*-SPOOF`/`LINK-USERINFO-SPOOF`/`LINK-DANGEROUS-SCHEME` link findings are blockers.
7. Implement (`edit`): normalize, resolve, then show both the name and the full resolved address (EIP-55 checksummed) in the confirmation step before any send; handle a `null` resolution and CCIP-Read (EIP-3668) failures as "not found", never as an empty address; show a name only after `reverse_verified`.
8. Call `shared_artifact_digest` on the patch and test names, then hand over.

## Outputs

- `code.patch` (resolve and display helpers) and `wallet.flow.spec` section (name states: unresolved, spoofed, verified), citing `verdict`, `normalization`, `reverse_verified` per name.

## Failure Handling

- INVALID_INPUT (`name` over 253 chars, bad `address`): fix the field or BLOCKED needs=input. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Without forward and reverse results from a real resolver (testnet or mainnet read), say the reverse check did not run; never mark a name verified from the tool alone.
- ENS rules evolve (ENSIP-15, new TLDs): defer to the pinned normalizer and the tool findings.

## Handover

- `to_agent`: `A09`. `goal`: review name resolution and display; `decisions`: show address next to name; `artifacts`: patch with digest (producer `A29`); `open_questions`: non-ASCII names pending the ENSIP-15 library; `verification`: partial.
- End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a29-dapp-wallet/ens-integration.md; edit the source and run scripts/build_agents.py -->
