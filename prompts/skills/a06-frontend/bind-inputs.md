---
name: a06-frontend--bind-inputs
description: Use when an A06 task starts and before any code is written; read the handover, detect the toolchain, bind spec, token and contract versions, list gaps.
owner: a06-frontend
tools: [shared_handover_read, shared_toolchain, shared_check_plan, shared_artifact_check, shared_open_questions, shared_handover_write]
---

## When to Use
- A `task.assign` just arrived and nothing has been written yet (prompt OPERATING_PROCEDURE steps 2 and 3: "Do not write code yet").
- Bind only reads and records. It never files a request: gaps it finds go to `a06-frontend--design-deviation` (spec or token gaps) or `a06-frontend--api-contract-bind` (API gaps).

## Procedure
1. Call `shared_handover_read` with `{task_id}`. `status` ok: take `goal`, `constraints` and `artifacts`. `missing`, `stale` or `malformed`: work from `task.assign` `inputs[]` alone and add the reason to the blockers list of step 6.
2. Call `shared_toolchain` with `{"scan": true}`, then `shared_check_plan` with `{"scan": true}`. Record `js`, `python` and the check `names` string; the final `checks` block must report the same names.
3. Use `read` on every entry of `inputs[]`. From `ux.spec` list each screen id with its `states`, focus order and breakpoints; from `design.system.tokens` the token set name and version; from `api.contract` each contract name and version; from `acceptance.criteria` the criterion ids; from a `perf.budget` input (when present) the bundle budget and thresholds.
4. Call `shared_artifact_check` with `{task_id, agent: "<upstream producer id such as A04>", outputs: [{kind, uri, digest, producer}]}`, one call per producer. `failed` above 0 (`missing`, `digest_mismatch`, `producer_mismatch`) means that input is not trusted; `unchecked` (a non-file uri or an output with no digest) is recorded as unverified, never as fine.
5. Call `shared_open_questions` with `{task_id}` and compare the list with what the spec leaves out: a state with no definition, a token the set lacks, a screen with no focus order, a contract with no version. Each is a gap with its owner (A04 for spec and tokens, A03 for contract).
6. Compose the bound-inputs note: screens with spec states, token version, contract versions, check names, gaps with owners, blockers. Keep it in your draft result; call `shared_handover_write` only when a gap or blocker stops the task (to_agent A01).

## Outputs
- A bound-inputs note: `screens[]` with `states`, `tokens_bound` candidate (`design.system.tokens@<version>`), `contracts_bound` candidates, toolchain flags, check names, gaps.

## Failure Handling
- `shared_toolchain` or `shared_check_plan` fails: retry once, then record `skipped:tool-missing` for that fact and continue (prompt DEGRADED_MODE).
- No `ux.spec` or no `api.contract` among the inputs: BLOCKED `needs=input` naming the missing artifact; do not infer states or shapes.

## Handover
Fields filled: `constraints` (token version, contract versions, spec states per screen), `blockers` (untrusted or missing inputs), `open_questions` (gaps, at most one question per owner). Call `shared_handover_write` to A01 when blocked; otherwise the same note goes into the task's closing handover.
