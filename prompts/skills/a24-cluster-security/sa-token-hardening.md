---
name: a24-cluster-security--sa-token-hardening
description: Use when workloads mount service-account tokens they do not need, share the default account or use long-lived or legacy token Secrets.
owner: a24-cluster-security
tools: [kpol_sa_token_audit, kpol_rbac_effective_access, kpol_secret_exposure_check, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# Service-account token hardening

## When to Use

- A `kpol.rbac` task about token exposure: `automountServiceAccountToken` defaults, the `default` ServiceAccount, `kubernetes.io/service-account-token` Secrets, or projected token lifetimes.
- Audit and proposed diffs only (L2); changing running workloads is L3 in production; deleting a ServiceAccount or token Secret is L4. Permissions of the account are `skill://a24-cluster-security--rbac-least-privilege-review`.

## Procedure

1. Collect workload manifests (Pod, Deployment, StatefulSet, DaemonSet, Job, CronJob) from `gitops.manifests`, ServiceAccount manifests and Secrets of type `kubernetes.io/service-account-token`. Live read-only with `bash`: `kubectl --context <ctx> get sa,secret -n <ns> -o json`; never print Secret `data`. No context or namespace: BLOCKED needs=input.
2. Run `python3 scripts/kpol_manifest_scan.py --task-id $TASK --correlation-id $CORR --root <repo> --path <dir> --json` and keep the `sa.*` findings and their `location`.
3. Decide which workloads really need the API (`token_required`): from the task owner, or those whose ServiceAccount has a binding. Check the second source with `kpol_rbac_effective_access` (`roles`, `bindings`, `subject` `{kind: "ServiceAccount", name, namespace}`): `access_level` `none` and no binding means the token is dead weight.
4. Call `kpol_sa_token_audit` with `workloads`, `service_accounts` (at most 200) and `token_required` (`Kind/namespace/name`). Read `finding_count`, `verdict` and ids: `sa.api-access-default-account` (major: needs the API but shares `default`), `sa.automount-enabled` (major on the default account, minor otherwise), `sa.default-account` (minor), `sa.legacy-token-secret` (major: never expires), `sa.token-expiry-invalid` (blocker: `expirationSeconds` outside 600 to 4294967296), `sa.token-long-lived` (minor: above 86400, one day). Cross-check with `token_required` empty (the script has no such input): the same `sa.*` id set must appear in `scripts/kpol_manifest_scan.py`; a difference is E-CONTRACT.
5. With `write`, propose per workload: `automountServiceAccountToken: false` on the ServiceAccount and in the pod spec (the pod field wins); a dedicated ServiceAccount where API access is needed; a projected `serviceAccountToken` volume with `audience` set and `expirationSeconds` at most 3600 (this skill's choice; the tool flags above 86400). Since Kubernetes 1.24 tokens are no longer auto-created as Secrets, so any remaining token Secret is manual or an older leftover.
6. For legacy token Secrets: read the `kubernetes.io/legacy-token-last-used` label (maintained by the control plane on recent versions; absent means unknown, not unused) with `bash`. Never remove a Secret still in use. Removal is a delete: L4, hand it to a human with the last-used evidence; propose rotation by TokenRequest instead.
7. Call `kpol_secret_exposure_check` with the same `workloads` to catch tokens leaked as env vars or loose volume modes (`secret.env-secretkeyref`, `secret.world-readable`); these are additional findings, not substitutes. Write the diffs to `cluster-reports/<cluster>/sa-tokens.md` with `write` and digest them with `shared_artifact_digest`.

## Outputs

- `cluster.policy.set` (token variant): ServiceAccount and pod-spec diffs.
- `cluster.security.findings`: `sa.*` and `secret.*` findings with locations.

## Failure Handling

- INVALID_INPUT (`token_required` entry not matching a workload, more than 200 accounts): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; name unaudited namespaces. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Sidecars and operators that call the API are invisible to static manifests: when unsure the workload needs a token, keep it and ask the owner (`open_questions`).
- Never apply a change; never retry a delete request. L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A22 (applies diffs); A10 gets the findings. Fill `goal`, `completed`, `decisions` (token_required list and its source), `artifacts` with digests, `open_questions`, `blockers`. Call `shared_handover_validate`, then `shared_handover_write`.
