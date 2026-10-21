---
name: "a17-platform-engineer--xrd-composition-change"
description: "Use when a platform.composition task edits a composite resource schema (XRD) and needs the breaking or additive verdict plus claims proven on the new schema."
disable-model-invocation: true
---

# XRD composition change

## When to Use

- A `platform.composition` task changes the schema of a Crossplane-style composite resource definition (`spec.versions[].schema.openAPIV3Schema`) that claims are written against.
- Planning the version and deprecation timetable of a breaking change is `skill://a17-platform-engineer--platform-api-versioning`. Applying the XRD to a cluster is A11 or A21; this skill never applies anything.
- An additive change is L2; a breaking change is L3 (`platform_api_breaking_change`).

## Procedure

1. `read` the old and the new XRD. Flatten each schema into fields `{path, type, required, enum_values, minimum, maximum, min_length, max_length}`: `path` is dot-separated (`spec.parameters.size`), `type` one of `string|integer|number|boolean|array|object`. A field is `required` only when its parent object lists it.
2. Call `plat_composition_diff` with `old_version`, `new_version` (the served version names, for example `v1alpha1`), `old_fields`, `new_fields`. Read `verdict`, `breaking_count`, `additive_count`, `requires_new_version`. Major findings are `field-added-required`, `field-removed`, `type-changed`, `required-added`, `enum-added`, `enum-values-removed` and any `minimum|maximum|min-length|max-length` tightening. A required field under a parent object that is itself new and optional is `field-added-under-optional-parent` (info, not breaking): `required` binds only when the parent is set. Parents you leave out of the flattened list count as existing, so list new parent objects (`type: object`) explicitly. Findings name counts, not the values involved: compare the two enum lists yourself.
3. `plat_composition_diff` `verdict` `breaking-same-version` carries a `breaking-change-in-same-version` blocker: stop, the served version cannot be edited in place. Create a new version name for the new schema and rerun step 2 with it; the old version stays served. Whether the cluster's Crossplane release can convert between versions is not decided here; defer to its documentation.
4. Collect the real claims from the consuming repositories with `grep` for the composite `kind`, flattened to `{path, type, value}` (value is always text, `true` or `false` for booleans). A claim value that is a credential (password, token, key, connection string) is never copied into the call or any file: use the name of its secret reference, and report the field as "secret reference". Call `plat_claim_validate` with `schema` = `new_fields` and one `claim` per example. `valid` false or `violation_count` above 0 lists the claims the change breaks (`required-missing`, `enum-violation`, `type-mismatch`, `below-minimum`, `above-maximum`, `unknown-field`); findings never repeat the claim value.
5. Cross-check `plat_composition_diff` against `plat_claim_validate`: every diff finding of class `field-added-required`, `enum-values-removed` or tightened bounds should produce at least one claim violation when the corpus uses that field. A major diff finding that no claim exercises is reported as "not covered by the corpus", never as "safe".
6. `breaking_count` above 0 (or `requires_new_version` true): call `plat_api_deprecation` with `checked_at` (ISO-8601 with offset), the old API as `state: deprecated` with its `stability` (`alpha|beta|ga`), `active_consumers` from step 4, and a proposed `deprecated_at` and `sunset_at`. `window-too-short` or `sunset-passed-with-consumers` blocks the plan.
7. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "breaking XRD change awaiting L3 approval", needs: ["human-approval"]}` and return its `result_patch`. Additive-only (`change_class` `additive` or `none`, all claims `valid`) proceeds at L2.
8. `write` the example claims that validate against the new schema; call `shared_artifact_digest` with `{path}` for them and for the flattened field files.

## Outputs

- `platform.claim`: validated example claims plus the diff verdict, counts and the claim-violation list.

## Failure Handling

- INVALID_INPUT on a duplicate or malformed `path`, a claim `value` that is not numeric for an `integer` or `number` type, or a numeric `value` whose decimal exponent is beyond ±100 (for example `1e999999999`): fix the named list once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never print `compatible`.
- PARTIAL_SUCCESS: use `completed_fraction`, name unchecked claims.
- No claims found: say so; the verdict then rests on the diff alone.

## Handover

`to_agent`: `A11` (applies the XRD through its pipeline) with `A21` as the cluster consumer. Fill `goal`, `decisions` (verdict, new version name), `blockers` (`human-approval`), `artifacts` (claims with digests, producer `A17`), `verification` (`partial` while approval is pending). End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a17-platform-engineer/xrd-composition-change.md; edit the source and run scripts/build_agents.py -->
