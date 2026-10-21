---
name: a01-orchestrator--parallel-lanes
description: Use when a brief has two or more path-disjoint scopes; draft blast radii, persist a parallel plan and preview which lanes start under the agent ceilings.
owner: a01-orchestrator
tools: [orch_dag_order, orch_budget_class, orch_dispatch_batch, shared_handover_validate, shared_handover_write]
---

# Parallel lanes

## When to Use

- The brief splits into scopes whose paths, owners or acceptance criteria share no file (agents/A01 decision rule 1). Single-stream work: `skill://a01-orchestrator--plan-decompose`.

## Procedure

1. Use `grep` and `glob` on the repo to find the paths each scope touches. Merge two scopes that share a file: radii overlap when paths are equal or one is a directory prefix of the other (`swarm/blast_radius.py` `_overlaps`).
2. Draft the blast-radii array for `scripts/orch_plan.py`, 2 to 32 objects `{id, paths[], agent, capability?, title?, acceptance?}`. `id` matches `[a-z][a-z0-9-]{0,31}` and is not `join`, `rev`, `qa`, `sec`, `rel`. Paths are repo-relative. `agent` is an implementer id (A02-A07, A11, A13-A30; not A01, A08, A09, A10, A12), or give a `capability` that one of them offers; the same agent may repeat.
3. Call `orch_dag_order` with `{nodes}`: every slice id with `depends_on: []`, `join` depending on all slice ids, `rev` depending on `["join"]`. A cycle or missing id is `INVALID_INPUT` field `nodes`.
4. Call `orch_budget_class` with `{risk}` for the plan risk class and note the three integers; `orch_plan.py` gives each task of that class this budget (`_budget`).
5. Persist with `python3 scripts/orch_plan.py --repo <repo> --brief-text "<brief>" --pattern parallel --slices-json '<array>' --risk-class <class> --json` (or `--slices file.json`, or a fenced `blast-radii` block in the brief; one of `--brief`, `--brief-text` or `--plan` is required). E-INPUT `blast radii overlap: a:path and b:path`: shrink or merge the two radii and rerun once.
6. Preview the first round: `python3 scripts/orch_status.py --repo <repo> --correlation-id <id> --json`, then call `orch_dispatch_batch` with `{ready: [{id: task_id, agent_id: agent}] for rows with ready true, running: {}, ceilings: {<agent id>: max_parallel from agents.json}, global_cap}`. `global_cap` is `--max-parallel` or `SWARM_MAX_PARALLEL` (default 4, at most 32). Nothing starts here; `deferred` is the queue.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write`, once per ready lane.

## Outputs

- Correlation id, lane task ids `<prefix>-<slice>` (prefix `T` plus 4 hex of the correlation id), branch `swarm/<prefix>/<slice-id>`, `isolate: worktree`, and the `<prefix>-join` and gate task ids.
- `start` and `deferred` (with reason) from the preview.

## Failure Handling

- Fewer than two radii after merging: not parallel; run `skill://a01-orchestrator--plan-decompose`.
- Slice agent is a control or gate agent (E-INPUT): choose an implementer.
- Never spawn another A01 per lane and never serialize lanes to let them review each other.

## Handover

- One handover per ready lane, `to_agent` the lane agent id. Fill `goal` (the slice acceptance), `constraints` (the paths, the branch, "do not review, do not merge"), `remaining` (lane work), `artifacts` (none yet).
