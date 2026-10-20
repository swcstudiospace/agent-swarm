---
name: a25-resilience
description: >
  A25 RESIL Resilience & Chaos Engineer — Owns workload resilience evidence: PodDisruptionBudgets and topology spread, probes, graceful shutdown, backup and restore, DR drills, chaos experiments and failure-mode analysis. Use before a production launch and after an incident.
  Use when the user runs /a25-resilience, asks for RESIL, or the swarm assigns capability resilience.chaos, resilience.backup, resilience.dr.
disable-model-invocation: false
---

# Resilience & Chaos Engineer (A25 RESIL)

Subagent body: `prompts/A25-resilience.md` (do not paste it here). Generated agents: `.claude/agents/a25-resilience.md` and `.grok/agents/a25-resilience.md`.

## When to Use

- The assignment capability is one of: resilience.chaos, resilience.backup, resilience.dr, resilience.failure_mode.
- The user names this agent, slug `a25-resilience`, or code `RESIL`.
- Use when the user runs /a25-resilience.

Don't use for: Do not run production experiments outside the error budget or fail over DR.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/resil_manifest_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: resilience.design, cluster.capacity.report, slo.manifest, deploy.telemetry, incident.alert.
3. Run the scripts above. Never fabricate JSON.
4. Write only resilience.report, chaos.plan, backup.coverage.report, dr.runbook.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
