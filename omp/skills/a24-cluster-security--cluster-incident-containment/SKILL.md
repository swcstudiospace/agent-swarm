---
name: "a24-cluster-security--cluster-incident-containment"
description: "Use when a runtime alert or suspected workload compromise needs evidence kept, blast radius sized and a containment plan prepared for human approval."
disable-model-invocation: true
---

# Cluster incident containment

## When to Use

- A Falco alert, A10 `vulnerability.report` finding with exploitation, or an owner report points to a compromised pod, node or service account in a cluster.
- A24 preserves evidence read-only (L2) and PREPARES containment. Any containment change (labels, NetworkPolicy, cordon) in production is L3; deleting a pod, account, Secret or namespace is L4 and destroys evidence. Declaring the incident is A13/A01, forensics and software triage are A10, node work is A21. Rule tuning afterwards is `skill://a24-cluster-security--runtime-detection-tuning`.

## Procedure

1. Take the explicit `--context`, namespace, pod and alert id from the task; missing: BLOCKED needs=input. Preserve evidence first, read-only through `bash`: `kubectl --context <ctx> -n <ns> get pod <pod> -o yaml`, `logs --all-containers --previous`, `get events`, and the node name. Never print Secret `data`. Digest each saved file under `cluster-reports/<cluster>/incident-<id>/` with `shared_artifact_digest`.
2. Size the blast radius of the pod's identity: call `kpol_rbac_effective_access` with `roles`, `bindings` (live, read-only) and `subject` `{kind: "ServiceAccount", name, namespace}`, `checks` for `get` and `list` on `secrets`, `create` on `pods` and `pods/exec`, and `*` on `*`. `access_level` `cluster-admin` or `cluster-wide` makes this a cluster-level incident: say so in the first line of the result.
3. Check which evidence exists: call `kpol_audit_policy_check` with the live audit `policy`. A finding `audit.pod-exec.under-audited`, `audit.secrets.under-audited` or `audit.service-account-tokens.under-audited` means the audit log cannot answer who used the account: record that gap; do not infer activity.
4. Check lateral reach: call `kpol_tenant_isolation_check` with `tenant_namespaces` (the namespace and its neighbours), `manifests` and `required_psa_level`. A finding `tenant.ingress-default-deny-missing` or `tenant.cross-tenant-binding` means the attacker can reach other tenants. Call `kpol_secret_exposure_check` with the pod's `workloads` to list secrets in env vars or loose modes that are now to be treated as exposed.
5. Prepare containment as manifests only, least destructive first: a NetworkPolicy that selects the pod by a quarantine label and denies ingress and egress; relabel the pod so its Deployment starts a clean replacement and the Service stops routing to it (the original keeps running for forensics); `kubectl cordon <node>` when node compromise is possible. Draft them with `write`; check with `kubectl --dry-run=server` on the same context. Pod and node moves are for A21. Do not delete, restart or scale anything.
6. With `write`, prepare the credential list for rotation (service-account tokens by TokenRequest expiry, secrets from step 4, image pull credentials) as a list of names, never values. Rotation is performed by the owners.
7. Call `shared_escalate` with `{task_id, reason_code: "policy", summary: "containment of <pod> in <ns>: quarantine NetworkPolicy and relabel, L3", needs: ["human-approval", "incident commander"]}`. In production wait for the reply; a delete request goes as a separate L4 line in `needs`. Cross-check the plan against steps 2 to 4: every reachable namespace in `tenant.*` findings is covered by a policy, or named as open.

## Outputs

- `cluster.security.findings` (incident): blast radius (`access_level`, reachable tenants), evidence gaps, exposed secrets by name, each with evidence digests.
- `cluster.policy.set` (containment): the quarantine manifests, awaiting L3 approval.

## Failure Handling

- INVALID_INPUT (`subject.namespace` missing, bad `policy`): fix the named field once or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unsized subjects are listed as unknown, never as safe. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tools see manifests and policies, not what the attacker did. Do not state root cause or attribution; give facts and gaps. Sources disagreeing (alert versus audit log): E-CONTRACT, report both.
- Containment applies are non-idempotent: never retry; L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A10 (incident evidence and gate); A13 declares the incident and A21 executes node actions. Fill `goal`, `completed`, `decisions`, `artifacts` (evidence files and digests), `blockers` (`human-approval`), `open_questions` (audit gaps), `constraints` (do not delete the pod). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/cluster-incident-containment.md; edit the source and run scripts/build_agents.py -->
