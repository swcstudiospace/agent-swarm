---
name: a14-maintenance--major-upgrade
description: Use when a dependency bump is breaking or behavior-flagged (L3) and needs a changelog review, a staged rollout draft and human approval before any merge.
owner: a14-maintenance
tools: [maint_semver_bump, maint_changelog_check, maint_stale_age, shared_escalate, shared_handover_validate, shared_handover_write]
---

# Major upgrade plan (L3)

## When to Use

- `maint_semver_bump` reports `breaking: true` (a major change, or a minor change while major is 0) or the task flags the bump as behavior-changing (A14 spec section 4, rule 2).
- Not for compatible bumps (`a14-maintenance--bump-merge`).

## Procedure

1. Call `maint_semver_bump` with `{from, to}` and confirm `breaking: true`, `autonomy: L3`. If it says `L2` and the task does not flag the bump as behavior-changing, stop and use `skill://a14-maintenance--bump-merge`.
2. Read the upstream changelog entry for the target version with `read` or `grep`, then call `maint_changelog_check` with `{entry: <markdown of that one section>, version: <target>}`. `valid: false` means the entry is not Keep a Changelog format (`## [version] - date` and the categories Added, Changed, Deprecated, Removed, Fixed, Security): use `first_problem` as a note and read the notes by hand; it does not stop the plan.
3. Use `bash` (`date -u +%F`) for today where needed and call `maint_stale_age` with `{installed_released, latest_released, threshold_days}` (dates `YYYY-MM-DD`, `threshold_days` from the task) to size how far behind the repo is; a large gap widens the migration scope. Without the release dates record `gap unknown` and continue.
4. List every `Removed`, `Changed` and `Deprecated` bullet from the entry that touches APIs this repo uses (use `grep` over the source), each with the file it hits.
5. Draft a staged rollout plan from the step 4 inventory and the `maint_semver_bump` `kind` as a proposal for A12 (A12 owns canary percentages and promotion): stages, the guardrail metric per stage, and the rollback trigger. Do not set the traffic percentages yourself.
6. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "L3 major bump needs human approval", needs: ["approval to proceed with <package> <from> to <to>", "A12 rollout window"]}`; keep the returned `result_patch` as the BLOCKED result.
7. Call `shared_handover_validate`, then `shared_handover_write`. Do not merge anything before approval.

## Outputs

- An upgrade plan: breaking-change inventory with file hits, rollout draft, rollback trigger, estimated effort.
- The BLOCKED `result_patch` from `shared_escalate` naming the approvals needed.

## Failure Handling

- No upstream changelog reachable: record `changelog unavailable`, list the risk as unknown and do not lower the risk class.
- `maint_changelog_check` is `INVALID_INPUT` because the entry is empty: re-read the section boundaries once, otherwise report the gap.
- `shared_escalate` fails with `DEPENDENCY_UNAVAILABLE`: state BLOCKED in the result yourself; never proceed without the approval.
- If upstream is unmaintained, switch to `skill://a14-maintenance--gate-break` for the fork ADR path.

## Handover

- Fill `goal` (approve and schedule the staged upgrade), `completed` (inventory done), `remaining` (approval, A12 window, implementation tasks), `decisions` (why L3), `blockers` (pending human approval), `constraints` (no merge before approval, gates unchanged), `open_questions`, `verification`.
- `shared_handover_write` to `A12`; when an API contract changes, write a second handover to `A03` with the affected endpoints. See `skill://swarm-handover-emit`.
