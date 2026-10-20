---
name: "a24-cluster-security--audit-logging-setup"
description: "Use when a kube-apiserver audit policy must be written or verified so secrets, tokens, RBAC, exec and admission changes are logged at the right level."
disable-model-invocation: true
---

# Audit logging setup

## When to Use

- A `kpol.runtime` task: a cluster has no audit policy, an audit policy changed (`iac.change` from A11), or an incident needs to know whether evidence exists.
- Policy design and verification are L2. Changing `kube-apiserver` flags or the policy file restarts the control plane: production L3, and on managed clusters it is a provider setting set by A11/A21. Detection rules are `skill://a24-cluster-security--runtime-detection-tuning`.

## Procedure

1. Read the current policy with `read`: the file named by the kube-apiserver `--audit-policy-file`, or the exported object (`apiVersion: audit.k8s.io/v1`, `kind: Policy`) from the task. Note the backend flags (`--audit-log-path`, `--audit-log-maxage`, `--audit-log-maxbackup`, `--audit-log-maxsize`, or `--audit-webhook-config-file`). No policy and no flags: say audit is off and continue to step 3 with a draft.
2. Call `kpol_audit_policy_check` with `policy` (the parsed object). Read `rule_count`, `failed_probes` (how many probe families have a finding), `finding_count`, `verdict`. The probes are `secrets`, `service-account-tokens`, `token-reviews`, `rbac-writes`, `pod-exec`, `pod-changes`, `admission-config`, `namespace-lifecycle`. Expected levels (this tool's house policy, derived from what each level logs): secret writes `Metadata` only (the request body holds the data: blocker), secret reads and TokenRequest at most `Request` (only `RequestResponse` logs the secrets or token returned: blocker); RBAC and admission writes at least `Request`; exec, attach and portforward at least `Metadata`; TokenReview not above `Metadata`.
3. Fix the `kpol_audit_policy_check` ids in order: `audit.nothing-logged` and `audit.no-catch-all` (first match wins, so end with a catch-all `level: Metadata` rule), `audit.unreachable-rule` (a rule shadowed by an earlier one: reorder, never delete a rule to silence it), `audit.request-received-logged` (add `omitStages: ["RequestReceived"]` to cut volume), `audit.managed-fields-logged` (set `omitManagedFields: true`).
4. Draft or amend the policy file with `write` (one rule per concern, most specific first, then the catch-all) and re-run `kpol_audit_policy_check`; `failed_probes` must be 0 and `verdict` pass before it is proposed. The draft is a proposal: do not install it.
5. Cross-check exposure: call `kpol_secret_exposure_check` with `workloads` from `gitops.manifests` and `encryption_config` when supplied. Audit hides secret bodies, but encryption at rest is independent: `secret.encryption-missing` or `secret.encryption-identity-first` are blocker findings that audit does not mitigate.
6. After an approved install (not by you), prove it on a non-prod cluster with `bash`: read a test secret with `kubectl --context <ctx> -n <ns> get secret <name>`, then find the event in the log with `grep` for the user, verb `get` and resource `secrets`: it must show `"level":"Metadata"` and no `responseObject`. A log that disagrees with the tool result is E-CONTRACT: report both and trust the log for that cluster.
7. For production, call `shared_escalate` with `{task_id, reason_code: "policy", summary: "audit policy change on <cluster> apiserver, L3", needs: ["human-approval"]}`. Also state retention and size: `--audit-log-maxage` days, `--audit-log-maxbackup`, and the log shipping target; storage and cost are A20/A21 matters. Digest `cluster-reports/<cluster>/audit-policy.md` and the draft policy with `shared_artifact_digest`.

## Outputs

- `cluster.policy.set` (audit variant): the proposed policy, probe results, backend settings.
- `cluster.security.findings`: audit and secret-storage findings.

## Failure Handling

- INVALID_INPUT (`policy` not an object, wrong `kind`): fix the named field or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unprobed families are gaps. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool simulates fixed probe requests; it does not know your CRDs or user-specific rules. The apiserver flag syntax (`--audit-policy-file`, dynamic backends) is by version: defer to the cluster's own docs and say so.
- Control-plane changes are non-idempotent: never retry. L3 or L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A11 (infrastructure change, `iac.change`); A10 gets the findings and A21 the control-plane implications. Fill `goal`, `completed`, `decisions`, `artifacts` with digests, `blockers` (`human-approval`), `verification`. Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a24-cluster-security/audit-logging-setup.md; edit the source and run scripts/build_agents.py -->
