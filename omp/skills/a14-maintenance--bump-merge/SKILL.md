---
name: "a14-maintenance--bump-merge"
description: "Use when one semver dependency bump PR is ready and you must give a merge-readiness verdict (L2) or route it as L3 after licence, pin, cap and gate checks."
disable-model-invocation: true
---

# Bump merge verdict

## When to Use

- A single `dependency.bump.pr` has `test.results` and you must give a verdict (A14 spec section 4, rule 2). A14 never merges or enables auto-merge itself: the verdict goes to A01 and a human or the release flow merges.
- Not for a breaking bump plan (`a14-maintenance--major-upgrade`) and not for a bump that already failed gates (`a14-maintenance--gate-break`).

## Procedure

1. Call `maint_semver_bump` with `{from, to}` (semver `X.Y.Z`). `autonomy: L2` and `breaking: false` continue; `L3` or `breaking: true` stop here and go to `skill://a14-maintenance--major-upgrade`; `kind: downgrade` or `none` (autonomy `none`) is rejected as not a bump; `kind: prerelease` is `hold` (convention: no prerelease is merge-ready).
2. Call `maint_spec_pin` with `{spec: <new specifier>}`. `wildcard` (including any `latest` tag) always blocks the merge (A14 spec section 7: no `latest`, ever). `range` blocks only when the task says the repo requires exact pins; otherwise list it as `kind=deps` debt.
3. Call `maint_license_change` with `{previous_license, current_license, allowlist}`: SPDX expressions from the lockfile, package metadata or SBOM entry in the PR, and an allowlist of at least one SPDX id from the task constraints. Any of them missing goes to `open_questions` and the verdict is `hold`. `requires_review` or `copyleft_introduced` true blocks the merge and goes to A10 as a licence finding.
4. The daily cap (spec: "max N/day per repo") and the number of bumps already merged today come from the task inputs. If either is missing call `shared_escalate` with `{task_id, reason_code: "policy", summary, needs: ["daily bump cap for this repo"]}` and stop with its `result_patch`.
5. Call `shared_handover_bundle` with `{task_id}` and read `verdicts` (`gate`, `verdict`, `signature`, `expired`). The required gates follow the task risk class (low: review; medium: review and quality; high: review, quality, security and release). Merge-ready needs every required gate present with `verdict: pass`, `signature: valid` and `expired: false`; absent, unsigned, expired or failed counts as not met.
6. Call `shared_artifact_digest` with `{path: <lockfile>}`. The provenance entry is source, that digest and the signature result; no registry tool verifies package signatures, so write `signature: not verified by tooling` unless the PR or registry evidence states it.
7. Write the verdict: `merge-ready` (L2, all checks pass), `hold` (any check unknown), `route-L3` or `reject` with the failing step. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- A verdict (`merge-ready`, `hold`, `route-L3`, `reject`) with one line per check and the tool field behind it.
- A provenance entry for the bumped package (source, lockfile digest, signature result).

## Failure Handling

- `INVALID_INPUT` on `from` or `to`: the versions are not semver; hold, do not merge.
- A gate that is red: stop and hand the PR to `skill://a14-maintenance--gate-break`; never retry gates to get green or bypass A08, A09 or A10.
- `DEPENDENCY_UNAVAILABLE` or `TIMEOUT`: retry once, then verdict `hold` with the missing check named.

## Handover

- Fill `goal` (merge or route this bump), `completed` (checks passed), `remaining` (merge step or approval), `decisions` (verdict and basis), `artifacts` (lockfile digest), `constraints` (daily cap, normal gates), `verification`.
- `shared_handover_write` to `A01`; when a licence finding exists write a second handover to `A10` with only that finding. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/bump-merge.md; edit the source and run scripts/build_agents.py -->
