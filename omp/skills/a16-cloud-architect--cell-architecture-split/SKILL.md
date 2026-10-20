---
name: "a16-cloud-architect--cell-architecture-split"
description: "Use when a service must be split into isolated cells so one failure hits only a bounded share of traffic; sizes cells, spares and per-cell quorum."
disable-model-invocation: true
---

# Cell architecture split

## When to Use

- A `cloud.resilience` task asks to bound the blast radius of a stateful or multi-tenant service by partitioning it into cells (independent full stacks behind a thin router), or to resize an existing cell fleet.
- Not for choosing a DR strategy (`skill://a16-cloud-architect--dr-strategy-selection`) and not for a generic resilience pattern choice (`skill://a16-cloud-architect--resilience-pattern-catalog`).
- Autonomy: the design is L2. Changing the cell count or tenant placement of a production fleet is L3 (BLOCKED `needs=human-approval`); moving tenant data is A07 work. Deleting a cell is L4.

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the blueprint and `slo.manifest`. Take the partition key (tenant, account, shard), the peak `load_rps`, the growth plan and the blast-radius limit. No partition key or no limit stated is BLOCKED via `shared_escalate` `reason_code: "ambiguous-input"`.
2. Obtain `cell_max_rps`: the load-tested maximum of one cell, supplied by the task or an A25 test result. An untested vendor figure is marked "unverified" and the design stays `proposed`.
3. Call `cloud_cell_capacity` with `load_rps`, `cell_max_rps`, `target_utilisation_pct`, `growth_pct`, `spare_cells` (at least 1) and `current_cells` (0 for a new design). Read `usable_rps_per_cell`, `projected_load_rps`, `cells_for_load`, `cells_required`, `headroom_pct`, `blast_radius_pct`, `shortfall_cells`, `current_cells_sufficient`. `cells-short` is major, `single-cell` and `no-spare-cell` are minor findings; this skill treats `no-spare-cell` as a failure because losing one cell then exceeds the utilisation ceiling.
4. Check `blast_radius_pct` against the task limit. Above it, split into smaller cells (lower `cell_max_rps` per cell, more cells) and call the tool again; do not raise `target_utilisation_pct` to pass.
5. Call `cloud_blast_radius` with `services` placed per cell (`instances[].cell` set to an id that is unique across regions, because the tool treats one id in two regions as one cell; the router and any shared service listed with `depends_on` edges) and `max_az_blast_pct` equal to the limit. `worst_blast_pct` for a `cell` scope must not exceed `blast_radius_pct` by more than rounding: if it does, a hard dependency crosses cells (shared database, global queue). Every service in `spof_services` is either made per-cell or documented as a deliberate shared tier with its own availability target.
6. For each cell's datastore call `cloud_replica_placement` with `placements` (replicas per `az`), `quorum_mode: "majority"` and `tolerate_az_failures: 1`. `survives_az_failures` must be true; `even-replica-count` (minor) and `min_azs_for_tolerance` tell the spread (3 AZs for 3 or 5 replicas). A cell confined to one AZ is a zonal cell: say so and compare its `blast_radius_pct` with the AZ share.
7. Write the cell design with `write` at the task path, else `docs/cloud/cell-design.md`: partition key and router rule (deterministic, no cross-cell calls), table from step 3, blast results, rebalancing and drain procedure for A25 to test, and which items are L3.
8. Call `shared_escalate` with `reason_code: "policy"`, `needs: ["human-approval"]` when production cell count or placement changes (L3). Call `shared_artifact_digest` on the doc, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `resilience.design`: cell count, spares, blast radius, router and partition rule, per-cell quorum evidence.

## Failure Handling

- `INVALID_INPUT` (for example `cell_max_rps` below 1): fix the field or BLOCKED `needs=input`.
- `PARTIAL_SUCCESS`: use `completed_fraction`; name unplaced services.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`.
- `cloud_cell_capacity` and `cloud_blast_radius` giving different per-cell shares is E-CONTRACT: print both, change nothing.
- Never retry an L3 escalation.
- Router design details (consistent hashing versus directory lookup) are a choice for A03; give the trade-off, do not edit A03 contracts.

## Handover

`to_agent` A25 (chaos and drain tests); a second write to A13 (per-cell SLO and alerts). Fill `goal`, `completed`, `decisions` (cell count, spares, limit), `artifacts` (kind `resilience.design`, digest, producer A16), `open_questions` (unverified `cell_max_rps`), `verification`. End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a16-cloud-architect/cell-architecture-split.md; edit the source and run scripts/build_agents.py -->
