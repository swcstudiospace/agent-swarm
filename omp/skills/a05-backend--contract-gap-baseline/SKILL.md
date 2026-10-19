---
name: "a05-backend--contract-gap-baseline"
description: "Use when a task pins an A03 api.contract; list missing and undocumented routes before any code is written and record the bound contract versions."
disable-model-invocation: true
---

## When to Use
- The assignment pins at least one A03 `api.contract` (and possibly an A07 `data.contract`) by version and coding has not started (prompts/A05-backend.md `<OPERATING_PROCEDURE>` steps 1 and 3).
- Not for finished code: route hygiene before the PR is `a05-backend--route-surface-hygiene`; a contract you cannot meet is `a05-backend--contract-deviation-request`.

## Procedure
1. Call `shared_handover_bundle` with `{task_id}`. Take `goal`, `acceptance`, `handover.document.constraints` (empty when no handover exists) and `task.correlation_id`, and write each pinned contract as `Name@MAJOR.MINOR.PATCH`. The `task.assign` inputs are authoritative when the bundle is empty (`task_known: false`). No pin at all means BLOCKED needs=input (A05 spec section 4 rule 1: bind at least one).
2. Run `python3 scripts/be_contract_conformance.py --task-id <task_id> --correlation-id <correlation_id> --root <repo> --contract <contract file> --prefix <mount prefix> --json`. Read `missing`, `extra`, `contract_routes` and `implemented_routes`. Drop `--contract` only to inventory routes: `extra` then lists every route and `status` is always `ok`. The `bun scripts/ts/be_contract_conformance.ts` twin is a pass-through to the same Python script, so one run is enough.
3. Entries are strings: `missing` reads `METHOD /path`, `extra` reads `METHOD /path (file:line)`. Call `be_route_norm` with `{path}` on the path part of each. The script already treats `:id`, `{id}` and `<int:id>` alike, so what this step finds is a mount mismatch: an `extra` whose `normalized` ends with the `normalized` of a `missing` route with the same method means the `--prefix` is wrong. Rerun step 2 with the right prefix instead of listing work.
4. For each contract path that has parameters call `be_openapi_path_params` with `{template}`. A non-empty `duplicates` is a contract defect for a deviation request, not a coding task; `param_count` is the number of path parameters the handler must read.
5. Write the work items: one per remaining `missing` route, and for each `extra` route either "remove" or "needs contract.change.request". Do not write code yet.
6. Call `shared_scratchpad_put` with `{correlation_id, key: "a05.contract-gaps", value: {pins, missing, extra, work_items}}` (value is an object, at most 8 KiB). `a05.contract-gaps` is this skill's key convention, read back by the self-gate and the route hygiene skill.

## Outputs
- Pinned contract list (`contracts_bound` candidates), gap lists from the script, work items, scratchpad key `a05.contract-gaps`.

## Failure Handling
- Contract file unreadable or script error (`status: fail`, finding `BC-000`): BLOCKED needs=input naming the path; never guess routes.
- `be_route_norm` or `be_openapi_path_params` INVALID_INPUT (template not starting with `/`, nested braces): correct the named field once, then report the route as unchecked.
- Scratchpad full (64 keys) or oversize: keep only route strings in `value`.

## Handover
No handover is sent here. `remaining` takes the work items and `constraints` takes the pins and mount prefix; they travel in the handover the self-gate writes (`skill://swarm-handover-emit`). The read side is `shared_handover_bundle`.

<!-- generated from prompts/skills/a05-backend/contract-gap-baseline.md; edit the source and run scripts/build_agents.py -->
