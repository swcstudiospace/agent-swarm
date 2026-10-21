---
name: a27-contract-audit--gas-dos-review
description: Use when a gas.report or loops over user-growable arrays or push payments could make a function exceed the gas limit or be blocked by one failing call.
owner: a27-contract-audit
tools: [audit_classify_vulnerability, audit_score_severity, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A `gas.report` (A26) lists a function whose gas grows with a user-controlled collection, or the code loops over `array.length`, iterates holders, distributes rewards or pays several recipients in one call.
- Gas optimization advice is not this skill's job (A26 owns optimization); this one asks "can an attacker or growth make the function uncallable".
- Analysis is L2. Running load tests is local only (Foundry or anvil), never on a public network.

## Procedure

1. `read` the `gas.report` and find the functions whose max gas is far above their average (a wide spread signals a data-dependent loop). Cross-check the function names against the code with `grep -n`; a report entry without code at the audited commit is E-CONTRACT (stale report), stop.
2. `grep -n` `for \(|while \(` and storage arrays with `push` and no `pop`/removal. For each loop record: the bound (constant, `length` of a storage array, `length` of a calldata array), who can grow it (anyone, a role, nobody), and the per-iteration cost (storage write about 20000 gas, cold `SLOAD` 2100, external call at least 2600 cold; these are EVM schedule values, confirm them for the target fork).
3. Find the gas limit that applies: read the chain's block gas limit and any per-transaction gas cap from the deploy plan or chain config. Never assume one number; a per-transaction cap (for example the one introduced by EIP-7825 where activated) can be lower than the block limit and is the binding one.
4. Model the worst case: at the largest N an attacker can reach (spam deposits of dust, many small orders) compute `base + N x per_iteration`. With the `gas.report` slope from two measured points when available; otherwise run a local test with `bash` (`forge test --match-test test_gasGrowth --gas-report`) at three sizes. Decision rule of this skill: worst case above 100 % of the binding limit means permanent denial of service (a finding); 50 to 100 % is a margin finding; under 50 % with a hard cap on N is a note.
5. Push payments, found with `grep -n` on `.call{`, `.transfer(`, `.send(`: a loop that sends ether or tokens to several recipients and reverts on one failure lets a recipient block everyone (SWC-113 `failed-call-dos`); a `call` with a fixed gas amount is `hardcoded-gas-call`. Check whether failures are caught or payments are pull-based.
6. Call `audit_classify_vulnerability` with `observations` = `[{id, signal, location}]` using `unbounded-loop` (SWC-128), `failed-call-dos` (SWC-113), `hardcoded-gas-call`, `insufficient-gas-griefing` for what you confirmed. Read `counts` and `verdict`; `total` equals the observations sent.
7. Call `audit_score_severity` for each: `loss_usd` is the funds frozen when the function is bricked (a withdrawal loop frozen means `irrecoverable` true), `tvl_usd`, `attacker_privilege` (`permissionless` if anyone can grow the array), `preconditions`. The default severity of the signal (medium) is a start; the scored `severity` wins. Cite the tool's value, whatever it is.
8. Recommend: cap N, paginate with a cursor, pull payments, per-recipient try/catch with a recorded failure. `write` `audit/gas-dos-review.md` with the table (function, bound, who grows it, worst-case gas, limit used) and digest it with `shared_artifact_digest`.

## Outputs

- Gas-DoS part of `audit.findings` with the worst-case calculation and the limit's source; the results of the local gas test as evidence.

## Failure Handling

- No `gas.report`: run the local tests yourself or BLOCKED `needs=input`; never estimate a function's gas from memory.
- INVALID_INPUT: fix the signal name (it must be one of the tool's enum) or the ids (unique).
- The per-transaction gas cap and EVM gas schedule depend on the fork and chain: where unsure, say "limit read from the deploy plan" or mark the result partial; defer to the tool output and the measured numbers.
- DEPENDENCY_UNAVAILABLE (`forge`): retry once, then `skipped:tool-missing`.

## Handover

- `to_agent`: `A26` (owner of contract fixes; it also produced the gas report). `artifacts`: review file with digest. `open_questions`: the limit used and who confirmed it. `verification`: verified only if the worst case rests on measured gas.
- End with `shared_handover_validate`, then `shared_handover_write`.
