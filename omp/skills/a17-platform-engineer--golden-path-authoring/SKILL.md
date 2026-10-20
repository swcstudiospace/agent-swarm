---
name: "a17-platform-engineer--golden-path-authoring"
description: "Use when a platform.golden_path task needs a new or revised scaffolder template as a golden.path.template matching the golden-path contract."
disable-model-invocation: true
---

# Golden-path authoring

## When to Use

- A `platform.golden_path` task needs a Backstage scaffolder Template (`apiVersion: scaffolder.backstage.io/v1beta3`, `kind: Template`) for one stack, built from a `cloud.architecture` (A16) and a list of required inputs.
- Onboarding a concrete service is `skill://a17-platform-engineer--service-onboarding`; changing an existing XRD is `skill://a17-platform-engineer--xrd-composition-change`. Pipelines the template references are A11's: never edit them here.
- Catalog and template files are L2 (`autonomy_ceiling.catalog`); you never publish to a shared registry.

## Procedure

1. `read` the `cloud.architecture` and the stack's existing template, if any. Write the golden-path input list: one `{name, type, required, allowed_values, default}` per input (type is `string|integer|number|boolean|array|object`). Every input comes from the architecture or the task; invent none.
2. `edit` or `write` `template.yaml` (`spec.parameters`, `spec.steps`) and the skeleton directory. Placeholders such as `${{ values.name }}` stay in the skeleton only.
3. Call `plat_template_params_check` with `template_parameters` (`{name, type, required, default, enum_values}` read from `spec.parameters`) and `golden_inputs` from step 1. `verdict` must be `compatible`: any `input-missing` (blocker), `type-mismatch`, `required-not-enforced`, `unrestricted-values`, `values-not-allowed` or `default-not-allowed` (all major) is fixed in the template, never waived. `default-differs` (minor) and `extra-parameter` (info) are recorded as decisions.
4. Render the skeleton with sample values into a scratch directory with `bash` (a plain substitution, no network), then run `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <scratch-dir> --json`. The generated `catalog-info.yaml` must give `status: ok` (exit 0). Exit 1 names the rule in `findings[].id`; exit 2 is E-INPUT.
5. Call `plat_catalog_lint` with the rendered component flattened into `entries` (`kind, name, namespace, type, lifecycle, owner, system, tier, description, depends_on, annotations, links`). Its `blocker_count` plus `major_count` must be 0, and it must agree with the script on the same entity. The script adds apiVersion and graph checks, so the script may report more but never fewer; a rule only the tool reports is E-CONTRACT.
6. Call `plat_golden_path_coverage` with `golden_paths` `[{id, latest_version, min_supported_version}]` for the new version (semver `MAJOR.MINOR.PATCH`; raise `min_supported_version` only for a breaking template change) and the current `services` (`{name, tier, template, template_version}`), `target_ratio` from the task. Report `outdated_count` and `unsupported_count`: they are the migration load of this release.
7. Raising `min_supported_version` above a version still in use makes `unsupported_count` positive: that is a platform API change: call `shared_escalate` with `reason_code` `policy`, `needs` `["human-approval"]` and follow `skill://a17-platform-engineer--platform-api-versioning`.
8. Call `shared_artifact_digest` with `{path}` for `template.yaml` and the skeleton index, then hand over.

## Outputs

- `golden.path.template`: template file, skeleton, the contract inputs, and `verdict`, script `entity_count`, coverage figures as evidence.

## Failure Handling

- `plat_template_params_check` INVALID_INPUT (duplicate or blank `name`): fix the named list once, else BLOCKED `needs=input`.
- Script exit 2 or `skipped:tool-missing:<file>` (no PyYAML for a YAML descriptor): `skipped:tool-missing`, validate with the tool call only and say the script gap.
- Any tool DEPENDENCY_UNAVAILABLE: retry once, then record `skipped:tool-missing`; never state `compatible` unchecked.
- PARTIAL_SUCCESS: use `completed_fraction`, name the unchecked inputs.

## Handover

`to_agent`: `A05` (the template owner's implementers; A19 consumes it too when the stack is event-driven). Fill `goal`, `completed` (checks run), `decisions` (each `default-differs`), `artifacts` (template and skeleton with `shared_artifact_digest` digests, producer `A17`), `open_questions`, `verification` (verified only if tool and script agreed). End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a17-platform-engineer/golden-path-authoring.md; edit the source and run scripts/build_agents.py -->
