---
name: "a17-platform-engineer--scorecard-rollout"
description: "Use when a platform.scorecard task rolls the bronze, silver, gold maturity scorecard out to catalogued services and needs a cross-checked result."
disable-model-invocation: true
---

# Scorecard rollout

## When to Use

- A `platform.scorecard` task asks for maturity levels for several services (a system, a team, or the whole catalog), or for a change to the required level of a tier.
- Delivery metrics are `skill://a17-platform-engineer--dora-baseline`. A single new service is `skill://a17-platform-engineer--service-onboarding`.
- Publishing a scorecard is L2. Raising a `required_levels` entry for a tier that has services below it is L3 (`platform_api_breaking_change`): teams lose a passing status.

## Procedure

1. Run `python3 scripts/plat_catalog_lint.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json` to list the services in scope (`entity_count`, `findings[].location`). Services with `owner-missing` or `tier-missing` cannot be scored fairly: list them under `open_questions` and score them only after the owner or tier is supplied.
2. With `read` and `grep`, collect the eight facts for each service from evidence, not assumption: `has_owner` (catalog `spec.owner`), `has_runbook` (runbook annotation or link, the same rule as the lint `runbook-missing`), `has_slo` (an `slo.manifest` from A13), `has_on_call`, `test_coverage_pct`, `sast`, `dependency_scan`, `secret_scan` (A10 results). A fact with no evidence is `false`, and the gap says "no evidence".
3. Call `plat_maturity_scorecard` with `services` (`name`, `tier` in `tier-0..tier-3`, the eight facts), `min_coverage_pct` (default 70 when the task names none; say which you used). Levels are cumulative: bronze = owner and runbook; silver adds SLO, on-call and coverage at or above the minimum; gold adds SAST, dependency scan and secret scan. A service that skips a level stays at the lower one.
4. Defaults to require: `tier-0` gold, `tier-1` silver, `tier-2` bronze, `tier-3` none. `below-required-level` is major for `tier-0` and `tier-1`, minor otherwise. Read `status`, `below_required_count`, `average_level`, `level_counts`, per-service `gaps`.
5. Cross-check the `plat_maturity_scorecard` result: `level_counts` must sum to `service_count`; `below_required_count` must equal the number of `below-required-level` findings; every service `passed` is at most `total` (8). Re-run the lowest and the highest service alone: the same `level` must come back, else E-INTERNAL.
6. Call `plat_catalog_graph` with `entries` for the scored services: `most_depended_on` entries below their required level go first in the remediation order (their `transitive_dependents` is the blast radius). Do not rank by name.
7. Rollout stages (this skill's convention): publish the scorecard as information only; enforce `tier-0` and `tier-1` gaps first. A change to `required_levels` (`tier_0`..`tier_3`, integers 0 to 3) needs `shared_escalate` with `reason_code` `policy`, `needs` `["human-approval"]`, attaching the services that would fall below it.
8. `write` the report; call `shared_artifact_digest` with `{path}`; hand over.

## Outputs

- `platform.scorecard`: per-service `level_name`, `gaps`, `meets_required`, the aggregate counts, remediation order, evidence source per fact.

## Failure Handling

- INVALID_INPUT (duplicate or blank service `name`, `test_coverage_pct` outside 0 to 100): fix the named field once, else BLOCKED `needs=input`.
- Tools DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; publish no level you did not compute.
- PARTIAL_SUCCESS: use `completed_fraction`, name services not scored.
- The 8-check weights and level names are this platform's house policy, not an external standard: defer to tool output.

## Handover

`to_agent`: `A13` (SLO gaps) and `A14` (maintenance debt); address `A14` when remediation tasks follow. Fill `goal`, `completed`, `decisions` (stage, `min_coverage_pct`), `artifacts` (report with digest, producer `A17`), `open_questions` (unowned services), `verification`. End with `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a17-platform-engineer/scorecard-rollout.md; edit the source and run scripts/build_agents.py -->
