---
name: a14-maintenance--dep-sweep
description: Use when a scheduled freshness run must scan declared dependencies for unpinned specs, outdated packages and staleness, and rank the candidate bumps.
owner: a14-maintenance
tools: [maint_unpinned_count, maint_spec_pin, maint_stale_age, maint_semver_bump, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Dependency sweep

## When to Use

- A scheduled maintenance task asks for dependency freshness and pin hygiene with no CVE driving it (A14 spec section 6 target: freshness at or above 95 % within one minor of latest).
- Not for one bump that is ready to merge (`a14-maintenance--bump-merge`) and not for a CVE-driven run (`a14-maintenance--cve-triage`).

## Procedure

1. Run `python3 scripts/maint_deps.py --task-id <task> --correlation-id <corr> --root . --json` and its twin `bun scripts/ts/maint_deps.ts --task-id <task> --correlation-id <corr> --root . --json`. Record each missing twin as `skipped:tool-missing`. Read `deps` (`ecosystem`, `name`, `spec`, `pin`), `unpinned` (every dependency whose `pin` is not `pinned`) and `outdated` (`pip`: list of `{name, version, latest_version}`; `npm`: map of name to `{current, wanted, latest}`; either may be the string `skipped:tool-missing`). Do not open any task yet.
2. Call `maint_unpinned_count` with `{specs: [<spec of every dep, "" when none>]}` (at most 500) and keep `total`, `pinned`, `unpinned`, `wildcard`, `unpinned_pct`. The tool classifies with the script's own `_pin_state` (same as `maint_spec_pin`), so its `unpinned` must equal the length of the script's `unpinned` list; a mismatch is a defect to report, not two numbers to publish.
3. Call `maint_spec_pin` with `{spec}` only for specs you will cite as debt: a `latest` tag, `*`, or a bare name is a policy violation (A14 prompt, decision logic 6), `range` is debt of `kind=deps`. An empty spec cannot be sent (minimum length 1): the script already classes it `wildcard`.
4. For every dependency in `outdated`, call `maint_semver_bump` with `{from: <installed>, to: <latest>}` and keep `kind`, `breaking`, `autonomy`. The tool needs three-part semver: `INVALID_INPUT` (for example a PEP 440 `2.32`) means `kind unknown`, listed under gaps.
5. Call `maint_stale_age` with `{installed_released, latest_released, threshold_days}` (dates `YYYY-MM-DD`) per outdated dependency. Neither `pip list` nor `npm outdated` returns dates: read them with `bash` from `npm view <pkg> time --json` or the PyPI JSON for the package when the network is available; otherwise report the dependency as `freshness unknown`, never estimate a date. `threshold_days` comes from the task.
6. Rank the candidates by `maint_semver_bump` `autonomy` and `maint_stale_age` `days_over_threshold`: L2 first, descending by days over; L3 entries go last with a pointer to `a14-maintenance--major-upgrade`. Compute freshness as the share of declared dependencies whose `kind` is `none`, `patch` or a one-minor `minor` step (read from the version strings), compare it with the 95 % target, and report the `stale` count separately.
7. Call `shared_artifact_digest` with `{path: .swarm/debt_register.json}` (the script refreshed it), then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- A ranked `dependency.bump` list (package, from, to, kind, autonomy, days over threshold).
- Pin hygiene totals (step 2) and the freshness percentage with the threshold used.

## Failure Handling

- No manifest found: report an empty sweep with the scanned root, not a clean bill.
- `INVALID_INPUT` from `maint_stale_age` (latest before installed): the dates are swapped or wrong; fix once, else mark that dependency unknown.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry once, then list the dependency under gaps. Offline `pip` or `npm` shows as `skipped:tool-missing` and caps the confidence of the result.

## Handover

- Fill `goal` (open bump tasks in this order), `completed` (scan totals), `remaining` (bumps to open), `decisions` (threshold_days and why), `artifacts` (debt register with digest), `open_questions` (unknown dates), `verification`.
- Call `shared_handover_write` with `to_agent: A01`. See `skill://swarm-handover-emit`.
