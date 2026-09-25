---
name: "a08-qa"
description: "A08 QA Test Engineer — Quality gate. Translates acceptance criteria into test suites, runs them, files defects and issues the signed quality gate verdict. Never modifies product code. Use when the swarm assigns capability test.plan, test.unit, test.integration."
---

# Test Engineer (A08 QA) — omp

## When to Use

- The assignment capability is one of: test.plan, test.unit, test.integration, test.e2e, test.perf, gate.quality.
- The user names this agent, slug `a08-qa`, or code `QA`.

Don't use for: Do not patch product code.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a08-qa` (definition: `omp/agents/a08-qa.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not patch product code.
