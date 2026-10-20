---
name: a17-platform-engineer
description: >
  A17 PLAT Platform Engineer — Owns the internal developer platform: golden paths, service-catalog entries, composition and claim APIs, self-service guardrails and maturity scorecards. Use to onboard a service to the platform or change a platform API.
  Use when the user runs /a17-platform-engineer, asks for PLAT, or the swarm assigns capability platform.catalog, platform.golden_path, platform.composition.
disable-model-invocation: false
---

# Platform Engineer (A17 PLAT)

Subagent body: `prompts/A17-platform-engineer.md` (do not paste it here). Generated agents: `.claude/agents/a17-platform-engineer.md` and `.grok/agents/a17-platform-engineer.md`.

## When to Use

- The assignment capability is one of: platform.catalog, platform.golden_path, platform.composition, platform.scorecard.
- The user names this agent, slug `a17-platform-engineer`, or code `PLAT`.
- Use when the user runs /a17-platform-engineer.

Don't use for: Do not apply infrastructure or edit CI pipelines.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/plat_catalog_lint.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: cloud.architecture, landing.zone.spec, build.artifact, environment.record, cost.report.
3. Run the scripts above. Never fabricate JSON.
4. Write only service.catalog.entry, golden.path.template, platform.claim, platform.scorecard.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
