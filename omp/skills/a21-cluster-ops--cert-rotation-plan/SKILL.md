---
name: "a21-cluster-ops--cert-rotation-plan"
description: "Use when control-plane or kubelet certificates are near expiry and need a bucketed expiry report and an ordered rotation plan with window and quorum guard."
disable-model-invocation: true
---

# Certificate rotation plan

## When to Use

- A scheduled check, an upgrade preflight or an `incident.alert` ("x509: certificate has expired") concerns the cluster's own PKI: apiserver, apiserver-kubelet-client, front-proxy, etcd server/peer/healthcheck-client, scheduler and controller-manager kubeconfigs, kubelet client and serving certificates, and the CAs.
- Cluster PKI only. Application TLS (ingress, mesh, cert-manager issuers) is A23 or A24. Managed control planes rotate their own: record `skipped:not-applicable` and check only node certificates. Renewal on a non-prod cluster is L2, production L3; replacing a CA is a cluster-wide trust change and, where it forces a rebuild, L4.

## Procedure

1. Collect expiries read-only: `kubeadm certs check-expiration` on each control-plane node (kubeadm leaf certificates are valid one year, CAs ten), or `openssl x509 -noout -enddate -in <cert>` on the file named by the task. Keep `name`, `not_after` and `is_ca`; take `now` from `date -u +%Y-%m-%dT%H:%M:%SZ`. Never copy a private key, token or kubeconfig content into any artifact.
2. Call `k8s_cert_expiry` with `now` and `certs` (`{name, not_after, not_before, is_ca}`; names unique). House buckets for a leaf: `critical` under 7 days, `warning` under 30, `notice` under 90; for a CA: 30, 180 and 365 days; a certificate is `expired` only after its `not_after` instant (RFC 5280: valid through `notAfter`, inclusive). `expired` or `critical` (counts and `verdict: critical`) means rotate now, except that a `not-yet-valid` finding also makes the verdict `critical`: read the findings before rotating (step 3). `attention` (a `warning`) means rotate in the next window; `ok` closes.
3. Cross-check the `k8s_cert_expiry` `days_remaining` of `nearest_expiry` against the raw `not_after` minus `now` by hand within 0.01 days (the tool truncates to hundredths of a day, never rounds up); a `not-yet-valid` finding (`not_before` after `now`) is clock skew, not expiry: report it, fix time sync first, and do not rotate on it.
4. Order the work with `write` notes for each certificate: leaf certificates first (`kubeadm certs renew all` renews leaves from the existing CAs, then the apiserver, controller-manager, scheduler and etcd static pods must be restarted one control-plane node at a time); kubelet client certificates rotate by themselves when `rotateCertificates` is true, so an expired kubelet certificate means that setting or the CSR approval is broken, not a manual renewal; a CA nearing expiry needs a staged trust-bundle rollover, which is a separate approved change plan.
5. Quorum guard before any control-plane restart: call `k8s_etcd_health` with the live numbers and `members`; `tolerable_failures` must be 1 or more and `verdict` not `critical`, because each restart removes one voter for a moment. Otherwise BLOCKED until `skill://a21-cluster-ops--etcd-health-review` clears it. Check also with `k8s_version_skew` that only one apiserver minor is live (`apiserver_oldest` equals `apiserver_newest`) so a restart does not coincide with an upgrade.
6. Call `k8s_maintenance_window` with `proposed_start`, `duration_minutes` (about 15 minutes per control-plane node unless the task states another figure) and the task's `windows` and `blackouts`. A `critical` or `expired` bucket may justify an emergency window, but only a human grants it: `shared_escalate` with `{task_id, reason_code: "policy", summary: "prod cert rotation awaiting L3 approval", needs: ["human-approval"]}`.
7. Write the plan with `write` to `cluster-reports/<cluster>/cert-rotation.md`: per certificate `bucket`, `days_remaining`, action, node order, verification (`kubeadm certs check-expiration` again, nodes Ready, apiserver `/readyz`), rollback (backup of `/etc/kubernetes/pki` taken by a human before the change). Digest it with `shared_artifact_digest`.

## Outputs

- `cluster.upgrade.plan` (rotation variant): bucket table, node order, window, guards, finding ids, file digest.

## Failure Handling

- INVALID_INPUT (`not_after` not after `not_before`, duplicate names, bad timestamp): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; name the certificates not assessed. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`; never mark certificates `ok` unchecked.
- The kubeadm one-year and ten-year lifetimes are defaults; clusters from other installers differ. The bucket limits come from the tool; defer to them and to the dates you read.
- Never run `kubeadm certs renew`, restart a static pod or delete a secret; propose and hand over. L3/L4: BLOCKED needs=human-approval.

## Handover

`to_agent` A14 (A12 and A22 read the same plan). `goal` (rotate the certificates in the stated order within the window), `completed`, `decisions` (buckets, node order, window), `artifacts` (plan, digest, producer A21), `blockers` (`human-approval`, quorum guard), `constraints` (one control-plane node at a time). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a21-cluster-ops/cert-rotation-plan.md; edit the source and run scripts/build_agents.py -->
