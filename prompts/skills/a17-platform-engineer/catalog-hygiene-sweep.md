---
name: a17-platform-engineer--catalog-hygiene-sweep
description: Use when a sweep must find unowned, orphaned, cyclic or stale catalog entities across a repository and fix the descriptors that are safe to fix.
owner: a17-platform-engineer
tools: [plat_catalog_lint, plat_catalog_graph, shared_escalate, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Catalog hygiene sweep

## When to Use

- A scheduled or on-demand `platform.catalog` sweep over every `catalog-info` descriptor under a repository.
- One new service is `skill://a17-platform-engineer--service-onboarding`; missing docs and runbooks only is `skill://a17-platform-engineer--techdocs-coverage`.
- Fixing descriptor syntax and metadata is L2. Changing an owner, moving an entity between systems or removing an entity is not yours to decide: BLOCKED `needs=human-approval`.

## Procedure

1. Run `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --json` (add `--tiers` if the house tiers differ from `tier-0,tier-1,tier-2,tier-3`). Descriptor names match `catalog-info[.<tag>].yaml|yml|json` in any letter case. Record `status`, `scanned_files`, `entity_count`, `finding_count`, `skipped`, `truncated`. Exit 0 is ok, 1 means a major or blocker exists, 2 is E-INPUT.
2. In the `scripts/plat_catalog_lint.py` output, `truncated` true means more than 2000 descriptors or the file bound was hit; `graph-skipped` (info) means more than 300 entities, so the script ran no graph check. Each skipped input, truncation and skipped graph check adds one major `scan/incomplete` finding, so `status` is `fail`. In both cases the sweep is partial: split by `--path` per directory and say so. A `skipped:tool-missing:<file>` entry means PyYAML is absent for that file.
3. With `eval`, group `findings` by id prefix: blockers first (`parse-error`, `owner-missing`, `lifecycle-missing`, `name-invalid`, `duplicate-entity`, `dependency-cycle`), then major (`apiversion-missing`, `descriptor-invalid`, `scan/incomplete`, `system-missing` or `docs-missing` on production, `owner-invalid`, `dependson-invalid` (a `dependsOn` that is not a list of strings adds no graph edge)), then minor.
4. Call `plat_catalog_graph` with `entries` `[{ref, lifecycle, owner, depends_on}]` from the descriptors (the same set the script read; `ref` is the full `kind:namespace/name`, so a bare `dependsOn` name resolves in that namespace). Cross-check against the script: `cycle_count` equals the script's `dependency-cycle` plus `self-dependency` findings, and the script's `dangling-dependency` count equals `dangling_count`; a difference is E-CONTRACT (the script uses only well-formed references).
5. Read `orphan_count` (component or resource with no edge in either direction, minor), `unowned_count` (major), `most_depended_on` (top five by `transitive_dependents`), `max_depth`. A `deprecated-dependency` or `experimental-dependency` from a production entity is major: name the dependent and the target.
6. Call `plat_catalog_lint` with the entries that have findings, after your edits, to prove the fix: `blocker_count` and `major_count` must be 0 for fixed entities. Safe `edit` fixes: name or namespace casing, `apiVersion: backstage.io/v1alpha1`, a duplicate `dependsOn` line, an annotation key typo. Never invent an owner, system or tier.
7. For findings you may not fix (`owner-missing`, `unowned`, cycles, orphans, a deprecated dependency), call `shared_escalate` with `reason_code` `ambiguous-input`, the entity refs in `summary`, and `needs` `["owner decision"]`; unfixed findings stay listed.
8. Re-run the script, compare `finding_count` before and after, `write` the diff summary and call `shared_artifact_digest` with `{path}` for every changed descriptor.

## Outputs

- `service.catalog.entry` updates, a before and after finding table, the list of unowned or cyclic entities.

## Failure Handling

- Script exit 2 (path outside the root or missing): fix `--path`, else BLOCKED `needs=input`.
- `parse-error` blocker: fix only an obvious syntax slip; otherwise return the file to its owner.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; report the sweep as partial.
- PARTIAL_SUCCESS: use `completed_fraction`.

## Handover

`to_agent`: `A15` for docs of the touched entities, or the assigner. Fill `goal`, `completed` (counts before and after), `remaining` (human decisions), `decisions`, `artifacts` (changed descriptors with digests, producer `A17`), `open_questions` (entities with no owner), `verification` (`partial` if truncated). End with `shared_handover_validate`, then `shared_handover_write`.
