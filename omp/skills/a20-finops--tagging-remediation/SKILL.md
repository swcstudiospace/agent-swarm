---
name: "a20-finops--tagging-remediation"
description: "Use when a finops.budget task needs allocation-tag coverage measured on IaC and an inventory, with a ranked remediation list by cost at stake."
disable-model-invocation: true
---

# Tagging remediation

## When to Use

- Costs cannot be attributed to an owner or environment (showback fails, spend is "unallocated"), or a tag policy is being introduced and the gap must be measured first.
- Not for dividing a shared cost once the tags exist (`skill://a20-finops--showback-report`) or for deleting untagged resources (never: `skill://a20-finops--idle-cleanup-proposal` proposes owner review). Writing the tags into IaC or the cloud is A11's change.
- Autonomy: measuring and ranking is L2. Tag changes on production resources are L3 (A11 applies after approval).

## Procedure

1. Call `shared_handover_read` with `{task_id}` for the A11 `environment.record` (environments and owners) and the resource inventory export the task supplies: per resource unique `id`, `monthly_cost` and its tags as `{key, value}` pairs (at most 500 resources and 50 tags per call). No inventory is BLOCKED `needs=input`.
2. Fix the required keys with the task (1 to 20 keys); the default policy of this skill and of the scanner is `owner` and `environment`. Tag keys are case-sensitive: `Owner` does not satisfy `owner`.
3. Run `python3 scripts/finops_cost_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <iac-dir> --required-tags owner,environment --json`. Read `by_rule["iac-untagged-resource"]` and the findings of that `rule` (each with `file` and `location`). Check `skipped`: `skipped:provider-default-tags` means Terraform tags inherited from provider `default_tags` were not evaluated, and `skipped:no-required-tags` means the check was off. These are gaps to name, not a pass. Kubernetes objects are not checked for tags by the script; labels are a separate question.
4. Call `finops_tag_coverage` with `resources` (`id`, `monthly_cost`, `tags`), `required_keys` (an array, for example `["owner", "environment"]`), `allowed_values` per key where the task fixes them (for example `environment`: `prod`, `staging`, `dev`; every `allowed_values` key must be in `required_keys`) and `min_coverage_pct` (default 95; pass it only when the task sets another). Inventories above 500 resources go in batches: add up `untagged_cost` and the compliant counts yourself.
5. Read `coverage_pct` (resources by count), `cost_coverage_pct` (by cost) and `untagged_cost`. `verdict` `non_compliant` means `coverage_pct` is under the minimum. `problem_counts` separates `missing`, `case_mismatch`, `empty_value` (each makes allocation impossible: a resource with any of them gets a major finding) from `invalid_value` and `duplicate_key` (minor). A `case_mismatch` is the cheapest fix: rename the key, do not add a second one.
6. Cross-check the script against `finops_tag_coverage`: match the IaC resources the script flagged against the inventory's non-compliant ids when the same stack is in both; a flagged IaC resource that is compliant in the inventory points to a tag applied outside IaC (drift, an admission policy, a tagging job). Report that as a finding, with both sources and their costs.
7. Rank the offenders by `monthly_cost` (the tool lists the 100 highest and sets `findings_truncated`; if true, call again on the resources not yet listed). Write the remediation list with `write` at the path the task names, else `docs/finops/tagging-<scope>.md`: counts, `untagged_cost`, owner inferred only when the environment record names it (else "unknown, ask"), the exact tag per resource and which fixes are IaC edits versus live changes. Note for billing that the provider may require cost allocation tags to be activated before they appear in cost reports: confirm in its documentation.
8. Production tag changes: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "apply production tag changes", "needs": ["human-approval"]}`.
9. Call `shared_artifact_digest` with `{path}`, then see Handover.

## Outputs

- `cost.report` (allocation section): coverage by count and by cost, problem counts, ranked remediation list; evidence is the script and `finops_tag_coverage` outputs.

## Failure Handling

- INVALID_INPUT: fix the named field (duplicate resource id, an `allowed_values` key not in `required_keys`) or BLOCKED `needs=input`.
- PARTIAL_SUCCESS: use `completed_fraction`; name resources without cost data (the tool counts them at 0).
- DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Script exit 1 is a major finding, not an error; exit 2 means fix `--path` or `--required-tags`.
- Never invent an owner from a name pattern. L3 actions end BLOCKED `needs=human-approval`.

## Handover

- `to_agent`: `A17` (consumes `cost.report`: ownership metadata belongs in the catalog). `goal`: collect the missing owners.
- `decisions`: required keys and allowed values; `artifacts`: list digest, producer `A20`; `open_questions`: unknown owners; `verification`: `partial` until the tags are applied.
- End with `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a20-finops/tagging-remediation.md; edit the source and run scripts/build_agents.py -->
