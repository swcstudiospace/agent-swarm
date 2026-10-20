---
name: "a23-mesh-network--traffic-shifting-plan"
description: "Use when a release needs a weighted canary or blue-green traffic.plan for a mesh service; validate weights, subsets and rollback before A12 promotes."
disable-model-invocation: true
---

# Traffic shifting plan

## When to Use

- A12 (release) or an `architecture.blueprint` asks for a staged shift of a service between versions inside the mesh.
- Producing the plan is L2. Executing a weight change on production is L3 and is done by a human or A12 under approval; this skill only plans and validates.
- Not for edge routing (`skill://a23-mesh-network--gateway-api-migration`), not for the release decision itself (A12).

## Procedure

1. Read the current routing: `read` the VirtualService and DestinationRule manifests (from `gitops.manifests` of A22) or `kubectl --context <ctx> -n <ns> get virtualservice,destinationrule -o json`. Name the host, the subsets (version labels) and the namespace; unknown host or version label is BLOCKED needs=input.
2. Define stages as weights that sum to 100 per route: default `5/95`, `25/75`, `50/50`, `100/0`. Use the `api.contract` to decide what may be sent to the new version (breaking changes: header- or cookie-based match first, weights second). Put the hold time and the abort metric per stage into the plan: abort when 5xx ratio of the canary subset exceeds the stable subset by more than 1 percentage point or p99 latency by more than 20 percent (this skill's default; the SLO of A13 overrides it).
   Write each stage as its own manifest file with `write`.
3. Call `mesh_traffic_split` with `virtual_services` and `destination_rules` for EVERY stage's manifest set. Per stage `verdict` must be `pass`. Findings that block: `TRAFFIC-WEIGHT-ZERO` (blocker: Istio rejects all-zero weights), `TRAFFIC-SUBSET-MISSING`, `TRAFFIC-SUBSET-NO-DESTINATIONRULE`, `TRAFFIC-PERTRY-EXCEEDS-TIMEOUT`, `TRAFFIC-DURATION-INVALID` (0s or sub-millisecond timeouts) and `TRAFFIC-RETRY-DISABLED-CONFIG` (all major). `TRAFFIC-WEIGHT-SUM` (minor) means the weights do not add up to 100: Istio treats weights as relative shares (the finding prints the effective split), so keep every stage at 100 to make it read as percentages. Other minors (`TRAFFIC-RETRY-NO-TIMEOUT`, `TRAFFIC-RETRY-ATTEMPTS-HIGH` above 5, a house ceiling) must be fixed or justified in the plan.
4. Call `mesh_destination_policy` with the same `destination_rules` and `workloads` (`[{namespace, service, labels}]`, the endpoints behind each subset). `DR-SUBSET-NO-ENDPOINTS` (no pod carries the labels) is major: a weight to an empty subset is a 100 percent error for that share. `DR-NO-OUTLIER-DETECTION` on the canary subset is a minor to fix, because an unhealthy canary should eject.
5. Cross-check the two tools: every `subset` named in a route of step 3 must have `subset_count` coverage in step 4 (`DR-SUBSET-*` absent). `TRAFFIC-SUBSET-MISSING` and `DR-SUBSET-NO-ENDPOINTS` can both be true (label exists in the rule but on no pod): fix labels in the Deployment, not the weight.
   Compare `mesh_destination_policy` ids (`DR-SUBSET-*`) with `mesh_traffic_split` ids.
6. Check retries on the shifted path: call `mesh_retry_amplification` with `calls` (the caller-to-callee chain through the service, each `{caller, callee, retries}`) and `max_multiplier` 2 (default of this skill unless the task gives another). A canary that fails every attempt must not push the chain over budget: `over_budget` true is a blocker for the plan.
7. Capacity: take `cluster.capacity.report` (A21). The new subset must fit at its stage (`replicas` times resource requests, plus the old subset until 100/0). Over the reported headroom is BLOCKED needs=input for A21; never invent figures.
   Read the capacity figures with `read` from the A21 report artifact.
8. Write the plan with `write` to `mesh-reports/<cluster>/traffic-<service>.md`: stages and weights, hold times, abort rule, rollback (set the stable subset to 100, re-validate with `mesh_traffic_split`), owners. Digest it and the per-stage manifests with `shared_artifact_digest`. Production: call `shared_escalate` with `{task_id, "reason_code": "policy", "summary": "prod traffic shift awaiting L3 approval", "needs": ["human-approval"]}`.

## Outputs

- `traffic.plan`: stages, validated manifests per stage, abort and rollback rules, capacity note, digests.

## Failure Handling

- INVALID_INPUT (weight not an integer, `host` missing): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; stages not checked are not approved. DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing` and the plan stays draft.
- Gateway API weighted `backendRefs` (HTTPRoute) are not read by `mesh_traffic_split`: for those use `mesh_gateway_api_check` via `skill://a23-mesh-network--gateway-api-migration` and say so.
- Never retry a weight change; a failed stage means roll back to the stable subset under approval.

## Handover

- `to_agent`: A12. `goal`: service, versions, final stage. `completed`, `decisions` (stage weights, abort thresholds), `artifacts` (plan, manifests, digests, producer A23), `open_questions` (capacity, SLO owner), `verification` (per-stage verdicts).
- Tools: `shared_handover_validate` then `shared_handover_write`.

<!-- generated from prompts/skills/a23-mesh-network/traffic-shifting-plan.md; edit the source and run scripts/build_agents.py -->
