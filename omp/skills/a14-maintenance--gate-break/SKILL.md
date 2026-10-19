---
name: "a14-maintenance--gate-break"
description: "Use when a dependency bump fails a gate or its upstream looks unmaintained; revert, find a compatible exact pin, and report incompatibility to A03."
disable-model-invocation: true
---

# Failed bump and unmaintained upstream

## When to Use

- A bump branch failed A08, A09 or A10, or upstream stopped releasing (A14 spec section 5: bump breaks gates; upstream unmaintained).
- Not for a breaking bump planned ahead of time (`a14-maintenance--major-upgrade`) and not for a bump that passed (`a14-maintenance--bump-merge`).

## Procedure

1. Call `shared_attempt_history` with `{task_id}` for the bump task: `attempt` against `max_attempts`, `rework_loops` against `max_rework_loops` and `last_error`. At the cap, stop retrying and plan the revert.
2. Call `shared_open_findings` with `{task_id}` to name the failing gates and their findings (unexpired failing verdicts only); the failure reason is quoted from them, not paraphrased.
3. Revert on the bump branch only, using `bash`: `git revert --no-edit <bump commit>` in your own worktree. Never push to a protected branch, never force-push, never touch another agent's branch, never bypass a gate; the revert reaches A01 as a branch reference in the handover.
4. For the bisect-compatible pin, take the known-good and failing versions and call `maint_semver_bump` with `{from: <good>, to: <failing>}` to see `kind`. List the released versions between them with `bash` (`npm view <pkg> versions --json`, or `pip index versions <pkg>`) and propose candidates, patch before minor. Each candidate is a new bump PR that goes through A08, A09 and A10 as usual: you do not run or sign gates. Call `maint_spec_pin` with the ecosystem's exact form (`==<candidate>` for pip, bare `<candidate>` for npm) and accept only `pinned`.
5. For an unmaintained upstream, use `bash` (`date -u +%F`) for today and call `maint_stale_age` with `{installed_released: <last upstream release date>, latest_released: <today>, threshold_days: <the task's threshold>}`. `stale: true` with no replacement is the evidence for a vendoring or fork proposal.
6. Write the `incompatibility` report for A03: package, versions, failing gate and finding ids, what breaks, the pin that works. For the fork or vendoring proposal draft an ADR input (L3) with a maintenance-cost estimate in hours per quarter; the decision belongs to A03 and human approval.
7. Call `shared_handover_validate`, then `shared_handover_write`.

## Outputs

- An incompatibility report (versions, evidence, working pin or none).
- The revert reference and, when needed, a fork or vendoring ADR input with its cost estimate.

## Failure Handling

- The revert conflicts: stop and report BLOCKED with the conflicting files; do not resolve by force.
- No compatible version exists: say so and give the unmaintained evidence; do not pin an untested version to get green.
- `INVALID_INPUT` on a version: the string is not three-part semver; use the exact tag from the registry or report the gap.

## Handover

- Fill `goal` (decide the upgrade path or the fork), `completed` (revert done, versions tested), `remaining`, `decisions` (pin chosen and why), `blockers` (no compatible release), `constraints` (gates unchanged, L3 for fork), `open_questions`, `verification`.
- `shared_handover_write` to `A03`. See `skill://swarm-handover-emit`.

<!-- generated from prompts/skills/a14-maintenance/gate-break.md; edit the source and run scripts/build_agents.py -->
