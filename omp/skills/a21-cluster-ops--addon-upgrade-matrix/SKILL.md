---
name: "a21-cluster-ops--addon-upgrade-matrix"
description: "Use when an upgrade path needs a compatibility matrix of CNI, CSI, ingress, DNS and kube-proxy add-ons, and the first control-plane minor each must be swapped."
disable-model-invocation: true
---

# Add-on upgrade matrix

## When to Use

- A `cluster.upgrade` from `current_version` to `target_version` is planned and the add-ons running on the cluster (CNI, CSI drivers, ingress controller, CoreDNS, kube-proxy, metrics or other) must each support every minor on the way.
- Compatibility only. Deploying the new add-on versions (Helm, manifests) is A22 (L2 non-prod, L3 prod); the network data-plane behaviour of a CNI or ingress change is A23. The whole ordering is `skill://a21-cluster-ops--cluster-upgrade-plan`.

## Procedure

1. Inventory the add-ons read-only with explicit `--context`: `kubectl get ds,deploy -n kube-system -o json`, installed CSI drivers (`kubectl get csidrivers`), the ingress controller deployment and its image tags, and the Helm release list from the task. Record for each `name`, `kind` (`cni`, `csi`, `ingress`, `dns`, `proxy`, `other`) and installed `version`.
2. Get each add-on's supported Kubernetes range from its OWN release compatibility table (the vendor docs or release notes the task points to, read with `read`). Put it in `min_k8s` and `max_k8s` as `1.NN`; for the upgrade candidate give `candidate_version`, `candidate_min_k8s`, `candidate_max_k8s` ALL together. A range you cannot cite: BLOCKED needs=input for that add-on; never fill a range from memory.
3. Call `k8s_addon_compatibility` with `current_version`, `target_version` and `addons` (names unique). Per add-on `status`: `compatible` (covers every minor from current to target), `upgrade_required` (the installed version fails at `first_incompatible`; `upgrade_at` is the earliest control-plane minor at which the candidate can replace it while installed and candidate ranges still overlap the path; a `major` finding when the installed version already fails at the running minor, reading `now`, otherwise a `minor`), `blocked` (no candidate, the candidate does not cover the rest of the path, or no minor on the path supports both the installed and the candidate version, so the swap needs a separate migration: a `blocker`). Any `blocked` makes `verdict: blocked` and stops the upgrade.
4. Place each `upgrade_required` add-on in the plan: call `k8s_upgrade_plan` with `current_version`, `target_version` and the pool list; its `addons` phase step comes after the control-plane hops. An add-on whose `upgrade_at` equals `current_version` must move BEFORE the first hop (the `minor` finding reads `before the control-plane upgrade starts`; a live add-on's `major` finding reads `now, while the control plane already runs at X`); one with a later `upgrade_at` must move while the control plane is at that minor, which is between hops. Order the matrix by `upgrade_at`, then by dependency (CNI before anything that needs pod networking, DNS after CNI).
5. Cross-check with `k8s_version_skew`: kube-proxy is checked there as a component (`kind: kube-proxy`, with its `node`): it must not be newer than the oldest apiserver and at most 3 minors older (2 below 1.25). A kube-proxy `compatible` in the matrix but a skew `major` is a disagreement in what the version means (image tag versus supported range): E-CONTRACT, report both.
6. Cross-check the `k8s_addon_compatibility` counts: `compatible_count + upgrade_required_count + blocked_count` must equal the number of add-ons sent. For managed add-ons (EKS add-ons, GKE-managed CNI) the provider's version set decides; defer to the tool run on the provider's stated range and say so.
7. Write the matrix with `write` to `cluster-reports/<cluster>/addon-matrix.md`: add-on, kind, installed, status, `upgrade_at`, candidate, source of the range (URL or doc title). Digest it with `shared_artifact_digest`. Nothing is installed here.

## Outputs

- `cluster.upgrade.plan` fragment: the add-on matrix with order, blockers and sources, plus the file digest.

## Failure Handling

- INVALID_INPUT (`min_k8s` above `max_k8s`, partial candidate triple, more than 6 minors, duplicate add-on): fix the named `field` or BLOCKED needs=input.
- PARTIAL_SUCCESS: use `completed_fraction`; DEPENDENCY_UNAVAILABLE: retry once, then `skipped:tool-missing`.
- The tool compares minor ranges only: it knows nothing about add-on feature flags, CRD migrations or CNI datapath changes. State that and ask A22/A23 to review those for each `upgrade_required` add-on.
- Never guess a supported range, never apply a version. L3 for production add-on changes.

## Handover

`to_agent` A22 (A12 and A14 read the same plan). `goal` (upgrade the add-ons in the stated order), `completed`, `decisions` (order and `upgrade_at` per add-on), `artifacts` (matrix, digest, producer A21), `open_questions` (ranges without a source), `blockers` (blocked add-ons). Call `shared_handover_validate`, then `shared_handover_write`.

<!-- generated from prompts/skills/a21-cluster-ops/addon-upgrade-matrix.md; edit the source and run scripts/build_agents.py -->
