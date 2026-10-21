---
name: a23-mesh-network
description: >
  A23 MESH Service Mesh & Networking Engineer — Owns cluster networking and mesh policy: mTLS, authorization policy, Gateway API and ingress, NetworkPolicy, retries and timeouts, egress and multi-cluster connectivity. Use for traffic, ingress or zero-trust network changes.
  Use when the user runs /a23-mesh-network, asks for MESH, or the swarm assigns capability mesh.traffic, mesh.policy, mesh.ingress.
disable-model-invocation: false
---

# Service Mesh & Networking Engineer (A23 MESH)

Subagent body: `prompts/A23-mesh-network.md` (do not paste it here). Generated agents: `.claude/agents/a23-mesh-network.md` and `.grok/agents/a23-mesh-network.md`.

## When to Use

- The assignment capability is one of: mesh.traffic, mesh.policy, mesh.ingress, mesh.netpol.
- The user names this agent, slug `a23-mesh-network`, or code `MESH`.
- Use when the user runs /a23-mesh-network.

Don't use for: Do not flip strict mTLS or change production mesh without approval.

## Prerequisites

- Swarm root with `agents.json`, `scripts/`, `swarm/`.
- `SWARM_DIR` (default `<git toplevel>/.swarm`, made absolute).
- `python3` and/or `bun` on PATH.
- Signed `task.assign` with task_id and correlation_id.

## How to Run

- `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --json`
- `bun scripts/ts/mesh_policy_check.ts --task-id $TASK --correlation-id $CORR --json`

## Procedure

1. Validate the signed task.assign. Echo task_id and correlation_id.
2. Read upstream artifacts in consumes: cluster.capacity.report, architecture.blueprint, api.contract, cloud.architecture, gitops.manifests.
3. Run the scripts above. Never fabricate JSON.
4. Write only mesh.policy.set, traffic.plan, netpol.coverage.report.
5. Finish with markdown summary + one fenced json `task.result`.

## Pitfalls

- Missing optional binary → `skipped:tool-missing`, do not invent results.
- L3/L4 → BLOCKED needs=human-approval.
- Third gate failure → do not retry; A01 escalates.

## Verification

- Script JSON `status` is ok or fail with findings.
- Final json block matches the prompt `<output_format>`.
- No writes outside this agent's single-writer zone.
