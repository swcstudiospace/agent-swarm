---
name: a25-resilience--resilience-regression-gate
description: Use when a manifest or release change must not weaken resilience; block on new blocker or major PDB, spread, probe or shutdown findings in changed manifests.
owner: a25-resilience
tools: [resil_pdb_check, resil_probe_check, resil_quorum_safety, shared_open_findings, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Resilience regression gate

## When to Use

- A pull request, release candidate or Helm and kustomize change touches Deployments, StatefulSets, DaemonSets, PDBs or HPAs, and A12 needs a pass or block on resilience.
- Not for fixing the findings: use `skill://a25-resilience--pdb-and-spread-hardening`, `skill://a25-resilience--probe-tuning` or `skill://a25-resilience--graceful-shutdown`. Security checks of the same change belong to A10.
- The gate is evidence (L2); waivers and production rollouts are L3 and human.

## Procedure

1. Run `python3 scripts/resil_manifest_check.py --task-id $TASK --correlation-id $CORR --root . --path <changed-dir> --json` once per changed directory (render Helm or kustomize output to a directory first with `bash`, never into `.swarm/`). Exit 0 is `ok`, exit 1 is `fail` (at least one blocker or major, any skipped entry, or zero files analysed), exit 2 is an input error (`--path` outside the root or missing): fix the path.
2. Record `status`, `counts`, `workloads_checked`, `pdbs_found`, `files_scanned` and `skipped` from the `scripts/resil_manifest_check.py` output. `files_scanned` of 0, `workloads_checked` of 0 for a change that touches workloads, or any `skipped` entry such as `yaml:skipped:tool-missing`, means the scan did not cover the change: the verdict is `unknown`, never `pass`.
3. Get the baseline with `read`: the findings of the last `resilience.report` for the service (the handover artifact digest names the file); compare by the pair (`id`, `workload`), where findings absent from it are `new`. No baseline means every blocker and major counts as new. Call `shared_open_findings` with `{task_id}` to list failing gate verdicts other gates already hold on this task (stale ones are replaced by newer verdicts): quote them in the verdict, never overrule them.
4. Apply the rule to the `scripts/resil_manifest_check.py` findings: every `blocker` blocks, baseline or not (a known blocker is no exemption); a `major` absent from the baseline blocks; a `major` already in the baseline is listed and carried forward with its age, not blocking; minor and info are listed only.
5. Re-verify the blocking findings with the tools instead of trusting one source. For `pdb-*`: `resil_pdb_check` with `{workloads: [{name, kind, replicas, min_available | max_unavailable}]}` using the HPA `minReplicas` as `replicas` when an HPA exists. For probe ids: `resil_probe_check` with the container probes and `serves_traffic`. For a changed replicated StatefulSet: `resil_quorum_safety` (`workload`, `replicas`, `members_per_zone`, `unavailable_members`). Same id and severity is confirmed; a missing or different one is E-CONTRACT, report both and do not block on the unconfirmed finding alone.
6. A waiver is a human decision: it needs the finding id, workload, an owner and an expiry date, and for production it is L3. Never write a waiver yourself; list the finding as `waiver-requested` and BLOCKED needs=human-approval.
7. Write the verdict with `write` to `resilience-reports/<service>/gate-<task_id>.md`: `pass`, `block` or `unknown`, new findings with `location`, baseline reference, the tools that confirmed each. Call `shared_artifact_digest` with `{path}`.
8. Keep the check cheap: it reads files with `read` and `grep` only. It is not a substitute for a staging experiment; say that in the report when the change alters PDBs, spread or quorum.

## Outputs

- `resilience.report`: gate verdict with new findings, confirmation per finding, baseline digest and file digest.

## Failure Handling

- INVALID_INPUT from a `resil_` tool: fix the named `field` (budget fields exclusive, zone sum) or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; the verdict is `unknown` for the part not covered. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Script exit 2, or a scan that failed for skipped entries or zero files analysed: `unknown`, never `pass`. Manifests only partly rendered: say which.
- Never edit the manifests in this skill, never waive, never retry a rollout.

## Handover

`to_agent` A12 (consumes `resilience.report`). `goal`: release or hold on the gate verdict. `completed`, `decisions` (verdict, blocking ids), `artifacts` (verdict file with digest, producer A25), `open_questions` (waivers, uncovered files), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.
