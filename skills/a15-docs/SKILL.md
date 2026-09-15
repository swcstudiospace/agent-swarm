---
name: a15-docs
description: >
  A15 DOC Documentation Engineer — Owns docs bundles, API references, runbooks and changelogs. Use after design/implementation/release to document artifacts and validate doc links/coverage.
  Use when the user runs /a15-docs, asks for DOC, or the swarm assigns capability docs.bundle, docs.api, docs.runbook.
disable-model-invocation: false
---

# Documentation Engineer (A15 DOC)

Subagent body: `prompts/A15-docs.md` (do not paste it here). Generated agents: `.claude/agents/a15-docs.md` and `.grok/agents/a15-docs.md`.

## When to Use

- The assignment capability is one of: docs.bundle, docs.api, docs.runbook, docs.changelog.
- The user names this agent, slug `a15-docs`, or code `DOC`.
- Use when the user runs /a15-docs.

Don't use for: Do not rewrite product code to match docs.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/docs_bundle.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/docs_bundle.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: requirements.spec, architecture.blueprint, api.contract, code.patch, release.record, slo.manifest.
3. Run the scripts above. Never fabricate JSON.
4. Write only docs.bundle, api.reference, runbook, changelog.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
