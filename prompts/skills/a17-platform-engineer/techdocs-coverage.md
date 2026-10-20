---
name: a17-platform-engineer--techdocs-coverage
description: Use when catalog entities lack TechDocs or runbook wiring and the job is to rank the gaps and fix annotations so A15 can write the docs.
owner: a17-platform-engineer
tools: [plat_catalog_lint, plat_catalog_graph, plat_maturity_scorecard, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# TechDocs coverage

## When to Use

- The lint or a scorecard shows `docs-missing` or `runbook-missing`, or a task asks "which services have no docs".
- Writing the documentation or the runbook text is A15's work (`runbook-author`, `api-reference`); general descriptor errors are `skill://a17-platform-engineer--catalog-hygiene-sweep`. You wire and rank; you do not write prose.
- Descriptor annotation edits are L2.

## Procedure

1. Run `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --json`. Keep findings with id prefix `docs-missing`, `runbook-missing` and `source-location-missing` (info), their `location`, and `entity_count`. Exit 1 is expected while major gaps exist.
2. From the `scripts/plat_catalog_lint.py` `findings`, compute coverage: for kinds `component`, `api` and `system` the share of entities without a `docs-missing` finding is the TechDocs coverage; runbook coverage counts production components only. Report both as `covered / total`, not a rounded percent.
3. Call `plat_catalog_lint` with the flattened entries (`annotations` as key list, `links` as titles; a link whose title contains `runbook` counts as a runbook, as does an annotation whose last key segment is `runbook`, `runbook-url`, `runbook_url` or `runbook-link`). Its `docs-missing` and `runbook-missing` counts must equal the script's for the same entities, else E-CONTRACT.
4. Call `plat_catalog_graph` with `entries` `[{ref, lifecycle, owner, depends_on}]`. Rank the gaps: `tier-0` and `tier-1` first (a runbook gap is major there), then by `most_depended_on[].transitive_dependents`, then by lifecycle (`production` before `experimental`).
5. For each ranked entity, `glob` for `mkdocs.yml` and `docs/index.md` beside the descriptor. If present, `edit` the descriptor to add `backstage.io/techdocs-ref: dir:.`; if the docs live elsewhere use `url:<repo-url>` only when the task supplies it. If neither exists, the entity goes to A15 as a docs request: do not create a stub here.
6. For a missing runbook, add `metadata.links` with a title containing `runbook` only when a runbook file already exists (`grep` for it); never point a link at a file that does not exist.
7. Call `plat_maturity_scorecard` for the touched components with `has_runbook` set from the rerun lint and the other facts from scorecard evidence: the change is meaningful only if a service moves up a level or its `gaps` loses `runbook`. State the before and after levels.
8. Re-run the script after the edits and compare the counts; call `shared_artifact_digest` with `{path}` for each edited descriptor.

## Outputs

- `service.catalog.entry` updates (annotations), the ranked docs-request list for A15, coverage before and after.

## Failure Handling

- Script exit 2: fix `--path`, else BLOCKED `needs=input`. `skipped:tool-missing` for YAML without PyYAML: that file stays unverified.
- INVALID_INPUT on `plat_maturity_scorecard` (coverage outside 0 to 100, duplicate service): fix once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- PARTIAL_SUCCESS: use `completed_fraction`.
- `backstage.io/techdocs-ref` value forms (`dir:`, `url:`) follow the Backstage docs; if the deployment uses a different builder, defer to its docs.

## Handover

`to_agent`: `A15`. Fill `goal` (write the missing docs and runbooks in ranked order), `completed` (annotations wired), `remaining` (docs requests with entity refs), `artifacts` (descriptors with digests, producer `A17`), `open_questions`, `verification`. End with `shared_handover_validate`, then `shared_handover_write`.
