---
name: "swarm-dod-evidence"
description: "Use when you must prove acceptance criteria are met; attach evidence to each criterion, run the deterministic check and report unknown as unmet."
disable-model-invocation: true
---

# Definition-of-done evidence

## When to Use

- Whenever the task carries acceptance criteria, and always before reporting IN_REVIEW.

## Procedure

1. Call `shared_handover_bundle` with `{task_id}` and copy `acceptance[]` verbatim. Never reword or drop a criterion to make it easier.
2. Assemble evidence for `shared_dod_check`: for each criterion one line in your draft result, either a `summary_md` bullet or a `checks[]` entry `{criterion, status: pass|fail, evidence}` naming the file, command output, metric or test that shows it. Reference criteria by id (`AC1`, `REQ3`) when they have one.
3. Call `shared_artifact_digest` with `{path}` for each output that evidence points to, and list it in `outputs[]` as `{kind, uri, digest}`. A bullet is judged by its words: a `remaining` or `blockers` entry in the draft counts against the criteria it mentions, so list them only when they are real.
4. Call `shared_dod_check` with `{result: <draft>, task_id}`. It marks a criterion `met` only with affirmative evidence and no contradicting or outstanding item; negative words ("not", "pending", "missing") and unchecked boxes count against it.
5. For every `unknown` result, read its `reason`: add real evidence (run the check, then record it), or leave it unmet and say so. Do not paste the criterion text as its own evidence.
6. Call `shared_artifact_check` with `{task_id, outputs}` (each output also carrying `producer`: your agent id) to confirm each listed output exists, its digest matches and you are the producer. Read `results[].status`: only `ok` counts.
7. Call `shared_open_findings` with `{task_id}`. A blocking finding is an unmet criterion until fixed. In a rework the findings of the verdicts that sent the task back stay listed as `stale: true` until the gate re-reviews: each must have a recorded fix (`finding <id> fixed: ...`), the gate itself clears them.
8. Report IN_REVIEW only when `shared_dod_check` shows `all_met` true and every blocking finding from `shared_open_findings` is either fixed with recorded evidence (a `stale: true` one) or absent. A blocking `stale: false` finding always blocks. Otherwise report BLOCKED or FAILED with the unmet criteria in `summary_md`.

## Outputs

- A result whose every criterion has a checkable evidence line and whose `outputs[]` carry digests.
- The `met`, `unknown` counts quoted in `summary_md`.

## Failure Handling

- No acceptance criteria on the task: say so in `summary_md` and use the goal's observable outcomes as stated criteria.
- The keyword match is shallow: when `met` looks wrong, state the stronger evidence in prose; do not game the wording.
- `INVALID_INPUT`: pass `criteria` (non-empty strings) explicitly.

## Handover

- Hand over the criteria with their status so the reviewer can re-run `shared_dod_check` on the same evidence.

<!-- generated from prompts/skills/shared/dod-evidence.md; edit the source and run scripts/build_agents.py -->
