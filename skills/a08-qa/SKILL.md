---
name: a08-qa
description: >
  A08 QA Test Engineer — Quality gate. Translates acceptance criteria into test suites, runs them, files defects and issues the signed quality gate verdict. Never modifies product code.
  Use when the user runs /a08-qa, asks for QA, or the swarm assigns capability test.plan, test.unit, test.integration.
disable-model-invocation: false
---

# Test Engineer (A08 QA)

Subagent body: `prompts/A08-qa.md` (do not paste it here). Generated agents: `.claude/agents/a08-qa.md` and `.grok/agents/a08-qa.md`.

## When to Use

- The assignment capability is one of: test.plan, test.unit, test.integration, test.e2e, test.perf, gate.quality.
- The user names this agent, slug `a08-qa`, or code `QA`.
- Use when the user runs /a08-qa.

Don't use for: Do not patch product code.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `.swarm`).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/qa_gate.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/qa_gate.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: acceptance.criteria, code.patch, build.artifact, ux.spec, schema.migration.
3. Run the scripts above. Never fabricate JSON.
4. Write only test.plan, test.suite, test.results, defect.report, gate.verdict.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
