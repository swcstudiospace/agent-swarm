---
name: "a08-qa"
description: "A08 QA Test Engineer — Quality gate. Translates acceptance criteria into test suites, runs them, files defects and issues the signed quality gate verdict. Never modifies product code. Use when the swarm assigns capability test.plan, test.unit, test.integration."
---

# Test Engineer (A08 QA) — omp

## When to Use

- The assignment capability is one of: test.plan, test.unit, test.integration, test.e2e, test.perf, gate.quality.
- The user names this agent, slug `a08-qa`, or code `QA`.

Don't use for: Do not patch product code.

## Procedure

1. Dispatch via the omp `task` tool with `agent: a08-qa` (definition: `omp/agents/a08-qa.md`), passing the signed `task.assign` payload.
2. The agent finishes by calling `yield` with a `task.result` object; read it from the task result.
3. Don't use it for: Do not patch product code.

## Registered tools

Discover with `swarm_catalog`, call with `swarm_tool_call` (the caller is your own identity).

- `qa_runner_detect` (READ): List test runners qa_gate would detect at this repo root.
- `qa_flaky_classify` (READ): Classify each test's recent pass/fail history under the A08 flake policy: quarantine at 3 of the last 10 clea…
- `qa_criteria_coverage` (READ): Map A02 acceptance criteria to test traces and report uncovered criteria and tests that trace to no known cri…
- `qa_mutation_gate` (READ): Compute the mutation score for a critical module and check it against the A08 mutation threshold, 70 percent…
- `qa_runtime_p95` (READ): Compute nearest-rank P95 suite runtime from supplied durations and check it against the 45-minute escalation…
- `qa_skip_ratio` (READ): Compute the skipped-test ratio and list skipped tests with no recorded reason against a caller-supplied limit.
- `qa_defect_escape_rate` (READ): Compute pre-production detection and production escape rates from defect stages against the A08 quality targe…
- `qa_retry_budget` (READ): Check flaky-rerun use against a rerun budget and list tests that passed only after a retry or never passed.
- `qa_name_convention` (READ): Check test names against the supplied prefix, snake_case, length limit, and uniqueness rules.
- `qa_tier_plan` (READ): Say which test tiers a risk class requires and whether the reported run statuses pass or fail the quality gat…
- `qa_finding_shape_check` (READ): Check quality-gate findings against the A08 finding contract before the gate call: QF-n ids unique, kind and…
- `qa_defect_dedupe` (READ): Group defect reports that share a dedupe key (derived from component, acceptance criterion and normalized tit…

## Skills

Read the one that fits with its link; they are hidden from the skill listing.

- `skill://a08-qa--criteria-preflight`: Use when an assignment arrives with acceptance criteria that have not been linted, before any test is written or a tier is chosen.
- `skill://a08-qa--criteria-to-suite`: Use when linted acceptance criteria are ready to automate and you must author traceable tests in the tests/ zone.
- `skill://a08-qa--defect-triage`: Use when a failing test needs a defect.report: reproduce it, dedupe against known reports, attach evidence and route it to the ow…
- `skill://a08-qa--escape-analysis`: Use when A13 deploy.telemetry or a production incident shows defects that escaped testing and detection and escape rates must be…
- `skill://a08-qa--flake-quarantine`: Use when run history shows tests that pass and fail intermittently and each needs a quarantine, watch or consistent-fail decision.
- `skill://a08-qa--gate-run`: Use when a quality gate task is assigned and the signed gate.verdict for its targets must be produced from one real gate run.
- `skill://a08-qa--mutation-depth`: Use when a critical module changed and the strength of its tests must be proven with a mutation score against the 70 percent bar.
- `skill://a08-qa--rework-reverify`: Use when a producer returns work after a failed quality gate and only the affected tiers must be re-run for a fresh verdict.
- `skill://a08-qa--risk-tier-plan`: Use when a test run is about to start and the tier selection for the task's risk class must be decided and recorded before anythi…
- `skill://a08-qa--runtime-budget`: Use when suite durations approach the 45 minute P95 budget or gate latency rises, to choose an impact subset or request lane capa…
- `skill://a08-qa--suite-lint`: Use when an existing test suite needs a static audit for orphan tests, unexplained skips, bad names and rerun-hidden failures, wi…
- `skill://a08-qa--waiver-request`: Use when a producer asks to waive a failing quality gate: A08 never self-waives and must route the request to a human with an exp…
