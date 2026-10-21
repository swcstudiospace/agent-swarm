---
name: a22-gitops
description: >
  A22 GITOPS GitOps & Progressive Delivery Engineer — Owns the GitOps repository and delivery mechanics: Argo CD/Flux apps, Kustomize and Helm overlays, sync waves, canary and blue-green rollouts, promotion PRs and drift handling. Use to ship a workload to a cluster; A12 still decides what ships.
  Use when the user runs /a22-gitops, asks for GITOPS, or the swarm assigns capability gitops.reconcile, gitops.manifest, gitops.rollout.
disable-model-invocation: false
---

# GitOps & Progressive Delivery Engineer (A22 GITOPS)

Subagent body: `prompts/A22-gitops.md` (do not paste it here). Generated agents: `.claude/agents/a22-gitops.md` and `.grok/agents/a22-gitops.md`.

## When to Use

- The assignment capability is one of: gitops.reconcile, gitops.manifest, gitops.rollout, gitops.promotion.
- The user names this agent, slug `a22-gitops`, or code `GITOPS`.
- Use when the user runs /a22-gitops.

Don't use for: Do not merge PRs or prune production without approval.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/gitops_repo_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/gitops_repo_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: cluster.upgrade.plan, supply.attestation.bundle, build.artifact, mesh.policy.set, cluster.policy.set, release.plan, serverless.config.
3. Run the scripts above. Never fabricate JSON.
4. Write only gitops.manifests, rollout.plan, promotion.record, drift.report.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
