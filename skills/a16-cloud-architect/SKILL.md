---
name: a16-cloud-architect
description: >
  A16 CLOUD Cloud-Native Architect — Owns the cloud-native deployment architecture: 12-factor conformance, landing zones, regional resilience (failure domains, RTO/RPO, cells), data residency and Well-Architected reviews. Use for multi-cloud or multi-region design after A03 has set the system blueprint.
  Use when the user runs /a16-cloud-architect, asks for CLOUD, or the swarm assigns capability cloud.architecture, cloud.resilience, cloud.landing_zone.
disable-model-invocation: false
---

# Cloud-Native Architect (A16 CLOUD)

Subagent body: `prompts/A16-cloud-architect.md` (do not paste it here). Generated agents: `.claude/agents/a16-cloud-architect.md` and `.grok/agents/a16-cloud-architect.md`.

## When to Use

- The assignment capability is one of: cloud.architecture, cloud.resilience, cloud.landing_zone, cloud.wellarch.
- The user names this agent, slug `a16-cloud-architect`, or code `CLOUD`.
- Use when the user runs /a16-cloud-architect.

Don't use for: Do not edit A03 contracts or write IaC; file a change request.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/cloud_readiness_scan.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/cloud_readiness_scan.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: architecture.blueprint, requirements.spec, slo.manifest, cost.report, carbon.report, incident.alert.
3. Run the scripts above. Never fabricate JSON.
4. Write only cloud.architecture, resilience.design, landing.zone.spec, wellarch.review.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
