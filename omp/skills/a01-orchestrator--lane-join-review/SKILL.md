---
name: "a01-orchestrator--lane-join-review"
description: "Use when every blast-radius lane is finished and only the join and one review remain; verify lanes, run the join, dispatch the single rev task to A09."
disable-model-invocation: true
---

# Lane join review

## When to Use

- All lane tasks of a `parallel` plan are IN_REVIEW, APPROVED or DONE and the `join` task is PLANNED. Review happens once, on the merged branch (prompts/A01 blast_radius). Real task ids are `<prefix>-<slice>`, `<prefix>-join` and `<prefix>-rev` (prefix `T` plus 4 hex of the correlation id); this skill writes `join` and `rev`.

## Procedure

1. Run `python3 scripts/orch_status.py --repo <repo> --correlation-id <id> --json`. Call `orch_dag_blocked` with `{tasks: [{id: task_id, depends_on, state}]}`; `join` must be in `ready` and `blocked` must be empty. A lane still waiting means this skill is early.
2. For each lane call `shared_open_findings` with `{task_id}`. Lanes are never reviewed before the join, so `blocking` above 0 means a stray verdict: stop and run `skill://a01-orchestrator--rework-loop`.
3. For each lane call `shared_artifact_check` with `{task_id, agent: <lane agent id>}`. `failed` above 0 holds the join.
4. Dispatch `join` through `skill://a01-orchestrator--dispatch-ready` and watch it with `scripts/orch_status.py`. `swarm_run.py` executes the join itself with no model (`notes.merge_branches`); in-session its assignment goes to A11 (`ci.pipeline`). A merge conflict aborts and leaves the checkout clean (`blast_radius.merge_branches`, E-CONTRACT `cannot merge <branch>`): that goes to `skill://a01-orchestrator--escalate-human` with the conflicting branches as evidence.
5. When `join` is IN_REVIEW or later, re-run step 1; the `rev` task (`gate.review`, A09, `review_source: greptile`, `review_scope: merged-branch`) becomes ready.
6. Call `shared_handover_bundle` with `{task_id: <rev id>}` and read `goal`, `acceptance`, `upstream`, `artifacts`: that is what A09 will see. Dispatch `rev` through `skill://a01-orchestrator--dispatch-ready`; the other gate tasks of the risk class (`qa`, `sec`, `rel`) also depend on `join` and run once on the merged branch.
7. Call `shared_handover_validate` with `{document, draft: true}`, then `shared_handover_write` to A09.

## Outputs

- Join result, merged branch names (`notes.merge_branches`), `rev` task id dispatched to A09.

## Failure Handling

- A09 finishes BLOCKED when the merged branch has no open pull request to ingest: relay the missing PR to the operator; never invent findings and never record a verdict.
- Do not run per-lane reviews.

## Handover

- `to_agent` `A09`, `task_id` the `rev` task. Fill `goal` (review the merged branch once), `artifacts` (merged branch, lane results with digests), `constraints` (one review for the whole plan), `remaining` (A09 gate steps).

<!-- generated from prompts/skills/a01-orchestrator/lane-join-review.md; edit the source and run scripts/build_agents.py -->
