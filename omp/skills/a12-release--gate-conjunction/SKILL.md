---
name: "a12-release--gate-conjunction"
description: "Use when a gate.release task or promote precheck must judge that every required gate verdict is present, signed, unexpired and passing, waivers approved."
disable-model-invocation: true
---

# Gate conjunction

## When to Use

- `task.assign` has capability `gate.release`, or a promotion is about to start and you must prove all gates are green first.
- You judge verdicts only. Authoring the canary plan is `skill://a12-release--plan-canary`; artifacts and signatures of the build are `skill://a12-release--provenance-precheck`.

## Procedure

1. Run the release gate: call `swarm_gate` with `{gate: "release", task_id: $TASK, correlation_id: $CORR}` (omp tool, not a registry tool; your guard blocks `scripts/rel_plan.py` in `bash`, and outside omp the same run is `python3 scripts/rel_plan.py --task-id $TASK --correlation-id $CORR --json`). The reply is one summary line: a `FAIL:` prefix means a failing verdict and `FROZEN` an active freeze. For a promotion precheck with no leased gate task, skip this step and treat the gate verdict as not yet issued. `read` `.swarm/verdicts/<task_id>.release.json` for `payload.findings` and `payload.runs`; a finding of kind `gate` reads `<task>: <gate> gate <reason>` with reason absent, unsigned, bad-sig, stale, expired or fail.
2. Call `shared_handover_bundle` with `{task_id}` for each target task. Rely on a `verdicts[]` entry only when `signature` is `valid` and `expired` is false; `unverified` or `absent` makes it a claim, not a pass. The entry has no issue time: take it from step 3.
3. For each required gate use `glob` on `.swarm/verdicts/*.<gate>.json` (the file is named after the issuing gate task, not after the target) and `read` each. Keep the files whose envelope `correlation_id` is this release's, and per gate take the newest `payload.issued_at` (epoch seconds, pass it as a string) with its `payload.verdict`. Get the current epoch with `bash` `date +%s`. Take approval ids for any `waive` from the recorded human approval in the task inputs, never from the verdict text.
4. Call `rel_gate_freshness` with `{verdicts: [{gate, verdict, issued_at}], max_age_h: {<gate>: 24 for each}, now, waivers: [{gate, approval_id}]}`; one entry per gate, `verdict` one of pass, fail, waive. 24 h is the security limit and the release verdict expiry (spec section 4, item 1). Any name in `expired`, `unwaived` or `failed` fails the gate.
5. Call `rel_gate_order_check` with `steps` = gate names sorted by `issued_at` and `required_order` = the gates of `GATES_BY_RISK[<task.risk_class from the bundle>]` in `swarm/taskstore.py`, without `release`. `missing` means a verdict was never issued; `out_of_order` is a finding, not an automatic fail.
6. Call `shared_verdict` with the finding severities from step 1. It only cross-checks; when it disagrees with the gate's own verdict, keep the gate verdict and note the difference.
7. Call `shared_open_findings` with `{task_id}` and route each finding to its issuing agent.
8. Decide: `pass` only when the gate verdict is pass, no `FROZEN`, `rel_gate_freshness` `ok` is true, `missing` is empty and every required gate has a valid unexpired bundle row; otherwise `fail`, hold the promotion and notify the producer loop.

## Outputs

- Release `gate.verdict` (signed by the gate script), a gate list with one status per gate, and findings of kind `gate` or `freeze`.

## Failure Handling

- `swarm_gate` failing or no verdict file: FAILED E-DEP; never hand-write a verdict.
- `rel_gate_freshness` INVALID_INPUT names the field (for example a verdict dated after `now`): fix the timestamp once, then BLOCKED needs=input.
- `shared_verdict` or `shared_open_findings` DEPENDENCY_UNAVAILABLE: record `skipped:tool-missing`; the verdict still rests on the gate.

## Handover

- `to_agent`: A01. `goal`: gate verdict for the release. `completed`: each gate with age and status. `remaining`: producer rework or promotion. `blockers`: expired, unwaived, failed or missing gates. `decisions`: pass or fail with the rule applied. `verification`: verified only when steps 1 to 5 all ran.
- Call `shared_handover_validate` with `draft: true`, then `shared_handover_write`.

<!-- generated from prompts/skills/a12-release/gate-conjunction.md; edit the source and run scripts/build_agents.py -->
