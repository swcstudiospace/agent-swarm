---
name: a17-platform-engineer--service-onboarding
description: Use when a platform.catalog task onboards one new service and needs a linted service.catalog.entry with owner, tier, dependencies and docs for A15.
owner: a17-platform-engineer
tools: [plat_catalog_lint, plat_catalog_graph, plat_maturity_scorecard, plat_golden_path_coverage, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Service onboarding

## When to Use

- A `platform.catalog` task names one service (repository plus a `build.artifact` from A11) that has no `catalog-info.yaml` yet, or a broken one.
- Repo-wide cleanup is `skill://a17-platform-engineer--catalog-hygiene-sweep`; writing the TechDocs content is A15's work, you only wire the annotation (`skill://a17-platform-engineer--techdocs-coverage`).
- Writing catalog descriptors is L2. You never edit the service's pipeline files.

## Procedure

1. `read` the service repository and the `build.artifact` (A11) to learn name, language, entry points and the repository URL. `grep` the repo for `catalog-info`, ignoring letter case, to be sure no descriptor exists. The owner, tier and system come from the task; an unknown owner is BLOCKED `needs=input`, never a guess.
2. `write` `catalog-info.yaml`: `apiVersion: backstage.io/v1alpha1`, `kind: Component`, `metadata.name` (1-63 chars, letters or digits at both ends, `-`, `_` and `.` allowed inside; leave `metadata.namespace` out unless the task names one: lowercase letters, digits and `-` only), `metadata.labels.tier` (`tier-0` to `tier-3`), annotations `backstage.io/techdocs-ref` and `backstage.io/source-location`, `spec.type`, `spec.lifecycle`, `spec.owner` (`group:<team>`), `spec.system`, `spec.dependsOn` (a bare name resolves to a Component in the entity's own namespace). Start `lifecycle` at `experimental`; `production` only after step 6.
3. Run `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <service-dir> --json`. Exit 0 means no major or blocker; exit 1 lists them in `findings` with `location`; exit 2 is E-INPUT (fix `--path`). Check `skipped` is empty and `status` is `ok`: a skipped input was not linted and makes the status `fail`.
4. Call `plat_catalog_lint` with the same entity as one `entries` item (fields `kind, name, namespace, type, lifecycle, owner, system, tier, description, depends_on, annotations, links`; `annotations` holds keys only). `entry_count` must equal the script `entity_count` for that file, and every tool finding id must appear in the script output (an entity whose kind, name or namespace is invalid is addressed as `entry#<position>`, which differs between the tool call and the script: compare the rule names for those). A mismatch is E-CONTRACT: report both, state no verdict.
5. Call `plat_catalog_graph` with `entries` `[{ref, lifecycle, owner, depends_on}]` for the new component plus its declared neighbours. Required: `cycle_count` 0, `unowned_count` 0, and no `deprecated-dependency` or `experimental-dependency` finding (major for a production component). `dangling-dependency` (minor) means a neighbour is not catalogued yet: onboard it first or list it in `open_questions`.
6. Call `plat_maturity_scorecard` with one `services` item (`has_owner`, `has_runbook`, `has_slo`, `has_on_call`, `test_coverage_pct`, `sast`, `dependency_scan`, `secret_scan` from evidence, never assumed) and `tier`. Set `lifecycle: production` only when `services[0].meets_required` is true; otherwise keep `experimental` and put its `gaps` in `remaining`.
7. Call `plat_golden_path_coverage` with the service's `template` and `template_version`; an `off-path` finding is recorded in `decisions`, not fixed here.
8. Call `shared_artifact_digest` with `{path}` for `catalog-info.yaml`; hand over.

## Outputs

- `service.catalog.entry`: descriptor, script summary, tool counts, graph and scorecard result.

## Failure Handling

- INVALID_INPUT from `plat_catalog_lint` (blank `kind` or `name`): fix that field once, else BLOCKED `needs=input`.
- `plat_catalog_graph` INVALID_INPUT on a malformed `ref`: correct it to `[kind:][namespace/]name`.
- DEPENDENCY_UNAVAILABLE on any tool: retry once, then `skipped:tool-missing`, and do not claim `production`.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked entities.
- Owner changes or another team's entity edits: BLOCKED `needs=human-approval`.

## Handover

`to_agent`: `A15`, who documents it. Fill `goal` (document the service), `completed`, `decisions` (lifecycle chosen and why, from `meets_required`), `artifacts` (descriptor with `shared_artifact_digest` digest, producer `A17`), `open_questions` (dangling neighbours, missing evidence), `verification` (`verified` only when script and tool agreed). End with `shared_handover_validate`, then `shared_handover_write`.
