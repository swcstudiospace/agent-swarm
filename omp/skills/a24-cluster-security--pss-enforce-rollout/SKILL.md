---
name: "a24-cluster-security--pss-enforce-rollout"
description: "Use when namespaces must move to Pod Security Admission baseline or restricted and a staged warn/audit then enforce plan with workload evidence is needed."
disable-model-invocation: true
---

# Pod Security Standards enforce rollout

## When to Use

- A `kpol.admission` task asks to raise one or more namespaces to `baseline` or `restricted` (Pod Security Admission labels `pod-security.kubernetes.io/{enforce,warn,audit}`), using `gitops.manifests` from A22 as input.
- Plan, evidence and a label diff only. Authoring custom policy is `skill://a24-cluster-security--admission-policy-authoring`; tenant-wide isolation is `skill://a24-cluster-security--tenant-isolation-audit`.
- Reading and warn/audit staging are L2. Setting `enforce` is an audit-to-enforce flip: L3 (human approval) in every cluster, production first in line. A24 never runs the label change on a production context.

## Procedure

1. Take the explicit `--context`, the namespace list and the manifest directory (`gitops.manifests`) from the task; none given: BLOCKED needs=input. Read the server version from `cluster.fleet.status` (PSA is stable since 1.25); below 1.25 stop and report.
2. Run `python3 scripts/kpol_manifest_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir> --level restricted --json`, then again with `--level baseline`. Exit 0 = status `ok` (no major or worse finding and every in-scope input analysed), exit 1 = `status: fail` (a result, not an error; skipped or unanalysed input counts), exit 2 = bad input. Note `skipped` and `limits_hit`: a `skipped:tool-missing` for `yaml` means YAML files were not scanned.
3. Call `kpol_pss_check` with `workloads` (the Pod, Deployment, StatefulSet, DaemonSet, Job and CronJob manifests of one namespace, at most 200) and `level` set to the target. Read `highest_compliant_level` (absent when no pod spec was checked: no level is claimed), `finding_count` and `verdict`. Cross-check: the `pss.*` ids and counts per object must equal those of `scripts/kpol_manifest_scan.py` at the same `--level`; a difference is E-CONTRACT.
4. Decide the target per namespace from `kpol_pss_check`: `restricted` when `highest_compliant_level` is `restricted`; `baseline` when it is `baseline`; `privileged` workloads stay out of scope until fixed; absent means the namespace is unchecked and gets no target. Blocker findings (`pss.privileged`, `pss.host-namespaces`, `pss.host-path`, `pss.host-process`) need a workload fix from the owner (A22), not an exception from you. Exceptions are `skill://a24-cluster-security--exception-lifecycle` and L4.
5. Stage the labels (L2): `warn` and `audit` first with `enforce-version` pinned to the server minor, for example `v1.30`. Validate with `bash`: `kubectl --context <ctx> label --dry-run=server --overwrite ns <ns> pod-security.kubernetes.io/enforce=<level>`. The server warns about every existing pod that would violate it; compare those pods with the workloads that have `kpol_pss_check` findings and list every difference as a gap (the version pin, exemptions and pods outside the manifests explain differences); never force the two to agree.
6. Call `kpol_tenant_isolation_check` with `tenant_namespaces`, `manifests` (the Namespace objects and quotas) and `required_psa_level`. After the plan, no namespace may show `tenant.psa-enforce-missing` or `tenant.psa-enforce-weak`; `tenant.psa-version-unpinned` (info) must be resolved by the version pin.
7. Flip rule (this skill's definition): `enforce` is requested only after `warn`/`audit` ran at least 7 days with zero new `pss.*` major or blocker findings on this level. Then call `shared_escalate` with `{task_id, reason_code: "policy", summary: "PSA enforce <level> on <ns> awaiting L3 approval", needs: ["human-approval"]}` and stop.
8. Write the label diff and evidence to `cluster-reports/<cluster>/pss-rollout.md` with `write`; add a rollback line (restore the previous label values). Digest it with `shared_artifact_digest`. A22 applies it through GitOps, never A24.

## Outputs

- `cluster.policy.set` (PSA variant): per-namespace target level, label diff, scan and tool counts, flip approval state, rollback.
- `cluster.security.findings` for remaining `pss.*` findings, severity as reported.

## Failure Handling

- INVALID_INPUT (`workloads` empty or over 200, bad `level`): fix the named field or BLOCKED needs=input. Split namespaces over 200 workloads.
- PARTIAL_SUCCESS: use `completed_fraction`; unscanned namespaces stay at their current level. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- PSA exempts users, runtimeClasses and namespaces through the apiserver AdmissionConfiguration; the tools do not read it. List this gap, do not call a namespace enforced without it.
- Flip and label changes are non-idempotent: never retry them. L3 or L4 action: BLOCKED needs=human-approval.

## Handover

`to_agent` A22 (applies the labels, owns the workloads); A10 receives the findings. Fill `goal`, `completed`, `decisions` (target per namespace, 7-day rule), `artifacts` (report and digest, producer A24), `blockers` (`human-approval`), `constraints` (version pin, rollback). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/pss-enforce-rollout.md; edit the source and run scripts/build_agents.py -->
