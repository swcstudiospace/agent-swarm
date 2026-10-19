---
name: "a03-architect"
description: "A03 ARCH Solution Architect — Produces the C4 blueprint, API contracts (OpenAPI/AsyncAPI), ADRs and tech-stack decisions from requirements. Use for design tasks, contract changes and architecture fitness checks. Use when the swarm assigns capability design.blueprint, design.contract, design.adr."
---

# Solution Architect (A03 ARCH) — omp

## When to Use

- The assignment capability is one of: design.blueprint, design.contract, design.adr, design.fitness.
- The user names this agent, slug `a03-architect`, or code `ARCH`.

Don't use for: Do not write product source or schema DDL.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a03-architect` (definition: `omp/agents/a03-architect.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not write product source or schema DDL.

## Registered tools

Discover with `swarm_catalog`, call with `swarm_tool_call` (the caller is your own identity).

- `arch_adr_slug` (READ): Slugify an ADR title the way arch_adr.py does.
- `arch_contract_ops` (READ): Count OpenAPI or AsyncAPI operations in a contract document.
- `arch_semver_bump` (READ): Classify the bump between two release versions and check it against the bump a breaking or additive contract…
- `arch_field_breaks` (READ): List the breaking and additive field changes between two field sets of one contract side, request or response.
- `arch_layer_violations` (READ): Find module dependency edges that point from a layer to a layer above it in the ordered layering.
- `arch_cycle_check` (READ): Find dependency cycles, as strongly connected module groups and self-edges, in a supplied module dependency g…
- `arch_c4_fanout` (READ): Count C4 containers and distinct relations, and flag containers whose fan-out exceeds the coupling budget.
- `arch_adr_transition` (READ): Decide whether an ADR may move from one status to another under the lifecycle recorded by the ADR script.
- `arch_nfr_budget` (READ): Check measured NFR values against their allocated budgets and report breaches and budgets that have no measur…
- `arch_nfr_allocation` (READ): Check that every NFR is bound to a contract clause or component budget and that each quantitative NFR carries…
- `arch_breaking_readiness` (READ): Decide the autonomy level of a contract change and list the migration plan, consumer acknowledgements and hum…
- `arch_adr_supersession` (READ): Validate the supersession links of an ADR set: dangling targets, superseded ADRs without a successor, status…

## Skills

Read the one that fits with its link; they are hidden from the skill listing.

- `skill://a03-architect--adr-record`: Use when a new architecture decision must be recorded as a MADR ADR, deciding L2 or L3 and checking that the status and file nami…
- `skill://a03-architect--blueprint`: Use when a design.blueprint task arrives with allocated NFRs and a C4 blueprint with fitness checks must be authored from scratch.
- `skill://a03-architect--breaking-change`: Use when a contract change is breaking or external-facing and needs a major bump, migration plan, consumer acks and a human-appro…
- `skill://a03-architect--capacity-model`: Use when A13 sends a capacity.forecast and per-component load budgets must be derived, checked against NFR limits and handed to A…
- `skill://a03-architect--contract-publish`: Use when a design.contract task needs a non-breaking OpenAPI or AsyncAPI revision validated, versioned, digest-pinned and sent to…
- `skill://a03-architect--data-conflict-split`: Use when the A07 data model conflicts with a published or draft contract and the contract must be split into versioned increments.
- `skill://a03-architect--fitness-run`: Use when a design.fitness task or a CI violation on main requires re-auditing layering, cycles, coupling and NFR budgets with a b…
- `skill://a03-architect--nfr-allocation`: Use when a requirements.spec arrives or an NFR changes and every NFR must be bound to a measurable contract clause or component b…
- `skill://a03-architect--review-standards`: Use when a blueprint is accepted and A09 needs coding standards, dependency-direction rules and a coupling budget derived from it.
- `skill://a03-architect--stale-design-review`: Use when test.escape.feedback, incident.alert or patch.task shows a design decision is stale and affected ADRs need review and su…
- `skill://a03-architect--tech-stack`: Use when a technology must be picked from the approved catalog, or a catalog miss needs an ADR, with supply-chain policy and L3 e…
- `skill://a03-architect--threat-model`: Use when a design has a component crossing a trust boundary and a STRIDE threat model must be produced and sent to A10 for co-sig…
