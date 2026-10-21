---
name: a15-docs--missing-artifact-stub
description: Use when a contract, ADR set or release record a doc is rendered from is absent, so a flagged auto-extracted stale stub is written, not invented content.
owner: a15-docs
tools: [shared_handover_bundle, shared_artifact_check, shared_artifact_digest, docs_digest_staleness, shared_open_questions, shared_handover_validate, shared_handover_write]
---

## When to Use
- The upstream artifact (`api.contract`, `adr.set`, `release.record`) is missing, empty or unreadable (03-agents/A15-docs.md section 5: stub with `auto-extracted` signatures and `stale: true`).
- Not when the artifact exists: render it with `a15-docs--api-reference` or `a15-docs--adr-render`. The sweep that only flags stale docs is `a15-docs--staleness-sweep`.

## Procedure
1. Call `shared_handover_bundle` with `task_id`; note which artifact is expected and its producer (`api.contract` and `adr.set` from A03, `data.contract` from A07, `release.record` from A12).
2. Call `shared_artifact_check` with `task_id`, `outputs` (the expected artifact, `producer` included) and `agent` (its producer). Proceed only when `results[].status` is `missing` or the uri cannot be read; an `ok` result means use the rendering skill instead.
3. Extract what the code says without prose: use `grep` over the files of the `code.patch` for route declarations, function and class signatures. `docs_bundle.py` only indexes markdown, so it cannot do this step.
4. `write` the stub at the doc path the task names: front matter `auto-extracted: true`, `stale: true`, `bound_artifact: <expected path>` and no `bound_digest`; body is the extracted signatures under the heading `Extracted signatures (unverified)`. No descriptions, no behaviour claims.
5. Call `docs_digest_staleness` with `docs` (the stub's `path` and `bound_artifact`) and `current` (`{artifact, digest}` for the artifacts that do exist, possibly empty). The stub must appear in `stale` (an entry with `path` and `reason`); if it shows in `unbound` or not at all, the front matter binding is wrong: fix it.
6. Call `shared_open_questions` with `task_id` to avoid duplicating a question, then raise one in the handover `open_questions` for the producer: which artifact, which docs it blocks, and that release notes for that scope stay blocked.
7. Call `shared_artifact_digest` with the stub's `path` for the artifacts list.

## Outputs
- The stub file, the `stale` verdict from the tool and one open question for the producing agent.

## Failure Handling
- No signatures extractable: write a stub with the heading only and `stale: true`; never fill in plausible content.
- INVALID_INPUT: fix the named field once; else BLOCKED `needs=input`.
- Tool DEPENDENCY_UNAVAILABLE: retry twice, then `skipped:tool-missing`; keep the stub flagged stale.

## Handover
Fill `goal` (replace the stub when the artifact arrives), `completed`, `remaining` (render from the real artifact), `blockers` (release notes for the scope), `open_questions` (the producer question), `artifacts` (stub path with digest, producer `A15`), `verification` (`unverified`). Write with `swarm-handover-emit` (`shared_handover_validate`, then `shared_handover_write`) to the agent named in task routing, A01 when unknown; name the producer (`A03`, `A07` or `A12`) in `open_questions`.
