---
name: "a06-frontend--api-contract-bind"
description: "Use when UI code adds or changes an API call; match every call to a path and method of the pinned contract, type the client from it, and report a mismatch."
disable-model-invocation: true
---

## When to Use
- A component, hook or service adds, renames or removes a network call (A06 spec section 4 rule 6 and section 5: call only contracted shapes; a mismatch files a `conflict.report` and ships the flag-off path).
- Acts on calls in code. Reading the contract versions at task start is `a06-frontend--bind-inputs`.

## Procedure
1. Take the pinned contract name and version (`contracts_bound`) from the bound-inputs note. After a resume call `shared_handover_read` with `{task_id}` and read `handover.constraints`; with `status` other than `ok`, take the pin from the `api.contract` entry of `inputs[]`.
2. Use `read` on the `api.contract`. When it is JSON, call `arch_contract_ops` with `{document: <the parsed OpenAPI or AsyncAPI object>}` and keep `operations` (a count). A contract that exists only as YAML cannot be passed: count its operations by hand and mark the count UNVERIFIED. Note the server base path (`servers[].url`) too.
3. Use `grep` for the client calls in the changed files (`fetch(`, the HTTP client wrapper, generated client methods) and list `{method, path}` per call.
4. For every client path and every contract path call `be_route_norm` with `{path}` (strip the server base path first); it maps `:id` and `{id}` to the same `{}` form and drops a trailing slash. A call whose method and normalized path are absent from the contract is out of contract: remove it or go to step 6. More distinct calls than `operations` also means some call is not in the contract.
5. Type the client from the contract's schemas only (generated types or hand-typed from the named schema), with `edit`; never from a sample response, never `any`.
6. On a mismatch, decide which kind it is. (a) The backend diverged at runtime: compose the `conflict.report` text (contract pin, request, observed response, expected response, reproduction steps) as one string for the handover `completed`, ship the consumer-side feature-flag-off path when the screen is user-facing, and call `shared_escalate` with `{task_id, reason_code: "conflict", summary, needs: ["A05 or A03 to reconcile <operation>"]}`. (b) The contract cannot serve the UI: you may not change shapes, so call `shared_open_questions` with `{task_id}` and, when no question to A03 is open, put exactly one in the handover.

## Outputs
- A calls-versus-contract table (method, normalized path, in contract yes or no), `contracts_bound`, and any `conflict.report` or change request text.

## Failure Handling
- `UNAUTHORIZED` from `be_route_norm` or `arch_contract_ops`: the grant is not applied in this registry; list the normalization as not run, compare paths by hand and mark them UNVERIFIED.
- `INVALID_INPUT` field `document`: the contract was not an object; do not retry with a guess.

## Handover
Fields filled: `constraints` (contract pins), `blockers` (open mismatches), `open_questions` (at most one, to A03), `decisions` (flag-off path chosen), `completed` (the `conflict.report` string). Written with `shared_handover_write` to A03 for a question, or to A01 (the arbiter named by `shared_escalate`) for a divergence.

<!-- generated from prompts/skills/a06-frontend/api-contract-bind.md; edit the source and run scripts/build_agents.py -->
