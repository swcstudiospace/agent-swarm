---
name: a23-mesh-network--dns-tuning
description: Use when pods show slow or noisy DNS from ndots and search domains; count lookups per name, pick ndots or trailing dots, and judge a node-local cache.
owner: a23-mesh-network
tools: [mesh_dns_ndots, mesh_egress_coverage, shared_artifact_digest, shared_handover_validate, shared_handover_write]
---

# DNS tuning

## When to Use

- CoreDNS load, NXDOMAIN rates or tail latency point at resolver behaviour of a workload, or a new service resolves many external names.
- Design is L2. Changing `dnsConfig` on production workloads is a production mesh/network change (L3); a CoreDNS or NodeLocal DNSCache change belongs with A21 and is also L3.
- Not for egress allow-lists (`skill://a23-mesh-network--egress-lockdown`).

## Procedure

1. Read the pod's real resolver settings: `kubectl --context <ctx> -n <ns> exec <pod> -- cat /etc/resolv.conf` is a live command, so ask for it as input unless the task authorises it; otherwise `read` the pod spec (`dnsPolicy`, `dnsConfig.options` ndots, `dnsConfig.searches`). The ClusterFirst default is `ndots:5` and the search list `<ns>.svc.<cluster-domain>`, `svc.<cluster-domain>`, `<cluster-domain>`, plus any node search domains. Missing data: BLOCKED needs=input.
2. Collect the names the pod resolves from application config, `grep` over env and ConfigMaps, and resolver logs, each tagged `target`: `cluster` (a Service name) or `external` (a public host). Include the top 20 by request rate; omitted names bias the result.
3. Call `mesh_dns_ndots` with `ndots`, `namespace`, `search_domains`, `names` (`[{name, target}]`) and `cluster_domain` (default `cluster.local`). Keep `total_lookups`, `wasted_lookups`, `best_ndots`, `best_total_lookups` and the per-name `lookups`. At ndots 5 an external name like `api.example.com` (2 dots) costs 4 lookups: three failed search-domain tries, then the absolute name.
4. Findings: `DNS-SEARCH-LIMIT` (major: more than 32 search domains or 2048 characters, the Pod `dnsConfig` validation limits), `DNS-NDOTS-WASTE` (minor: an external name with fewer dots than ndots), `DNS-SHORT-NAME-WASTE` (minor: ndots so high that even in-cluster short names are tried as written first), `DNS-NDOTS-TUNE` (info: a better value exists).
   Every finding id comes from `mesh_dns_ndots`; the limits are Pod validation constants (`MaxDNSSearchPaths`, `MaxDNSSearchListChars`).
5. Choose the fix by cost to the application. Preferred: write external names as FQDNs with a trailing dot (no change to ndots, zero risk to short in-cluster names). Next: lower ndots to `best_ndots` through `dnsConfig.options: [{name: ndots, value: "2"}]`, and re-run `mesh_dns_ndots` with the full `cluster` name list: a name with at least ndots dots is tried as written first, so each short in-cluster name (`svc.ns` has 1 dot) costs one extra failed lookup but still resolves through the search list; read the new `lookups` per name and accept only a net saving.
6. Cross-check external names against the mesh: call `mesh_egress_coverage` with those names as `destinations` (`host`, `port`, `protocol`), the ServiceEntries and `outbound_policy`; a name that resolves cleanly but is `EGRESS-BLOCKED` under REGISTRY_ONLY is a different problem (fix in `skill://a23-mesh-network--egress-lockdown`), not DNS.
7. Judge a node-local cache (NodeLocal DNSCache; its link-local listen address is chosen by the operator, the Kubernetes docs fix no default) only when `total_lookups` of the busiest workloads multiplied by their request rate is a real CoreDNS load (from A13 or A21 telemetry; a figure not supplied is BLOCKED needs=input). It reduces conntrack and cross-node DNS traffic but is an A21 cluster change: put it in the report as a recommendation, never apply it.
   Quote `total_lookups` from `mesh_dns_ndots` and the telemetry source in the report.
8. Write before/after per name (`lookups`, `wasted_lookups`) and the exact `dnsConfig` diff to `mesh-reports/<cluster>/dns-<workload>.md` with `write`, then `shared_artifact_digest`. Rollout by workload, one at a time, watching NXDOMAIN and error rates for 1 hour; production rollout is L3 approval.

## Outputs

- `mesh.policy.set` (DNS variant): `dnsConfig` diff, FQDN list, lookups before and after, cache recommendation.

## Failure Handling

- INVALID_INPUT (`target` not cluster or external, a name with spaces or `..`, `search_domains` not strings): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; names not analysed keep their old cost. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool counts lookups from names and settings, not measured latency: resolver caching, `single-request` options and IPv6 AAAA queries are not modelled; say the saving is an estimate.
- Never retry a rollout step; revert `dnsConfig` under approval.

## Handover

- `to_agent`: A22 (workload manifests); A21 when a node-local cache is recommended. `goal`: workloads and names. `completed`, `decisions` (FQDN vs ndots), `artifacts` (diff, report, digests), `open_questions` (missing names, rates), `verification` (re-run of the tool on the proposed settings).
- Tools: `shared_handover_validate` then `shared_handover_write`.
