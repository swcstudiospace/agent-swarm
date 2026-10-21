---
name: a27-contract-audit--fuzz-campaign-review
description: Use when fuzz or invariant campaign results must be judged adequate or not (runs, depth, seeds, coverage, revert ratio) before claiming the code was fuzzed.
owner: a27-contract-audit
tools: [audit_fuzz_adequacy, audit_invariant_coverage, shared_verdict, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

## When to Use

- A suite exists (yours from `skill://a27-contract-audit--invariant-design` or the team's) and the report would say "fuzzed" or "invariant tested".
- It judges the campaign, not the spec: the spec's coverage of state is `audit_invariant_coverage` in the invariant-design skill. Running the project's CI is A08.
- Running tests locally is L2. Changing the adequacy policy is L3 (human), never a way to make a campaign pass.

## Procedure

1. `read` `foundry.toml`: `[fuzz] runs`, `seed`; `[invariant] runs`, `depth`, `fail_on_revert`; the profile used. Defaults differ between Foundry versions, so record `forge --version` and use the file's values, never remembered defaults.
2. Run the campaign locally with `bash` under three distinct seeds: `forge test --match-path "audit/invariants/*" --fuzz-seed 1`, then `2`, then `3` (add `--fuzz-runs <n>` only when the profile is lower than the policy). Every run must finish; a seed that times out does not count in `distinct_seeds`.
3. Measure coverage with `bash`: `forge coverage --report summary`; for via-IR builds add `--ir-minimum` and mark the numbers approximate. Take line % and branch % for in-scope files only, not the whole repository.
4. From the invariant run output (`-vv` call table) sum calls and reverts per handler function: `revert_ratio_pct` = reverts / calls x 100; `functions_exercised` = handler functions with at least one call; `functions_total` = state-changing functions. Cross-check `functions_total` with `state_changing_functions` from `audit_invariant_coverage`; a difference is E-CONTRACT.
5. Call `audit_fuzz_adequacy` with `mode` (`stateless` or `invariant`), `runs`, `depth` (required for invariant mode), `distinct_seeds`, `line_coverage_pct`, `branch_coverage_pct`, `revert_ratio_pct`, `functions_total`, `functions_exercised`. Leave `min_total_calls` out unless a human approved a different floor (L3); when given it replaces the 100000 call floor in invariant mode and the 10000 run floor in stateless mode, and a value below that floor is reported as `FUZZ-FLOOR` (medium), which is a miss to list.
6. Read the policy checks from the output (the tool is the authority; the numbers are this audit's policy, not a Foundry or industry standard): stateless `runs` >= 10000; invariant `depth` >= 50 and `total_calls` (runs x depth) >= 100000; `distinct_seeds` >= 3; line coverage >= 95 % and branch >= 90 %; `revert_ratio_pct` <= 50; every function exercised. A miss is medium, except `distinct_seeds` (low); it is high for stateless runs under a tenth of the floor (under 1000), total calls under a tenth of the floor, coverage more than 15 points under, or under 90 % of functions exercised.
7. Check `total_calls` = `runs` x `depth` against your own multiplication, and `checks_passed` / `checks_total` x 100 = `adequacy_score` (a plain share of passed checks: it does not weigh severity, read the findings). Then call `shared_verdict` with `severities` = the finding severities mapped to the gate scale (info to `info`, low and medium to `minor`, high to `major`, critical to `critical`); it must be `fail` exactly when the tool `verdict` is `fail` (a tool `warn` maps to `pass`). `fail` means the campaign is inadequate.
8. Decide: `pass` or `warn` with only medium misses: report "fuzzed" with the exact numbers and the misses. `fail`: report "campaign inadequate", list which checks failed, request a longer or better-targeted campaign (more actors, fewer reverting calls, missing handler functions) and rerun; never lower a threshold to pass. Save the numbers to `audit/fuzz-review.json` and digest it with `shared_artifact_digest`.

## Outputs

- Campaign adequacy record: inputs, `adequacy_score`, failed checks, verdict (feeds `audit.findings` as low-to-high process findings and the report's methodology).
- No exploit claim is made from a clean campaign: absence of failure is not proof.

## Failure Handling

- INVALID_INPUT (`functions_exercised` above `functions_total`, missing `depth`): fix the count or the mode; do not clamp.
- DEPENDENCY_UNAVAILABLE for `forge`: retry once, then `skipped:tool-missing`; no adequacy figures may be invented.
- PARTIAL_SUCCESS: if a seed or coverage run failed, name it and compute with what completed.
- A counterexample found by any seed is a finding, not a campaign result: use `skill://a27-contract-audit--finding-writeup-poc`.

## Handover

- `to_agent`: `A26` (campaign gaps are fixed by adding handlers or tests) or `A08` when the gap is in the shared suite. `artifacts`: `audit/fuzz-review.json` with digest. `verification`: verified only when all three seeds and the coverage run completed.
- End with `shared_handover_validate`, then `shared_handover_write`.
