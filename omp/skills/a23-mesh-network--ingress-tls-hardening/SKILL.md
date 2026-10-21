---
name: "a23-mesh-network--ingress-tls-hardening"
description: "Use when ingress or gateway listeners need a TLS review: version floor, cipher suites, certificate SANs, HSTS and plaintext listeners, with an ordered fix list."
disable-model-invocation: true
---

# Ingress TLS hardening

## When to Use

- A task, an A10 finding or a periodic review asks whether public listeners (Gateway, Ingress, Istio ingress gateway) meet the TLS baseline.
- Producing the hardened settings is L2; changing a production listener is L3 (a wrong version floor or cipher list can lock out clients).
- Not for certificate issuance or rotation (A21 `cert-rotation-plan`), not for route migration (`skill://a23-mesh-network--gateway-api-migration`).

## Procedure

1. Inventory every listener with `read`/`grep` over the Gateway, Ingress and ingress-controller ConfigMap manifests, or `kubectl --context <ctx> -n <ns> get gateway,ingress -o json`. Per listener note: `name`, `hostnames`, whether it terminates TLS, TLS version bounds, cipher list, certificate secret name and its SANs (read the certificate from the secret with `kubectl ... get secret -o jsonpath` only to extract SANs, never print key material), HSTS header, HTTP-to-HTTPS redirect. Fields the manifests do not show are unknown: BLOCKED needs=input, never "defaults".
2. Call `mesh_ingress_tls` with `listeners` as `[{name, hostnames, terminates_tls, min_tls_version, max_tls_version, cipher_suites, certificate_name, certificate_sans, hsts_max_age_s, hsts_include_subdomains, hsts_preload, redirect_http_to_https}]`. `min_tls_version` values are `SSLv3`, `TLSv1.0` to `TLSv1.3` (as the tool accepts; fix the named field on INVALID_INPUT). Keep `listener_count`, `tls_listener_count` and the findings.
3. Rank by severity. Blocker: `TLS-VERSION-SSL3`. Major: `TLS-VERSION-LEGACY` (TLS 1.0/1.1 accepted, deprecated by RFC 8996), `TLS-MAX-LEGACY`, `TLS-CIPHER-WEAK` (RC4, RC2, DES/3DES, NULL, EXPORT, anonymous, MD5), `TLS-NO-CERT`, `TLS-SAN-MISMATCH`, `TLS-HSTS-PRELOAD-INVALID` (preload without a one-year max-age or without includeSubDomains, including when max-age is absent). Minor: `TLS-CIPHER-NO-PFS`, `TLS-CIPHER-CBC`, `TLS-HSTS-MISSING`, `TLS-HSTS-SHORT`, `TLS-HSTS-DISABLED`, `TLS-PLAINTEXT-LISTENER`, `TLS-NO-HTTP-REDIRECT`. Info: `TLS-MIN-VERSION-UNSET`, `TLS-CIPHER-UNKNOWN`.
   Order the fix list worst first from the `mesh_ingress_tls` findings.
4. Target the baseline: floor TLS 1.2 (TLS 1.3 where every client supports it; with a 1.3 floor the cipher list no longer applies), AEAD ciphers with ECDHE only (`ECDHE-ECDSA|RSA-AES128|256-GCM-SHA256|384`, `CHACHA20-POLY1305`), certificate SANs covering every hostname (a wildcard SAN covers exactly one label and not the bare domain, RFC 6125), HSTS `max-age` at least 31536000 (one year) with `includeSubDomains`, and `preload` only with both (hstspreload.org), HTTP redirected to HTTPS with a 301 or 308.
   Re-check every proposed value with `mesh_ingress_tls` in step 7.
5. Check client compatibility before raising the floor: ask for the TLS version and cipher distribution from access logs (A13 telemetry). If more than 1 percent of requests use TLS below the new floor, the change is staged and the owner decides; no data is BLOCKED needs=input.
   Count the legacy-TLS share with `grep` over the exported access-log sample and record the percentage.
6. For Gateway API listeners, also call `mesh_gateway_api_check` with `gateways`, `routes`, `reference_grants`: `GW-LISTENER-TLS-MISSING` and `GW-CERT-NO-GRANT` (certificate in another namespace without a ReferenceGrant) must agree with `TLS-NO-CERT` from step 2 for the same listener. Contradicting answers are E-CONTRACT.
7. Re-run `mesh_ingress_tls` on the proposed listeners: verdict `pass` (a `warn` is acceptable only when each minor is justified in the plan; no major, no blocker). The port 80 listener must be redirect-only (`terminates_tls` false, `redirect_http_to_https` true): that raises no `TLS-PLAINTEXT-LISTENER`; any other plaintext listener does. Run `python3 scripts/mesh_policy_check.py --task-id $TASK --correlation-id $CORR --root <repo> --path <manifest dir> --json` as the repository check of the `gateway-references` result.
8. Write the ordered fix list and rollback (previous listener config revision) to `mesh-reports/<cluster>/ingress-tls.md` with `write`; digest with `shared_artifact_digest`. Production change: `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod listener TLS change awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `mesh.policy.set` (ingress TLS variant): per-listener before/after findings, hardened settings, client-impact note, rollback.

## Failure Handling

- INVALID_INPUT (unknown TLS version string, `max_tls_version` below `min_tls_version`, no `name`): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; unchecked listeners are not hardened. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- Cipher-name conventions differ (OpenSSL vs IANA names); unrecognised names give `TLS-CIPHER-UNKNOWN`: check them against the controller's documentation, do not assume safe.
- mTLS to clients (`MUTUAL`), OCSP stapling, and certificate expiry are not covered by the tool: list as gaps. Never retry a listener change.

## Handover

- `to_agent`: A10 (security reviews the baseline) and A22 (manifests). `goal`: listeners in scope. `completed`, `decisions` (floor, HSTS values), `artifacts` (fix list, digests), `open_questions` (client versions), `verification`.
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/ingress-tls-hardening.md; edit the source and run scripts/build_agents.py -->
