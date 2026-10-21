---
name: "a08-qa--rework-reverify"
description: "Use when a producer returns work after a failed quality gate and only the affected tiers must be re-run for a fresh verdict."
disable-model-invocation: true
---

## When to Use

A producer (A05, A06 or A07) resubmits after your gate failed. General intake rules live in `skill://swarm-rework-intake`; this skill states only the A08 delta: a verdict issued before the rework is stale and invalid (A08 prompt, stale-verdict rule), the re-run is limited to what the open findings touch, and the rework bound is two loops. The first full pass is `a08-qa--gate-run`.

## Procedure

1. Call shared_handover_read with `task_id` for the producer's handover; read `completed` (what they say they fixed) and `artifacts`.
2. Call shared_open_findings with `task_id` the producer's task (the gate target; verdict rows hang on the target). It lists only unexpired findings of failing verdicts, which is the work list; note each `kind` and `ac_ref`.
3. Call shared_attempt_history with `task_id`. If `rework_loops` is already at `max_rework_loops`, go to step 6; this run would be the third failure.
4. Map each open finding to its tier (functional to unit or integration, perf to perf, coverage to unit) and confirm each repro with `bash` using the runner's own command on just the affected tests (for example `python3 -m pytest -q <test id>`). Then take the fresh verdict with one swarm_gate run (`gate` "quality", the gate task id, its `correlation_id`, `per_target_findings` for the findings you still hold). swarm_gate has no tier option, so the gate script re-runs every detected runner and the omp guard blocks shell runs of `scripts/qa_gate.py`; the narrowing above is your own evidence, and a verdict issued before the last producer commit is never inherited.
5. If tests passed only after reruns, call qa_retry_budget with `runs` = `{test, attempts[]}` and `max_reruns` 2 (convention); `rescued` tests go to `skill://a08-qa--flake-quarantine`, `unresolved` ones stay failed. Close a finding only when its repro now passes; record the evidence per finding id.
6. On the third failure or when `rework_loops` is exhausted, call shared_escalate with `task_id`, `reason_code` `policy`, a `summary` naming the unfixed finding ids and `needs` `["A01 decision"]`; do not retry.

## Outputs

- Fresh gate result for the re-run tiers; per finding: closed (with passing evidence) or still open.
- `rework_loops` used out of the cap.

## Failure Handling

- Handover `status` stale or missing: ask the producer through an open question; do not re-run on guesswork.
- An open finding without `ac_ref`: keep it open and state that the producer has no trace to verify against.

## Handover

Call shared_handover_write to the producer if findings remain (`remaining` each open id with its failing repro, `blockers` the tier, `decisions` which tiers you did not re-run and why) or to A01 when all closed (`completed` closed ids, `verification` status `verified`).

<!-- generated from prompts/skills/a08-qa/rework-reverify.md; edit the source and run scripts/build_agents.py -->
