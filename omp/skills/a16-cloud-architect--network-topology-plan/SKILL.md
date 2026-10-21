---
name: "a16-cloud-architect--network-topology-plan"
description: "Use when VPCs, clusters and regions need a non-overlapping CIDR allocation, hub-and-spoke or mesh choice and a latency check before anything is built."
disable-model-invocation: true
---

# Network topology plan

## When to Use

- A `cloud.architecture` task needs the address plan and connectivity model (peering, transit hub, VPN, service mesh boundary) for several networks that will coexist.
- Not for the account and OU layout (`skill://a16-cloud-architect--landing-zone-design`) and not for configuring routers or meshes: A11 writes the IaC, A23 owns the mesh and network policy.
- Autonomy: the plan is L2. Renumbering a live network or connecting production to a new provider is L3 (BLOCKED `needs=human-approval`). Circuit or carrier commitments are L4.

## Procedure

1. Call `shared_handover_read` with `{task_id}`; take the existing allocations (from the landing zone spec or the platform team), regions, environments and which networks must talk to each other. An allocation inventory that is "somewhere in a wiki" is BLOCKED via `shared_escalate` `reason_code: "ambiguous-input"`.
2. Use `read` and `grep` on the allocation inventory and IaC to list leaf allocations only (never a VPC and its own subnets together): `name`, `cidr` (no host bits set), `routable` (reachable over peering, VPN or transit), `group` (same cluster or environment: members may never overlap) and `required_addresses` (nodes, pods, services, load balancers). Kubernetes pod and service ranges are separate entries; 100.64.0.0/10 (RFC 6598) is a common non-routable pod range.
3. Call `cloud_cidr_plan` with `networks`, `reserved_addresses` (5 per subnet is the AWS and Azure behaviour, Google Cloud reserves 4; take the provider's number from the task) and `headroom_pct` from the task; when it states none use 100 for pod ranges and 50 for the rest and write that down as this skill's default.
4. Read `overlap_count`, `undersized_count`, `findings`, `findings_truncated`, `verdict`. `overlap:*` between two routable networks is a blocker: renumber one of them; between members of one `group` it is major. Overlaps between unrouted networks of different groups are not reported at all: say which pairs you rely on never routing. IPv6 global-unicast ranges (2000::/3) are not flagged as `public-range`. `undersized:*` (major): enlarge the prefix. `public-range:*` (minor): a non-private range inside a private network needs an explicit reason. Accept only `pass` or `warn`.
5. When `findings_truncated` is true (more than 100 findings) call `cloud_cidr_plan` again per `group` and merge the results; never accept a truncated `pass`.
6. Choose the connectivity model from the evidence: more than two routable networks that all talk to each other points to a transit hub; two networks with one flow point to peering (transitive routing is not provided by peering on the major providers; confirm with the provider documentation named in the task). Record the choice and the rejected alternative.
7. Call `cloud_latency_budget` for the longest cross-network request path: one hop per network crossing (hub, firewall or inspection appliance, VPN, inter-region link) with measured `p50_ms`, `p99_ms` and `calls`. `meets_target` false or `dominant-stage:*` on an inspection hop means that hop moves out of the hot path or the service moves to the same network. Unmeasured hops are `needs=input`.
8. Write the plan with `write` at the task path, else `docs/cloud/network-plan.md`: allocation table, free space kept per region for growth, connectivity diagram in text, the step-4 and step-7 results, and the renumbering approvals needed (L3). Call `shared_artifact_digest`, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `cloud.architecture` network section: allocation table, connectivity model, latency evidence.
- `landing.zone.spec` network annex when a hub or shared-services network is introduced.

## Failure Handling

- `INVALID_INPUT` on a `cidr` (host bits set, invalid syntax): fix the entry; never "round" a CIDR silently.
- `PARTIAL_SUCCESS`: use `completed_fraction`. `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`.
- Provider limits (maximum VPC prefix, route table size, peering quotas) differ and change: cite the task's figure or leave `open_questions`; do not recite from memory.
- An address plan disagreeing with live inventory is E-CONTRACT; the inventory wins until a human confirms.

## Handover

`to_agent` A23 (mesh and network policy); a second write to A11 (IaC from the allocation table). Fill `goal`, `completed`, `decisions`, `artifacts` (kind `cloud.architecture`, digest, producer A16), `blockers` (L3 renumbering), `open_questions`, `verification`. End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a16-cloud-architect/network-topology-plan.md; edit the source and run scripts/build_agents.py -->
