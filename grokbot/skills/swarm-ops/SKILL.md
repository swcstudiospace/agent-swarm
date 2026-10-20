---
name: swarm-ops
description: >
  Grok Bot lane ops for a11-devops, a12-release, a13-observability, a17-platform-engineer, a21-cluster-ops, a22-gitops, a23-mesh-network, a25-resilience, a28-chain-infra, a30-custody-compliance.
  Use when Desk Lead routes work to this lane of the 30 AgentSwarm roles.
disable-model-invocation: false
---

# Swarm lane ops

Generated from agents.json and the role prompts. Do not edit by hand.
Regenerate with `python3 scripts/build_agents.py`.

## When to Use

- The assignment belongs to lane `ops`.
- The role slug is one of: a11-devops, a12-release, a13-observability, a17-platform-engineer, a21-cluster-ops, a22-gitops, a23-mesh-network, a25-resilience, a28-chain-infra, a30-custody-compliance.

## Procedure

1. Read the role prompt at `$SWARM_ROOT/<prompt>` below. `$SWARM_ROOT` is the pinned agent-swarm checkout. The target repo does not contain these prompts. The swarm has exactly these 30 roles.
2. Route the change with `grokbot/swarm/seat-map.json`. A routing rule wins over role path globs. Among role globs, the longest match wins, and an equal length goes to the lowest role id. Android paths go to bot-03, iOS paths to bot-04, and desktop shells to bot-02.
3. Run that role's scripts from the pinned checkout: `python3 "$SWARM_ROOT/scripts/<script>.py" --root <target repo> --json` (the bun twin is `bun "$SWARM_ROOT/scripts/ts/<script>.ts" --root <target repo> --json`). `<target repo>` is the git toplevel being edited. Do not run a `scripts/` path from the target tree. Stay inside the role's path globs and autonomy ceiling.
4. Verify with the seat's verification tools from the seat map.
5. Finish with a task.result. A keyless cloud session is advisory. Leave the pull request in draft.

## Roles

### A11 DEVOPS (a11-devops)

- Prompt: `$SWARM_ROOT/prompts/A11-devops.md`
- Role: Senior DevOps / Platform Engineer (A11 DEVOPS, slug a11-devops) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A11-devops.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for iac.change, ci.pipeline, build.artifact, environment.record.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: non_prod=L2, prod_infra=L3.
- Scripts: `$SWARM_ROOT/scripts/devops_ci_check.py`, `$SWARM_ROOT/scripts/devops_build_record.py`.

### A12 REL (a12-release)

- Prompt: `$SWARM_ROOT/prompts/A12-release.md`
- Role: Senior Release Manager (A12 REL, slug a12-release) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A12-release.md, agents.json, and the scripts and registry tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for release.plan, promote/rollback commands, and release gate.verdict.
- Home: `cloud`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: canary=L2, prod_high_risk=L4, rollback=L2.
- Scripts: `$SWARM_ROOT/scripts/rel_plan.py`.

### A13 OBS (a13-observability)

- Prompt: `$SWARM_ROOT/prompts/A13-observability.md`
- Role: Senior Observability / SRE (A13 OBS, slug a13-observability) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A13-observability.md, agents.json, and the scripts and tools listed in &lt;tools&gt;. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for slo.manifest, incident.alert, deploy.telemetry.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: alerting=L2, incident_declare=L2, page_humans=L2.
- Scripts: `$SWARM_ROOT/scripts/obs_slo.py`.

### A17 PLAT (a17-platform-engineer)

- Prompt: `$SWARM_ROOT/prompts/A17-platform-engineer.md`
- Role: Senior Platform Engineer (A17 PLAT, slug a17-platform-engineer) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A17-platform-engineer.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for service.catalog.entry, golden.path.template, platform.claim, platform.scorecard.
- Home: `executor`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: catalog=L2, platform_api_breaking_change=L3, tenant_quota_change=L3.
- Scripts: `$SWARM_ROOT/scripts/plat_catalog_lint.py`.

### A21 K8S (a21-cluster-ops)

- Prompt: `$SWARM_ROOT/prompts/A21-cluster-ops.md`
- Role: Senior Kubernetes Cluster Operator (A21 K8S, slug a21-cluster-ops) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A21-cluster-ops.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for cluster.capacity.report, cluster.upgrade.plan, cluster.fleet.status.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: cluster_delete=L4, non_prod_cluster=L2, prod_cluster_change=L3.
- Scripts: `$SWARM_ROOT/scripts/k8s_manifest_check.py`.

### A22 GITOPS (a22-gitops)

- Prompt: `$SWARM_ROOT/prompts/A22-gitops.md`
- Role: Senior GitOps &amp; Progressive Delivery Engineer (A22 GITOPS, slug a22-gitops) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A22-gitops.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for gitops.manifests, rollout.plan, promotion.record, drift.report.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: non_prod_sync=L2, prod_promotion=L3, prod_prune=L4.
- Scripts: `$SWARM_ROOT/scripts/gitops_repo_check.py`.

### A23 MESH (a23-mesh-network)

- Prompt: `$SWARM_ROOT/prompts/A23-mesh-network.md`
- Role: Senior Service Mesh &amp; Networking Engineer (A23 MESH, slug a23-mesh-network) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A23-mesh-network.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for mesh.policy.set, traffic.plan, netpol.coverage.report.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: mtls_strict_flip=L3, policy_design=L2, prod_mesh_change=L3.
- Scripts: `$SWARM_ROOT/scripts/mesh_policy_check.py`.

### A25 RESIL (a25-resilience)

- Prompt: `$SWARM_ROOT/prompts/A25-resilience.md`
- Role: Senior Resilience &amp; Chaos Engineer (A25 RESIL, slug a25-resilience) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A25-resilience.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for resilience.report, chaos.plan, backup.coverage.report, dr.runbook.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: dr_failover_prod=L4, experiment_prod=L3, experiment_staging=L2.
- Scripts: `$SWARM_ROOT/scripts/resil_manifest_check.py`.

### A28 CHAIN (a28-chain-infra)

- Prompt: `$SWARM_ROOT/prompts/A28-chain-infra.md`
- Role: Senior Blockchain Infrastructure Engineer (A28 CHAIN, slug a28-chain-infra) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A28-chain-infra.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for chain.infra.plan, rpc.policy, indexer.spec.
- Home: `bot-05-infrastructure`. Seat: `bot-05-infrastructure`.
- Autonomy ceiling: mainnet_node_change=L3, testnet_ops=L2, validator_key_ops=L4.
- Scripts: `$SWARM_ROOT/scripts/chain_config_check.py`.

### A30 CUSTODY (a30-custody-compliance)

- Prompt: `$SWARM_ROOT/prompts/A30-custody-compliance.md`
- Role: Senior Web3 Custody &amp; Compliance Engineer (A30 CUSTODY, slug a30-custody-compliance) in AgentSwarm. Execute the assignment using repository evidence from 03-agents/A30-custody-compliance.md, agents.json, the scripts listed in &lt;tools&gt;, and the registry tools in TOOL_INVENTORY. Never invent paths, libraries, or APIs that are not in those sources or the target repo. Fail closed. You are the single-writer for custody.policy.set, custody.evidence.pack, key.ceremony.plan.
- Home: `executor`. Seat: `bot-06-quality-security`.
- Autonomy ceiling: fund_moving=L4, key_ceremony=L4, policy_design=L2, signer_change=L3.
- Scripts: `$SWARM_ROOT/scripts/custody_policy_check.py`.
