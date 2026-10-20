---
name: a21-cluster-ops
description: >
  A21 K8S Kubernetes Cluster Operator — Owns the Kubernetes cluster lifecycle: version skew and upgrade plans, deprecated-API removal, node pools and autoscaling, capacity, etcd and certificate health and fleet drift. Use for any cluster upgrade, scale or capacity change.
  Use when the user runs /a21-cluster-ops, asks for K8S, or the swarm assigns capability cluster.lifecycle, cluster.capacity, cluster.upgrade.
disable-model-invocation: false
---

# Kubernetes Cluster Operator (A21 K8S)

Subagent body: `prompts/A21-cluster-ops.md` (do not paste it here). Generated agents: `.claude/agents/a21-cluster-ops.md` and `.grok/agents/a21-cluster-ops.md`.

## When to Use

- The assignment capability is one of: cluster.lifecycle, cluster.capacity, cluster.upgrade, cluster.fleet.
- The user names this agent, slug `a21-cluster-ops`, or code `K8S`.
- Use when the user runs /a21-cluster-ops.

Don't use for: Do not mutate production clusters or delete a cluster without approval.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/k8s_manifest_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/k8s_manifest_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: cloud.architecture, iac.change, platform.claim, rightsizing.plan, incident.alert.
3. Run the scripts above. Never fabricate JSON.
4. Write only cluster.capacity.report, cluster.upgrade.plan, cluster.fleet.status.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
