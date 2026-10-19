---
name: swarm-self-verify
description: Use when you are about to report a result; run the pre-completion checklist: criteria, artifacts, findings, contract, secrets, then pick an honest state.
owner: shared
tools: [shared_dod_check, shared_artifact_digest, shared_artifact_check, shared_open_findings, shared_open_questions, shared_status_report, shared_handover_validate]
---

# Self-verify

## When to Use

- Immediately before every final `task.result`, for every agent and every task.

## Procedure

1. Draft the full `task.result` first: `task_id`, `state`, `outputs[]` (`kind`, `uri`, `digest`), `summary_md`, `metrics`, `needs` when BLOCKED, `error` when FAILED. Do not verify an imagined result.
2. Acceptance: call `shared_dod_check` with `{result: <draft>, task_id}`. IN_REVIEW needs `all_met: true`; every `unknown` is unmet (`swarm-dod-evidence`).
3. Artifacts: call `shared_artifact_digest` with `{path}` for each output and write the digest into `outputs[]`. Then call `shared_artifact_check` with `{task_id, outputs}`, each output also carrying `producer` (your agent id); `failed` must be 0 and any `unchecked` output must be named as unverified in `summary_md`.
4. Findings: call `shared_open_findings` with `{task_id}`. Every blocking finding must be fixed or listed as unmet; never submit over a blocking finding silently. `stale: true` findings come from the verdicts that sent the task back for rework and stay listed until the gate re-reviews: submit with each one's fix recorded as evidence, and treat a blocking `stale: false` finding as unmet.
5. Questions: call `shared_open_questions` with `{task_id}`. An unanswered blocking question means BLOCKED, not IN_REVIEW (`swarm-open-questions`).
6. Contract: `state` is exactly IN_REVIEW, BLOCKED or FAILED; `task_id` is yours; BLOCKED carries `needs`; FAILED carries `error: {code, message}`; the handover id appears in `summary_md` (`swarm-handover-emit`). Check the handover with `shared_handover_validate` `{document}` when you wrote one by hand.
7. Secrets: use `grep` over your changed files and your draft for `ghp_`, `github_pat_`, `sk-`, `AKIA`, `xox`, `Bearer `, `-----BEGIN` and `password=`. Remove every hit from your draft and from files you can edit (an agent without edit or write tools reports the hit as a finding instead), and rotate nothing yourself; report a real leaked credential as a finding.
8. Pick the state honestly: IN_REVIEW only when steps 2 to 7 pass; BLOCKED when you need something named; FAILED when the work cannot succeed, with its cause. Then call `shared_status_report` with `{task_id, kind: done, step}` and emit the result.

## Outputs

- A verified final `task.result` and a checklist outcome: pass, or the first failing step with its cause.

## Failure Handling

- A check fails and you can fix it within scope: fix, then rerun from step 2 once. A second failure downgrades the state; do not loop.
- A check tool is `DEPENDENCY_UNAVAILABLE`: retry at most twice, then say in `summary_md` which check did not run and report IN_REVIEW only if the rest passed and the gap is stated.
- Never edit a criterion, digest or evidence line to turn a check green.

## Handover

- Hand over the checklist outcome and any unverified item as `verification` and `open_questions` (`swarm-handover-emit`).
