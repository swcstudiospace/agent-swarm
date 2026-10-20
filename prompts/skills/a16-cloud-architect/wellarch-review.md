---
name: a16-cloud-architect--wellarch-review
description: Use when a cloud.wellarch task needs a six-pillar Well-Architected review of a repository or design with ranked remediation and evidence from the scan.
owner: a16-cloud-architect
tools: [cloud_wellarch_scorecard, cloud_twelve_factor_audit, shared_handover_read, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Well-Architected review

## When to Use

- A `cloud.wellarch` task asks how ready a repository, service or design is across operational excellence, security, reliability, performance efficiency, cost optimization and sustainability.
- Not for a security audit (A10 owns scanners and the security gate), nor for a cost model (A20), nor for publishing a reference design (`skill://a16-cloud-architect--reference-architecture-publish`).
- A review is a recommendation (L2). It never changes code or infrastructure.

## Procedure

1. Run `python3 scripts/cloud_readiness_scan.py --task-id $TASK --correlation-id $CORR --root . --path <dir> --json`. Exit 0 is `status: ok`, exit 1 is `status: fail` (a major finding exists, not a tool error), exit 2 is an input error. Read `overall_score_pct`, `pillars[]`, `signals`, `files_scanned`, `truncated` and `skipped`. Finding ids start with `cloud.`. The scan reads the working tree, not git: use a clean checkout. It is a text heuristic: modules, Helm values and generated code can hide signals.
2. Read the `skipped` and `truncated` fields with `read` of the scan JSON: `skipped:tool-missing` (PyYAML absent) or `truncated: true` (2000 files) are coverage gaps for the report; `answered` counts rules, not breadth of evidence: without IaC, security, cost optimization and sustainability each rest on a single rule (`env-file`, `tagging`, `efficiency`). Report a pillar with `answered` 1 as "weak evidence", never as an assessed 100.
3. Build the questionnaire answers from the scan plus the task: one entry `{pillar, question, weight (1-5), risk (none|medium|high)}` for each rule the scan could not judge (for example operational runbooks, data classification, sustainability targets). Source each from `shared_handover_read` of the blueprint or from the task; an unsourced answer is `medium`, flagged "assumed".
4. Call `cloud_wellarch_scorecard` with `{answers, pillar_weights}`; use weights 1-5 per pillar from the task, default 1 each. Read `overall_score_pct`, `pillars[]` (`score_pct`, `high_risk`, `medium_risk`), `weakest_pillar`, `high_risk_count`, `remediation[]` ordered by `impact` (weight x risk).
5. Cross-check the `cloud_wellarch_scorecard` result against the scan: the scan scores its own findings with the same scorecard logic. For every pillar present in both, `score_pct` from the tool must not differ from the scan `pillars[]` value by more than 25 points; a larger gap means the manual answers contradict the repository evidence: list each pair, do not average.
6. Call `cloud_twelve_factor_audit` with a `service` descriptor when the target is a runtime service (fields such as `config_from_environment`, `secrets_in_codebase`, `handles_sigterm_gracefully`, `startup_seconds`, `logs_to_stdout`). A field left out of `service` is a finding (`... is not declared`): `major` for a major-class check, so a partial descriptor fails; ask A05 for the fact, never fill in `true`. Its `findings` of severity `major` (for example `factor-03:secrets_in_codebase`) must also be visible as reliability or security risk in step 4; if the scan found `cloud.env-file-committed` and the audit says `secrets_in_codebase: false`, that is E-CONTRACT.
7. Rank the `cloud_wellarch_scorecard` `remediation`: blockers and `high` risks in security and reliability first. Verdict follows the scorecard `verdict` (`fail` on any major or blocker). `high_risk_count` > 0 in any pillar means that pillar is not accepted. Never invent a score for a pillar the evidence does not cover.
8. Write the review with `write` at the task path, else `docs/cloud/wellarch-review.md`: scan output summary, step-4 table, top 10 `remediation` items with owner agent (A10 security, A11 IaC, A03 contract, A20 cost) and the assumptions list. Call `shared_artifact_digest` for it, then `shared_handover_validate` and `shared_handover_write`.

## Outputs

- `wellarch.review`: per-pillar scores, ranked remediation, coverage gaps, assumptions, scan evidence.

## Failure Handling

- Script exit 2 or `INVALID_INPUT`: fix `--path` or the named field; else BLOCKED `needs=input`.
- `PARTIAL_SUCCESS`: use `completed_fraction` and name pillars without answers.
- `DEPENDENCY_UNAVAILABLE`: retry once, then `skipped:tool-missing`.
- Never retry a failing scan with a narrower `--path` to make it pass.
- The 25-point tolerance is this skill's choice, not a standard. Pillar weights come from the task; do not tune them to a desired score.
- No L3 or L4 action is taken in this skill.

## Handover

`to_agent` A03 (system blueprint owner, consumes `wellarch.review`). Fill `goal`, `completed`, `decisions` (pillar verdicts), `artifacts` (kind `wellarch.review`, digest, producer A16), `open_questions` (assumed answers, coverage gaps), `verification` (`partial` if any gap). End with `shared_handover_validate` then `shared_handover_write`.
