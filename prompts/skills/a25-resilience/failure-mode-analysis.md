---
name: a25-resilience--failure-mode-analysis
description: Use when a resilience.failure_mode task needs failure modes of a design ranked by FMEA risk priority number with evidence-backed scores and mitigation owners.
owner: a25-resilience
tools: [resil_fmea_rpn, resil_pdb_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Failure-mode analysis

## When to Use

- A design (`resilience.design` from A16), a launch or an incident (`incident.alert` from A13) needs its failure modes ranked so that experiments and fixes go to the riskiest first.
- Not for scheduling the experiments: `skill://a25-resilience--game-day-plan`. Not for threat modelling: that is A10.
- Analysis is read-only evidence work (L2).

## Procedure

1. Use `read` on `resilience.design`, `slo.manifest` and the incident record to list components and, per component, the failure modes: node loss, zone loss, dependency timeout, disk full, certificate expiry, bad deploy, queue backlog, credential rotation failure. One id per mode, unique and stable (`<component>:<mode>`).
2. Run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <manifest-dir> --json`. A finding is evidence for a score: `pdb-missing` raises occurrence of node-drain outage, `readiness-missing` raises detection (hard to notice) for bad deploys, `single-replica` raises occurrence of any pod loss.
3. Score each mode 1 to 10 and write the source next to the number: severity from the SLO impact in `slo.manifest` (10 = safety, data loss or total outage; 7 to 8 = SLO breach for most users; 4 to 6 = degraded feature; 1 to 3 = unnoticed), occurrence from incident counts or `deploy.telemetry` (10 = near certain, 1 = remote), detection from existing alerts (10 = no alert, nobody notices; 1 = automatic and immediate). A score without a source is labelled `assumed`.
4. Call `resil_fmea_rpn` with `{"rpn_threshold": <task policy>, "modes": [{id, severity, occurrence, detection}]}`. No policy in the task: use 100 and record the assumption. Read `ranked` (rpn descending), `requiring_action`, `pareto_count`, `max_rpn`, `total_rpn`.
5. Cross-check the `resil_fmea_rpn` output by hand: `rpn` of the top mode is severity times occurrence times detection; `total_rpn` is the sum of all `rpn`; `pareto_count` is the fewest top modes reaching 80% of `total_rpn`. A mode needs action when `rpn` is at least the threshold or severity is 9 or higher, whatever the RPN; a finding at 2 times the threshold is a blocker. The RPN scale is classic AIAG FMEA; the newer AIAG-VDA method uses Action Priority tables, so treat the threshold as team policy and say so.
6. For each node-drain or pod-loss mode, confirm the mitigation exists with `resil_pdb_check` (`workloads: [{name, kind, replicas, min_available | max_unavailable}]`): `pdb-missing`, `pdb-unsatisfiable` or `pdb-blocks-eviction` keeps the mode open. Route the other action modes: PDB or spread to `skill://a25-resilience--pdb-and-spread-hardening`, probes to `skill://a25-resilience--probe-tuning`, quorum to `skill://a25-resilience--quorum-protection`, restore to `skill://a25-resilience--backup-restore-drill`.
7. Re-score each mitigation as projected values (label them `projected`) and call `resil_fmea_rpn` again: `max_rpn` under the threshold and `requiring_action` only for severity 9 or higher modes with a tested mitigation is the exit state. Never replace a measured score by a projected one in the report.
8. Use `write` for `resilience-reports/<service>/fmea.md` (table: id, S, O, D, RPN, source, mitigation, owner skill, re-drill) and call `shared_artifact_digest` with `{path}`.

## Outputs

- `resilience.report` FMEA section: ranked modes with score sources, action list with owners, before and projected `max_rpn`, digest.

## Failure Handling

- INVALID_INPUT (duplicate `id`, score outside 1 to 10): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- More than a third of scores `assumed`: report the analysis as `provisional` and list the evidence to collect; do not present a ranking as measured.
- Script `skipped` YAML: the manifest evidence is partial; say so.

## Handover

`to_agent` A12. `goal`: schedule the action modes before release. `decisions` (threshold and its source), `artifacts` (FMEA with digest, producer A25), `open_questions` (assumed scores), `verification` (`partial` while scores are assumed). Call `shared_handover_validate`, then `shared_handover_write`.
