---
name: "a03-architect--adr-record"
description: "Use when a new architecture decision must be recorded as a MADR ADR, deciding L2 or L3 and checking that the status and file naming are legal."
disable-model-invocation: true
---

## When to Use
- `design.adr` for a fresh decision: a catalog miss, a pattern choice, a tech.stack pick or a breach found by another skill.
- A decision that replaces an earlier ADR is `skill://a03-architect--stale-design-review`, which also repairs the old ADR.
- Autonomy (prompts/A03 GUARDRAILS): ADRs are L2. Novel infrastructure or a new first-party dependency is L3 and ends BLOCKED `needs` `human-approval: novel_infrastructure`.

## Procedure
1. Call `shared_handover_read` with `{task_id}`; take the decision context, drivers and options from `handover`. A `status` other than `ok` is a `shared_escalate` with `reason_code` `stale-handover`.
2. Call `arch_adr_slug` with `{title}`. The returned `slug` is what the script puts in the file name `docs/adr/NNNN-<slug>.md` (it clips to 60 characters).
3. Decide the level (from the handover and `read` of the context): new infrastructure or a new first-party dependency is L3, anything else L2.
4. With `bash` run `python3 scripts/arch_adr.py --title "<title>" --status proposed --decision "<chosen option>" --context "<problem>" --drivers "<drivers>" --alternatives "<a;b;c>" --consequences "<effects>" --task-id <task_id> --correlation-id <correlation_id> --json` (ids from the assignment). Read `adr.adr_id` (`ADR-NNNN`, next sequential) and `adr.file`.
5. Use `edit` on `adr.file` to replace every `TBD` the template leaves (Context, Decision Drivers, both Consequences lists) with real text; the index does not catch them.
6. L2 only: call `arch_adr_transition` with `{from_status: proposed, to_status: accepted}`. When `allowed` is true use `edit` to change the `- **Status:** proposed` line to `accepted`.
7. L3: leave the ADR `proposed` and call `shared_escalate` with `{task_id, reason_code: policy, summary, needs: ["human-approval: novel_infrastructure"]}`; its `result_patch` is the BLOCKED result.
8. With `bash` run `python3 scripts/arch_adr.py --list --json`. The new entry must have empty `missing_sections` and `verdict` must be `pass`.
9. Call `shared_artifact_digest` with `{path: adr.file}`, then `shared_handover_validate` (`draft` true) and `shared_handover_write`.

## Outputs
- ADR file under `docs/adr/` with id, status, context, decision, alternatives and consequences.
- Level decision (L2 or L3) with the reason.

## Failure Handling
- `allowed` false from `arch_adr_transition`: its `reason` is the answer; do not edit the status by hand.
- Script `fail` or `missing_sections`: fix the ADR text, rerun `--list`.
- Script `E-INPUT` (`--decision` missing) or `INVALID_INPUT` (blank title): fix once, else BLOCKED `needs` `input`.
- Never write a second ADR to override an old one without `--supersedes`; that is `stale-design-review`.

## Handover
Fill `goal`, `completed`, `decisions` (decision and rationale, ADR id included), `artifacts` (kind `adr.set`, `uri` the file, `digest`, `producer` A03), `blockers` (the L3 approval), `open_questions`, `verification` (`status`, `confidence`, `checks`). Call `shared_handover_write` with `to_agent` A01, and a second time with `to_agent` A15 when documentation must render the ADR.

<!-- generated from prompts/skills/a03-architect/adr-record.md; edit the source and run scripts/build_agents.py -->
