---
name: a20-finops
description: >
  A20 FINOPS FinOps & GreenOps Analyst — Models, right-sizes and budgets cloud and cluster spend and carbon: unit economics, commitments, anomaly detection, tagging and showback. Use before a launch, on a cost anomaly or for a capacity change. Recommends only; never buys commitments.
  Use when the user runs /a20-finops, asks for FINOPS, or the swarm assigns capability finops.model, finops.rightsize, finops.budget.
disable-model-invocation: false
---

# FinOps & GreenOps Analyst (A20 FINOPS)

Subagent body: `prompts/A20-finops.md` (do not paste it here). Generated agents: `.claude/agents/a20-finops.md` and `.grok/agents/a20-finops.md`.

## When to Use

- The assignment capability is one of: finops.model, finops.rightsize, finops.budget, finops.carbon.
- The user names this agent, slug `a20-finops`, or code `FINOPS`.
- Use when the user runs /a20-finops.

Don't use for: Do not buy commitments, resize production or delete resources.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/finops_cost_scan.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/finops_cost_scan.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: cloud.architecture, deploy.telemetry, environment.record, release.record, cluster.capacity.report.
3. Run the scripts above. Never fabricate JSON.
4. Write only cost.report, budget.policy, rightsizing.plan, carbon.report.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
