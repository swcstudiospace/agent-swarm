---
name: "a15-docs--adr-render"
description: "Use when an adr.set is new or changed and accepted, superseded and deprecated ADRs must be rendered into docs verbatim with supersession links resolving."
disable-model-invocation: true
---

## When to Use
- A03 delivers or changes an `adr.set` and the docs site must show the decisions (agents/A15-docs.md section 2: ADR renderers by A03).
- Not for endpoint reference: use `a15-docs--api-reference`. ADRs are rendered, never rewritten or judged here.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; take the `adr.set` artifact (`kind`, `uri`, `digest`, `producer`) from `artifacts`.
2. Call `shared_artifact_check` with `task_id`, `outputs` (the adr.set artifact, `producer` included) and `agent` (the producer, normally `A03`). Read `results[].status`: only `ok` continues; `missing` goes to `a15-docs--missing-artifact-stub`; `digest_mismatch` or `producer_mismatch` goes to A03 as an open question; never edit the set.
3. Use `read` on the adr.set and `write` one doc per ADR whose status is accepted, superseded or deprecated (the same three statuses `docs_adr_render_coverage` counts): title, status, date and body copied verbatim. Proposed and rejected ADRs are not rendered. Use the doc paths the task `outputs[]` names; with none named, `docs/adr/<ADR-id>.md` (a convention of this skill).
4. Call `docs_adr_render_coverage` with `adrs` (`{id, status}` for every ADR in the set), `rendered` (ids you wrote) and `target_pct` 100. Render each id in `missing`; for each `orphan_rendered` id (rendered but absent from the set) `read` the doc and delete it with `bash` only when the task says the ADR left the set, else report it.
5. For superseded ADRs, call `docs_link_targets` with `files` (repo-relative paths of all rendered docs) and `links` (every `{source, target}` that points at another ADR, found with `grep` pattern `ADR-[0-9]+`). Each `broken` entry (`"source -> target"`) is fixed by pointing at the rendered file of the successor.
6. When this task is a rework after a failed gate (`swarm-rework-intake` has the loop), call `shared_open_findings` with `task_id` and fix each finding that concerns rendering; findings about the decision itself go upstream to A03, not into the text.

## Outputs
- One rendered doc per in-scope ADR, plus `adr_rendered_pct`, `missing`, `orphan_rendered` and the `excluded` count.

## Failure Handling
- An ADR text contradicts another: render both as written and list the conflict as an open question; never merge or edit decisions.
- INVALID_INPUT from `docs_adr_render_coverage` (duplicate ADR id, or nothing in scope): fix the list once; otherwise BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing` and report coverage as unproven.

## Handover
Fill `goal`, `completed` (ids rendered), `remaining` (missing or orphan ids), `open_questions` (conflicts and orphans for A03), `artifacts` (each rendered path with its `shared_artifact_digest` digest, producer `A15`), `verification` (`verified` only when `adr_rendered_pct` is 100). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent named in task routing, A01 when unknown.

<!-- generated from prompts/skills/a15-docs/adr-render.md; edit the source and run scripts/build_agents.py -->
