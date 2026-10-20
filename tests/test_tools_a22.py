"""Behaviour tests for the A22 GitOps & Progressive Delivery Engineer tools (gitops_*): kustomize, Helm values, Argo CD / Flux
applications and ApplicationSets, sync waves, prune risk, Argo Rollouts, canary verdicts, promotion gates, drift, images, secrets
and repository layout.

Every assertion is on output values for specific inputs, boundaries, INVALID_INPUT fields and callers. The registry
document is the live registry plus this lane's intake fragment while it is unmerged, so the file passes before and
after `scripts/_registry_intake.py --apply` merges and deletes the fragment.

tests/conftest.py evicts swarm* modules between tests: modules are resolved through importlib at call time.
"""
from __future__ import annotations

import copy
import functools
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A22", "a22-gitops", "gitops_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "gitops_kustomize_check",
    "gitops_helm_values_drift",
    "gitops_app_sync_policy",
    "gitops_appset_check",
    "gitops_sync_wave_check",
    "gitops_prune_risk",
    "gitops_rollout_check",
    "gitops_canary_verdict",
    "gitops_promotion_gate",
    "gitops_drift_classify",
    "gitops_image_policy",
    "gitops_secret_scan",
    "gitops_repo_layout",
]


def _registry():
    return importlib.import_module("swarm.tool_registry")


def _fragment_entries() -> list[dict]:
    """Entries still waiting in the lane's intake fragment(s): <slug>.tools.json and <slug>.<shard>.tools.json."""
    entries: list[dict] = []
    for path in sorted(INTAKE.glob(f"{SLUG}.*tools.json")):
        entries.extend(json.loads(path.read_text(encoding="utf-8")))
    return entries


@functools.cache
def lane_document() -> dict:
    """The registry plus this lane's fragment entries that are not merged yet."""
    document = copy.deepcopy(_registry().load_document())
    agent = next(a for a in document["agents"] if a["id"] == AGENT)
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(e) for e in _fragment_entries() if e["name"] not in present)
    return document


def lane_tools() -> list[dict]:
    return next(a for a in lane_document()["agents"] if a["id"] == AGENT)["registered_tools"]


def call(name: str, payload, caller: str = AGENT) -> dict:
    return _registry().call_tool(name, caller, payload, data=lane_document())


def ok(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


def invalid(name: str, payload, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result
    return result


# --- registration ---------------------------------------------------------------------------------------------


def test_lane_registers_exactly_its_tools_as_read_only_owner_calls():
    tools = lane_tools()
    assert [t["name"] for t in tools] == TOOL_NAMES
    assert 10 <= len(tools) <= 15
    for tool in tools:
        suffix = tool["name"][len(PREFIX):]
        assert tool["name"].startswith(PREFIX) and tool["owner"] == AGENT and tool["permitted_callers"] == [AGENT]
        assert tool["side_effect_tier"] == "READ" and tool["idempotency"] == "idempotent"
        assert tool["entrypoint"] == f"swarm/tools/{AGENT.lower()}_tools.py"
        assert tool["handler"] == f"swarm.tools.{AGENT.lower()}_tools:{suffix}"
        assert "probe" in tool["input_schema"]["properties"] and "|" not in tool["purpose"]
        assert tool["output_schema"]["properties"]["state"] == {"type": "string", "const": "SUCCESS"}


def test_lane_entries_validate_in_a_copy_of_the_registry(tmp_path):
    copy_path = tmp_path / "agents.json"
    copy_path.write_text(json.dumps(lane_document()), encoding="utf-8")
    errors = _registry().validate_registry(copy_path)
    mine = [e for e in errors if AGENT in e or any(name in e for name in TOOL_NAMES)]
    assert mine == []


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_refuses_other_callers_and_malformed_payloads(name):
    sample = next(t for t in lane_tools() if t["name"] == name)["examples"]["success"]
    assert call(name, sample, caller="A01")["state"] == "UNAUTHORIZED"
    assert call(name, {})["state"] == "INVALID_INPUT"
    assert call(name, [sample])["field"] == "payload"
    assert call(name, {**sample, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert call(name, {**sample, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


def severities(result: dict) -> dict:
    """finding id -> severity of a result."""
    return {f["id"]: f["severity"] for f in result["findings"]}


# --- gitops_kustomize_check -----------------------------------------------------------------------------------

CLUSTER_SCOPED = [
    "APIService", "CSIDriver", "CSINode", "CertificateSigningRequest", "ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition",
    "GatewayClass", "IngressClass", "MutatingWebhookConfiguration", "Namespace", "Node", "PersistentVolume", "PriorityClass",
    "RuntimeClass", "StorageClass", "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding", "ValidatingWebhookConfiguration",
    "VolumeAttachment",
]
NAMESPACED = ["ConfigMap", "Deployment", "HorizontalPodAutoscaler", "Ingress", "NetworkPolicy", "PersistentVolumeClaim", "Role", "RoleBinding",
              "Secret", "Service", "ServiceAccount", "StatefulSet"]


def res(kind, name, ns=None, api="apps/v1"):
    metadata = {"name": name, **({"namespace": ns} if ns else {})}
    return {"apiVersion": api, "kind": kind, "metadata": metadata}


def kcheck(kustomization, resources, **extra):
    return ok("gitops_kustomize_check", {"kustomization": kustomization, "resources": resources, **extra})


def test_kustomize_clean_overlay_passes():
    result = kcheck({}, [res("Deployment", "web", "shop")])
    assert result == {"state": "SUCCESS", "resource_count": 1, "patch_count": 0, "unresolved_patch_count": 0, "verdict": "pass",
                      "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}


def test_kustomize_duplicate_ids_are_blockers():
    twice = kcheck({}, [res("Deployment", "web", "shop"), res("Deployment", "web", "shop")])
    assert severities(twice) == {"duplicate-resource:apps/Deployment/shop/web": "blocker"} and twice["verdict"] == "fail"
    # real Kustomize (v0.21.2) builds the same name under two API versions; the cluster sees one object (see the review block below)
    versions = kcheck({}, [res("Deployment", "web", "shop"), res("Deployment", "web", "shop", "apps/v1beta1")])
    assert severities(versions) == {"duplicate-object-versions:apps/Deployment/shop/web": "major"}


@pytest.mark.parametrize("kind", CLUSTER_SCOPED)
def test_kustomize_cluster_scoped_kinds_collide_across_namespaces(kind):
    result = kcheck({}, [res(kind, "x", "a", "v1"), res(kind, "x", "b", "v1")])
    assert severities(result) == {f"duplicate-resource:{kind}/x": "blocker"}


@pytest.mark.parametrize("kind", NAMESPACED)
def test_kustomize_namespaced_kinds_do_not_collide_across_namespaces(kind):
    assert kcheck({}, [res(kind, "x", "a", "v1"), res(kind, "x", "b", "v1")])["findings"] == []


def test_kustomize_extra_cluster_scoped_kinds_extend_the_table():
    two = [res("Widget", "w", "a", "acme.io/v1"), res("Widget", "w", "b", "acme.io/v1")]
    assert kcheck({}, two)["findings"] == []
    assert severities(kcheck({}, two, extra_cluster_scoped_kinds=["Widget"])) == {"duplicate-resource:acme.io/Widget/w": "blocker"}


def test_kustomize_namespace_transformer_collisions_and_moves():
    result = kcheck({"namespace": "x"}, [res("ConfigMap", "cfg", "a", "v1"), res("ConfigMap", "cfg", "b", "v1")])
    assert severities(result) == {"namespace-collision:ConfigMap/x/cfg": "blocker", "namespace-overridden:a": "minor", "namespace-overridden:b": "minor"}
    assert result["severity_counts"] == {"blocker": 1, "major": 0, "minor": 2, "info": 0}
    # real Kustomize renames every v1 Namespace to the override: a Namespace that already carries it is fine, a second one collides
    same = kcheck({"namespace": "x"}, [res("ConfigMap", "cfg", "x", "v1"), res("Namespace", "x", None, "v1")])
    assert same["findings"] == []
    both = kcheck({"namespace": "x"}, [res("ConfigMap", "cfg", "x", "v1"), res("Namespace", "other", None, "v1"), res("Namespace", "x", None, "v1")])
    assert severities(both) == {"namespace-collision:Namespace/x": "blocker", "namespace-renamed:other": "info"}


@pytest.mark.parametrize("old,new", [("bases", "resources"), ("commonLabels", "labels"), ("patchesStrategicMerge", "patches"),
                                     ("patchesJson6902", "patches"), ("vars", "replacements")])
def test_kustomize_deprecated_fields_name_their_replacement(old, new):
    result = kcheck({old: []}, [])
    assert severities(result) == {f"deprecated-field:{old}": "minor"} and f"in favour of {new}" in result["findings"][0]["summary"]


def test_kustomize_duplicate_resource_entries_are_normalised():
    result = kcheck({"resources": ["./a.yaml", "a.yaml", "b.yaml", "dir/../a.yaml"]}, [])
    assert severities(result) == {"duplicate-resource-entry:a.yaml": "major"}


def test_kustomize_explicit_targets_are_whole_value_regular_expressions():
    web = [res("Deployment", "web-a", "shop")]
    patch = [{"op": "replace", "path": "/spec/replicas", "value": 2}]

    def run(target):
        return kcheck({"patches": [{"target": target, "patch": patch}]}, web)

    assert run({"kind": "Deployment", "name": "web.*"})["findings"] == []
    assert run({"kind": "Deployment", "name": "^web-a$"})["findings"] == []
    assert run({"kind": "Deployment", "name": "we.-a"})["findings"] == []
    assert run({"group": "apps", "version": "v1", "kind": "Deployment", "namespace": "shop"})["findings"] == []
    for target in ({"kind": "Deployment", "name": "web"}, {"kind": "Deployment", "name": "web.+x"}, {"kind": "Service"},
                   {"kind": "Deployment", "namespace": "other"}, {"group": "batch"}, {"version": "v2"}):
        assert severities(run(target)) == {"patch-target-not-found:patches[0]": "major"}, target
    # .+ needs at least one more character
    assert severities(run({"kind": "Deployment", "name": "web-a.+"})) == {"patch-target-not-found:patches[0]": "major"}
    assert run({"kind": "Deployment", "name": "web.+"})["findings"] == []


def test_kustomize_regex_beyond_the_evaluated_subset_is_reported_not_guessed():
    result = kcheck({"patches": [{"target": {"name": "web-(a|b)"}, "patch": [{"op": "remove", "path": "/spec"}]}]}, [res("Deployment", "zzz")])
    assert severities(result) == {"patch-regex-unverified:patches[0]": "info"} and result["verdict"] == "pass"


def test_kustomize_selectors_and_opaque_patches_are_info_only():
    selector = kcheck({"patches": [{"target": {"labelSelector": "app=web"}, "patch": [{"op": "remove", "path": "/spec"}]}]}, [])
    assert severities(selector) == {"patch-selector-unverified:patches[0]": "info"}
    opaque = kcheck({"patches": [{"path": "patch.yaml"}]}, [res("Deployment", "web")])
    assert severities(opaque) == {"patch-unresolved:patches[0]": "info"} and opaque["unresolved_patch_count"] == 1
    yaml_text = kcheck({"patches": [{"target": {"kind": "Deployment"}, "patch": "spec:\n  replicas: 2\n"}]}, [res("Deployment", "web")])
    assert yaml_text["findings"] == [] and yaml_text["unresolved_patch_count"] == 0


def test_kustomize_patches_derive_their_target_from_the_patch_object():
    merge = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop"}, "spec": {"replicas": 3}}
    deployment = [res("Deployment", "web", "shop")]
    assert kcheck({"patchesStrategicMerge": [merge]}, deployment)["findings"] == [
        {"id": "deprecated-field:patchesStrategicMerge", "severity": "minor",
         "summary": "patchesStrategicMerge is deprecated in favour of patches; `kustomize edit fix` migrates it"}]
    assert severities(kcheck({"patches": [{"patch": {**merge, "metadata": {"name": "other"}}}]}, deployment)) == {
        "patch-target-not-found:patches[0]": "major"}
    assert severities(kcheck({"patches": [{"patch": {"metadata": {"name": "x"}}}]}, deployment)) == {"patch-target-missing:patches[0]": "blocker"}
    # a derived target compares the name literally: a regex metacharacter in the name is not a pattern
    assert kcheck({"patches": [{"patch": {**merge, "metadata": {"name": "web.v1"}}}]}, [res("Deployment", "web.v1")])["findings"] == []  # neither names a namespace
    assert severities(kcheck({"patches": [{"patch": {**merge, "metadata": {"name": "web.*"}}}]}, deployment)) == {
        "patch-target-not-found:patches[0]": "major"}


def test_kustomize_json_patch_operations_are_validated():
    deployment = [res("Deployment", "web", "shop")]
    ops = [{"op": "bogus", "path": "/a"}, {"op": "add", "path": "a", "value": 1}, {"op": "add", "path": "/a"}, {"op": "move", "path": "/a"},
           {"op": "remove", "path": "/ok"}, {"op": "test", "path": "", "value": 1}]
    result = kcheck({"patchesJson6902": [{"target": {"kind": "Deployment", "name": "web"}, "patch": ops}]}, deployment)
    assert severities(result) == {
        "deprecated-field:patchesJson6902": "minor",
        "patch-op-invalid:patchesJson6902[0][0]": "major", "patch-op-invalid:patchesJson6902[0][1]": "major",
        "patch-op-invalid:patchesJson6902[0][2]": "major", "patch-op-invalid:patchesJson6902[0][3]": "major"}
    assert kcheck({"patches": [{"patch": ops[4:5]}]}, deployment)["findings"][0]["id"] == "patch-target-missing:patches[0]"
    as_text = kcheck({"patches": [{"target": {"kind": "Deployment"}, "patch": json.dumps([{"op": "replace", "path": "/a", "value": 1}])}]}, deployment)
    assert as_text["findings"] == [] and as_text["patch_count"] == 1


def test_kustomize_rejects_malformed_or_oversized_inputs_naming_the_field():
    invalid("gitops_kustomize_check", {"kustomization": {}, "resources": [{"kind": "Deployment", "apiVersion": "apps/v1", "metadata": {}}]}, "resources")
    deep: dict = {}
    node = deep
    for _ in range(45):
        node["a"] = {}
        node = node["a"]
    invalid("gitops_kustomize_check", {"kustomization": deep, "resources": []}, "kustomization")
    invalid("gitops_kustomize_check", {"kustomization": "x", "resources": []}, "kustomization")
    invalid("gitops_kustomize_check", {"kustomization": {}, "resources": [], "extra_cluster_scoped_kinds": [""]}, "extra_cluster_scoped_kinds")


def test_kustomize_bounds_the_patch_times_resource_work():
    patch = {"target": {"kind": "Nothing"}, "patch": [{"op": "remove", "path": "/a"}]}
    resources = [res("ConfigMap", f"c{i}", "x", "v1") for i in range(100)]
    at_limit = kcheck({"patches": [patch] * 200}, resources)
    assert at_limit["patch_count"] == 200 and len(at_limit["findings"]) == 200  # 200 x 100 = 20000 combinations is allowed
    invalid("gitops_kustomize_check", {"kustomization": {"patches": [patch] * 201}, "resources": resources}, "kustomization")
    assert "20000" in call("gitops_kustomize_check", {"kustomization": {"patches": [patch] * 201}, "resources": resources})["message"]


def test_kustomize_does_not_evaluate_patterns_longer_than_a_kubernetes_name():
    long_name = "a" * 257
    result = kcheck({"patches": [{"target": {"name": long_name}, "patch": [{"op": "remove", "path": "/a"}]}]}, [res("Deployment", "web")])
    assert severities(result) == {"patch-regex-unverified:patches[0]": "info"}
    exact = kcheck({"patches": [{"target": {"name": "a" * 256}, "patch": [{"op": "remove", "path": "/a"}]}]}, [res("Deployment", "web")])
    assert severities(exact) == {"patch-target-not-found:patches[0]": "major"}


# --- gitops_helm_values_drift ---------------------------------------------------------------------------------


def hdrift(environments, required=(), invariant=(), **extra):
    return ok("gitops_helm_values_drift", {"environments": [{"name": n, "values": v} for n, v in environments], "required_keys": list(required),
                                           "invariant_keys": list(invariant), **extra})


def test_helm_drift_reports_required_invariant_type_and_key_drift():
    result = hdrift([("s", {"a": {"b": 1, "c": "x"}, "r": 1}), ("p", {"a": {"b": "1"}, "z": 2, "r": None})], required=["r", "q"], invariant=["a"])
    assert severities(result) == {
        "invariant-key-differs:a.b": "major", "invariant-key-differs:a.c": "major", "required-key-empty:p:r": "major",
        "required-key-missing:p:q": "major", "required-key-missing:s:q": "major", "type-mismatch:p:a.b": "major",
        "type-mismatch:p:r": "major", "key-missing-in-env:p": "minor", "key-only-in-env:p": "info"}
    assert (result["environment_count"], result["compared_key_count"], result["reference"], result["diverging_value_count"]) == (2, 4, "s", 0)
    assert result["verdict"] == "fail" and result["severity_counts"] == {"blocker": 0, "major": 7, "minor": 1, "info": 1}
    by_id = {f["id"]: f["summary"] for f in result["findings"]}
    assert by_id["invariant-key-differs:a.c"] == "a.c must be identical in every environment but differs: s: <string #1>; p: <unset>"
    assert by_id["invariant-key-differs:a.b"] == "a.b must be identical in every environment but differs: s: 1; p: <string #2>"
    assert by_id["key-missing-in-env:p"] == "p lacks 1 key(s) set in s: a.c"


def test_helm_drift_counts_diverging_values_of_the_same_type_without_findings():
    result = hdrift([("staging", {"replicaCount": 2, "image": {"repository": "r"}}), ("prod", {"replicaCount": 4, "image": {"repository": "r"}})],
                    required=["replicaCount"], invariant=["image.repository"])
    assert result["findings"] == [] and result["verdict"] == "pass" and result["diverging_value_count"] == 1


def test_helm_drift_value_equality_follows_yaml_numbers_and_types():
    same = hdrift([("a", {"n": 1, "l": [1, 2], "m": {"k": [1]}}), ("b", {"n": 1.0, "l": [1, 2], "m": {"k": [1]}})], invariant=["n", "l", "m"])
    assert same["findings"] == [] and same["diverging_value_count"] == 0
    flag = hdrift([("a", {"f": True}), ("b", {"f": 1})], invariant=["f"])
    assert severities(flag) == {"invariant-key-differs:f": "major", "type-mismatch:b:f": "major"}
    order = hdrift([("a", {"l": [1, 2]}), ("b", {"l": [2, 1]})], invariant=["l"])
    assert severities(order) == {"invariant-key-differs:l": "major"} and order["diverging_value_count"] == 1


def test_helm_drift_reference_environment_and_prefix_keys():
    envs = [("s", {"resources": {"limits": {"cpu": "1"}}, "only_s": 1}), ("p", {"resources": {"limits": {"cpu": None}}, "only_p": 1})]
    default = hdrift(envs, required=["resources"])
    assert severities(default) == {"required-key-empty:p:resources": "major", "key-missing-in-env:p": "minor", "key-only-in-env:p": "info",
                                   "type-mismatch:p:resources.limits.cpu": "major"}
    flipped = hdrift(envs, reference="p")
    assert flipped["reference"] == "p" and "key-missing-in-env:s" in severities(flipped)
    assert hdrift([("a", {"x": {}}), ("b", {"x": {}})], required=["x"])["findings"] == []
    many = hdrift([("a", {f"k{i:02d}": i for i in range(12)}), ("b", {})])
    assert many["findings"][0]["summary"] == "b lacks 12 key(s) set in a: k00, k01, k02, k03, k04, k05, k06, k07 (+4 more)"


def test_helm_drift_rejects_bad_environments_keys_and_reference():
    base = {"required_keys": [], "invariant_keys": []}
    invalid("gitops_helm_values_drift", {"environments": [{"name": "a", "values": {}}, {"name": "a", "values": {}}], **base}, "environments")
    invalid("gitops_helm_values_drift", {"environments": [{"name": "a", "values": []}, {"name": "b", "values": {}}], **base}, "environments")
    invalid("gitops_helm_values_drift", {"environments": [{"name": "a", "values": {}}], **base}, "environments")
    two = [{"name": "a", "values": {}}, {"name": "b", "values": {}}]
    invalid("gitops_helm_values_drift", {"environments": two, "required_keys": ["a..b"], "invariant_keys": []}, "required_keys")
    invalid("gitops_helm_values_drift", {"environments": two, "required_keys": [], "invariant_keys": [".a"]}, "invariant_keys")
    invalid("gitops_helm_values_drift", {"environments": two, "required_keys": [], "invariant_keys": ["a."]}, "invariant_keys")
    invalid("gitops_helm_values_drift", {"environments": two, **base, "reference": "zzz"}, "reference")


# --- gitops_app_sync_policy -----------------------------------------------------------------------------------

REPO = "https://git.example.com/acme/deploy.git"


def argo_app(spec_extra=None, source_extra=None, metadata=None, **spec_over):
    source = {"repoURL": REPO, "path": "apps/web", "targetRevision": "v1.4.0", **(source_extra or {})}
    spec = {"project": "shop", "source": source, "destination": {"server": "https://kubernetes.default.svc", "namespace": "shop"}, **spec_over}
    spec.update(spec_extra or {})
    return {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {"name": "web", **(metadata or {})}, "spec": spec}


def sync_policy(resource, environment="nonprod"):
    return ok("gitops_app_sync_policy", {"resource": resource, "environment": environment})


def test_argo_application_that_is_pinned_and_manual_passes():
    result = sync_policy(argo_app(), "prod")
    assert result == {"state": "SUCCESS", "engine": "argocd", "kind": "Application", "auto_sync": False, "prune_enabled": False,
                      "revision_class": "tag", "verdict": "pass", "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}


def test_argo_automated_sync_is_a_production_finding_and_prune_is_a_blocker():
    auto = {"syncPolicy": {"automated": {}, "retry": {"limit": 3}}}
    assert severities(sync_policy(argo_app(auto), "prod")) == {"prod-auto-sync": "major"}
    assert sync_policy(argo_app(auto), "nonprod")["findings"] == []
    prune = {"syncPolicy": {"automated": {"prune": True, "selfHeal": True}, "retry": {"limit": 3}}}
    prod = sync_policy(argo_app(prune), "prod")
    assert severities(prod) == {"prod-auto-sync": "major", "prod-auto-prune": "blocker"} and prod["prune_enabled"] is True and prod["auto_sync"] is True
    assert severities(sync_policy(argo_app(prune), "nonprod")) == {"auto-prune": "info"}


def test_argo_allow_empty_matters_only_with_prune():
    retry = {"limit": 3}
    assert sync_policy(argo_app({"syncPolicy": {"automated": {"allowEmpty": True}, "retry": retry}}))["findings"] == []
    both = {"syncPolicy": {"automated": {"allowEmpty": True, "prune": True}, "retry": retry}}
    assert severities(sync_policy(argo_app(both), "nonprod")) == {"auto-prune": "info", "allow-empty-prune": "major"}
    assert severities(sync_policy(argo_app(both), "prod"))["allow-empty-prune"] == "blocker"


def test_argo_automated_sync_without_retry_is_minor():
    assert severities(sync_policy(argo_app({"syncPolicy": {"automated": {}}}))) == {"no-retry": "minor"}


@pytest.mark.parametrize("revision,chart,klass", [
    ("", False, "floating"), (None, False, "floating"), ("HEAD", False, "floating"), ("a" * 40, False, "pinned"), ("a" * 64, False, "pinned"),
    ("v1.2.3", False, "tag"), ("1.2", False, "tag"), ("v1.2.3-rc.1", False, "tag"), ("main", False, "branch"), ("release/1.x", False, "branch"),
    ("{{ .revision }}", False, "templated"), ("1.2.3", True, "pinned"), ("1.2.3-rc.1", True, "pinned"), ("1.*", True, "range"),
    ("^1.2.0", True, "range"), ("~1.2", True, "range"), (">=1.0.0 <2.0.0", True, "range"), ("1.x", True, "range"), ("*", True, "range"),
    ("", True, "floating"), ("latest", True, "unknown"),
])
def test_argo_revision_classes(revision, chart, klass):
    source = {"chart": "web", "path": None} if chart else {}
    app = argo_app(source_extra={"targetRevision": revision, **source})
    if chart:
        del app["spec"]["source"]["path"]
    assert sync_policy(app, "prod")["revision_class"] == klass


def test_argo_floating_and_branch_revisions_by_environment():
    assert severities(sync_policy(argo_app(source_extra={"targetRevision": "HEAD"}), "prod")) == {"floating-revision:source 0 targetRevision 'HEAD'": "major"}
    assert severities(sync_policy(argo_app(source_extra={"targetRevision": "HEAD"}), "nonprod")) == {"floating-revision:source 0 targetRevision 'HEAD'": "info"}
    assert severities(sync_policy(argo_app(source_extra={"targetRevision": "main"}), "prod")) == {"branch-revision:source 0 targetRevision 'main'": "minor"}
    assert sync_policy(argo_app(source_extra={"targetRevision": "main"}), "nonprod")["findings"] == []


def test_argo_source_and_destination_structure():
    assert severities(sync_policy(argo_app(source=None, destination=None))) == {"no-source": "blocker", "no-destination": "blocker"}
    both = argo_app(destination={"server": "s", "name": "n"})
    assert severities(sync_policy(both)) == {"destination-server-and-name": "blocker"}
    assert severities(sync_policy(argo_app(source_extra={"repoURL": " ", "path": None}))) == {"no-repo-url:0": "blocker", "no-path-or-chart:0": "blocker"}
    assert severities(sync_policy(argo_app(source_extra={"chart": "web"}))) == {"path-and-chart:0": "major"}
    multi = argo_app(sources=[{"repoURL": REPO, "ref": "values", "targetRevision": "v1.0.0"}, {"repoURL": REPO, "chart": "web", "targetRevision": "1.0.0"}], source=None)
    assert sync_policy(multi, "prod")["findings"] == []
    assert severities(sync_policy(argo_app(project=""))) == {"no-project": "minor"}
    assert severities(sync_policy(argo_app(project="default"), "prod")) == {"default-project": "minor"}
    assert sync_policy(argo_app(project="default"), "nonprod")["findings"] == []


ARGO_BOOL_OPTIONS = ["ApplyOutOfSyncOnly", "CreateNamespace", "FailOnSharedResource", "Force", "PruneLast", "Replace", "RespectIgnoreDifferences",
                     "ServerSideApply", "SkipDryRunOnMissingResource", "Validate"]


@pytest.mark.parametrize("option", ARGO_BOOL_OPTIONS)
def test_argo_documented_boolean_sync_options_accept_only_true_or_false(option):
    bad = sync_policy(argo_app({"syncPolicy": {"syncOptions": [f"{option}=maybe"]}}))
    assert severities(bad) == {f"sync-option-value:{option}=maybe": "major"}
    assert "sync-option-unknown" not in "".join(severities(sync_policy(argo_app({"syncPolicy": {"syncOptions": [f"{option}=true"]}}))))


def test_argo_sync_option_semantics():
    def options(*values, environment="nonprod"):
        return severities(sync_policy(argo_app({"syncPolicy": {"syncOptions": list(values)}}), environment))

    assert options("Force=true") == {"sync-option:Force": "minor"} and options("Force=true", environment="prod") == {"sync-option:Force": "major"}
    assert options("Replace=true") == {"sync-option:Replace": "minor"} and options("Replace=true", environment="prod") == {"sync-option:Replace": "major"}
    assert options("Force=false", "Replace=false", "Validate=true", "CreateNamespace=true") == {}
    assert options("Validate=false") == {"sync-option:Validate": "minor"}
    assert options("PrunePropagationPolicy=nope") == {"sync-option-value:PrunePropagationPolicy=nope": "major"}
    assert options("Prune=maybe") == {"sync-option-value:Prune=maybe": "major"} and options("Delete=sometimes") == {"sync-option-value:Delete=sometimes": "major"}
    assert options("Prune=true", "Prune=false", "Prune=confirm", "Delete=true", "Delete=false", "Delete=confirm") == {}
    assert options(*[f"PrunePropagationPolicy={p}" for p in ("foreground", "background", "orphan")]) == {}
    assert options("Mystery=1") == {"sync-option-unknown:Mystery": "info"}
    assert options("noequals") == {"sync-option-malformed:noequals": "minor"}


def test_argo_ignore_differences_and_finalizer():
    ignoring = argo_app({"ignoreDifferences": [{"kind": "Deployment", "jsonPointers": ["/spec/replicas"]}, {"jsonPointers": ["/"]},
                                               {"jqPathExpressions": ["."]}, {"jqPathExpressions": [".spec.x"]}]})
    assert severities(sync_policy(ignoring)) == {"ignore-everything:1": "minor", "ignore-everything:2": "minor"}
    final = argo_app(metadata={"finalizers": ["resources-finalizer.argocd.argoproj.io/background"]})
    assert severities(sync_policy(final)) == {"cascade-delete": "info"}


def flux_kustomization(**spec_over):
    spec = {"interval": "10m", "prune": False, "sourceRef": {"kind": "GitRepository", "name": "flux-system"}, "path": "./apps"}
    spec.update(spec_over)
    return {"apiVersion": "kustomize.toolkit.fluxcd.io/v1", "kind": "Kustomization", "metadata": {"name": "apps"}, "spec": spec}


def test_flux_kustomization_prune_and_production_rules():
    clean = sync_policy(flux_kustomization(serviceAccountName="deployer"), "prod")
    assert clean == {"state": "SUCCESS", "engine": "flux", "kind": "Kustomization", "auto_sync": True, "prune_enabled": False, "revision_class": "unknown",
                     "verdict": "pass", "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}
    assert severities(sync_policy(flux_kustomization(), "prod")) == {"no-impersonation": "minor"}
    assert sync_policy(flux_kustomization(), "nonprod")["findings"] == []
    assert severities(sync_policy(flux_kustomization(prune=True, serviceAccountName="d"), "prod")) == {"prod-prune": "blocker"}
    assert severities(sync_policy(flux_kustomization(prune=True), "nonprod")) == {"prune-enabled": "info"}
    assert sync_policy(flux_kustomization(prune=True), "nonprod")["prune_enabled"] is True
    forced = flux_kustomization(force=True, serviceAccountName="d")
    assert severities(sync_policy(forced, "prod")) == {"force": "major"} and severities(sync_policy(forced, "nonprod")) == {"force": "minor"}
    suspended = sync_policy(flux_kustomization(suspend=True))
    assert severities(suspended) == {"suspended": "minor"} and suspended["auto_sync"] is False


@pytest.mark.parametrize("interval,expected", [("60s", {}), ("1m", {}), ("1h30m", {}), ("1.5h", {}), ("59s", {"interval-under-1m": "minor"}),
                                               ("500ms", {"interval-under-1m": "minor"}), ("bad", {"invalid-interval": "blocker"}),
                                               ("10", {"invalid-interval": "blocker"}), ("5m30", {"invalid-interval": "blocker"})])
def test_flux_interval_boundaries(interval, expected):
    assert severities(sync_policy(flux_kustomization(interval=interval))) == expected


def test_flux_kustomization_required_fields_and_sources():
    missing = flux_kustomization()
    for field in ("interval", "prune", "sourceRef"):
        del missing["spec"][field]
    assert severities(sync_policy(missing)) == {"no-interval": "blocker", "prune-unspecified": "blocker", "no-source-ref": "blocker"}
    assert severities(sync_policy(flux_kustomization(sourceRef={"kind": "Foo", "name": "x"}))) == {"source-ref-kind": "blocker"}
    for kind in ("GitRepository", "OCIRepository", "Bucket"):
        assert sync_policy(flux_kustomization(sourceRef={"kind": kind, "name": "x"}))["findings"] == []
    assert severities(sync_policy(flux_kustomization(decryption={"provider": "age"}))) == {"decryption-provider": "blocker"}
    assert sync_policy(flux_kustomization(decryption={"provider": "sops"}))["findings"] == []


@pytest.mark.parametrize("group,version,kind", [("kustomize.toolkit.fluxcd.io", "v1beta1", "Kustomization"), ("kustomize.toolkit.fluxcd.io", "v1beta2", "Kustomization"),
                                                ("helm.toolkit.fluxcd.io", "v2beta1", "HelmRelease"), ("helm.toolkit.fluxcd.io", "v2beta2", "HelmRelease")])
def test_flux_deprecated_api_versions(group, version, kind):
    resource = flux_kustomization() if kind == "Kustomization" else flux_helm_release()
    resource["apiVersion"] = f"{group}/{version}"
    assert severities(sync_policy(resource))["deprecated-api-version"] == "minor"


def flux_helm_release(**spec_over):
    spec = {"interval": "10m", "chart": {"spec": {"chart": "web", "version": "1.4.2", "sourceRef": {"kind": "HelmRepository", "name": "acme"}}},
            "install": {"remediation": {"retries": 3}}, "driftDetection": {"mode": "enabled"}}
    spec.update(spec_over)
    return {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "metadata": {"name": "web"}, "spec": spec}


def test_flux_helm_release_rules():
    clean = sync_policy(flux_helm_release(), "prod")
    assert (clean["findings"], clean["engine"], clean["revision_class"], clean["prune_enabled"]) == ([], "flux", "pinned", False)
    ranged = flux_helm_release(chart={"spec": {"chart": "web", "version": "1.x"}})
    assert severities(sync_policy(ranged, "prod")) == {"floating-revision:chart version '1.x'": "major"}
    assert severities(sync_policy(ranged, "nonprod")) == {"floating-revision:chart version '1.x'": "info"}
    assert severities(sync_policy(flux_helm_release(chart={"spec": {"chart": "web"}}), "prod")) == {"floating-revision:chart version None": "major"}
    assert severities(sync_policy(flux_helm_release(chart=None, chartRef={"kind": "OCIRepository", "name": "x"}))) == {}
    assert severities(sync_policy(flux_helm_release(chart=None))) == {"no-chart": "blocker"}
    assert severities(sync_policy(flux_helm_release(install={}, driftDetection={"mode": "warn"}))) == {
        "no-remediation": "minor", "drift-detection-off": "info"}
    assert sync_policy(flux_helm_release(install={}, upgrade={"remediation": {"retries": 1}}))["findings"] == []
    forced = flux_helm_release(upgrade={"force": True, "remediation": {"retries": 1}})
    assert severities(sync_policy(forced, "prod")) == {"upgrade-force": "major"} and severities(sync_policy(forced, "nonprod")) == {"upgrade-force": "minor"}
    assert severities(sync_policy(flux_helm_release(interval="bad"))) == {"invalid-interval": "blocker"}


def test_sync_policy_rejects_unsupported_or_malformed_resources():
    invalid("gitops_app_sync_policy", {"resource": {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "x"}}, "environment": "prod"}, "resource")
    invalid("gitops_app_sync_policy", {"resource": {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {}}, "environment": "prod"}, "resource")
    app = argo_app()
    app["spec"] = "not-an-object"
    invalid("gitops_app_sync_policy", {"resource": app, "environment": "prod"}, "resource")
    invalid("gitops_app_sync_policy", {"resource": argo_app(), "environment": "staging"}, "environment")


# --- gitops_appset_check --------------------------------------------------------------------------------------

CLUSTERS = [{"name": "a", "labels": {"env": "prod", "tier": "1"}}, {"name": "b", "labels": {"env": "stg"}}, {"name": "c"}]


def appset_template(name="web-{{name}}", destination=None, **spec_over):
    spec = {"project": "shop", "source": {"repoURL": REPO, "path": "apps/web", "targetRevision": "v1.0.0"},
            "destination": destination or {"server": "{{server}}", "namespace": "shop"}}
    spec.update(spec_over)
    return {"metadata": {"name": name}, "spec": spec}


def appset(generators, template=None, **spec_over):
    return {"apiVersion": "argoproj.io/v1alpha1", "kind": "ApplicationSet", "metadata": {"name": "web"},
            "spec": {"generators": generators, "template": template or appset_template(), **spec_over}}


def check_appset(document, clusters=None, environment="nonprod"):
    return ok("gitops_appset_check", {"applicationset": document, "clusters": CLUSTERS if clusters is None else clusters, "environment": environment})


def selected(selector):
    return check_appset(appset([{"clusters": {"selector": selector}}]))["matched_cluster_count"]


def test_appset_clean_cluster_generator():
    result = check_appset(appset([{"clusters": {"selector": {"matchLabels": {"env": "stg"}}}}]))
    assert result == {"state": "SUCCESS", "generator_count": 1, "matched_cluster_count": 1, "verdict": "pass",
                      "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}


@pytest.mark.parametrize("selector,count", [
    ({"matchLabels": {"env": "prod"}}, 1),
    ({"matchLabels": {"env": "prod", "tier": "1"}}, 1),
    ({"matchLabels": {"env": "prod", "tier": "2"}}, 0),
    ({"matchExpressions": [{"key": "env", "operator": "In", "values": ["prod", "stg"]}]}, 2),
    ({"matchExpressions": [{"key": "env", "operator": "NotIn", "values": ["prod"]}]}, 2),  # a cluster without the key is not in the set
    ({"matchExpressions": [{"key": "tier", "operator": "Exists"}]}, 1),
    ({"matchExpressions": [{"key": "tier", "operator": "DoesNotExist"}]}, 2),
    ({"matchLabels": {"env": "prod"}, "matchExpressions": [{"key": "tier", "operator": "Exists"}]}, 1),
    ({"matchLabels": {"env": "stg"}, "matchExpressions": [{"key": "tier", "operator": "Exists"}]}, 0),
])
def test_appset_cluster_selector_semantics(selector, count):
    assert selected(selector) == count


def test_appset_cluster_selection_findings():
    everything = check_appset(appset([{"clusters": {}}]))
    assert everything["matched_cluster_count"] == 3
    assert severities(everything)["all-clusters-selected:generators[0]"] == "major"
    nothing = check_appset(appset([{"clusters": {"selector": {"matchLabels": {"env": "dev"}}}}]))
    assert severities(nothing) == {"selector-matches-no-cluster:generators[0]": "major"} and nothing["matched_cluster_count"] == 0
    bad = check_appset(appset([{"clusters": {"selector": {"matchExpressions": [{"key": "env", "operator": "Equals", "values": ["x"]}]}}}]))
    assert severities(bad)["selector-invalid:generators[0]"] == "blocker"
    unknown = check_appset(appset([{"clusters": {"selector": {"matchLabels": {"env": "stg"}}}}]), clusters=[])
    assert unknown["matched_cluster_count"] == -1 and severities(unknown) == {"clusters-not-supplied": "info"}
    assert check_appset(appset([{"list": {"elements": [{"name": "x", "server": "y"}]}}]))["matched_cluster_count"] == -1


def test_appset_template_keys_must_be_produced_by_the_generators():
    gens = [{"list": {"elements": [{"name": "x", "server": "y"}]}}]
    assert check_appset(appset(gens))["findings"] == []
    bad = check_appset(appset(gens, appset_template(name="web-{{nope}}")))
    assert severities(bad) == {"unknown-template-key:nope": "major"}
    uneven = check_appset(appset([{"list": {"elements": [{"name": "x", "server": "y"}, {"name": "z"}]}}], appset_template(name="a-{{name}}-{{server}}")))
    assert severities(uneven) == {"list-elements-inconsistent:generators[0]": "minor"}
    assert severities(check_appset(appset([{"list": {"elements": []}}], appset_template(name="x", destination={"name": "k"})))) == {
        "list-empty:generators[0]": "major"}
    assert check_appset(appset([{"list": {"elementsYaml": "{{ .x }}"}}], appset_template(name="{{whatever}}")))["findings"] == []


def test_appset_template_name_and_destination_must_vary_per_element():
    git = [{"git": {"repoURL": REPO, "directories": [{"path": "apps/*"}]}}]
    static = check_appset(appset(git, appset_template(name="web", destination={"server": "https://k", "namespace": "x"})))
    assert severities(static) == {"template-name-static": "blocker"}
    one = [{"list": {"elements": [{"name": "x"}]}}]
    assert check_appset(appset(one, appset_template(name="web", destination={"server": "https://k", "namespace": "x"})))["findings"] == []
    two = [{"list": {"elements": [{"name": "x"}, {"name": "y"}]}}]
    assert severities(check_appset(appset(two, appset_template(name="web", destination={"name": "k"})))) == {"template-name-static": "blocker"}
    fixed_dest = check_appset(appset([{"clusters": {"selector": {"matchExpressions": [{"key": "env", "operator": "Exists"}]}}}],
                                     appset_template(destination={"server": "https://k", "namespace": "x"})))
    assert severities(fixed_dest) == {"destination-static": "major"}
    assert check_appset(appset([{"clusters": {"selector": {"matchLabels": {"env": "prod"}}}}], appset_template(destination={"server": "https://k"})))["findings"] == []
    no_name = appset(two, appset_template(destination={"name": "k"}))
    del no_name["spec"]["template"]["metadata"]["name"]
    assert severities(check_appset(no_name)) == {"template-no-name": "blocker"}


def test_appset_generator_shape_rules():
    two_types = check_appset(appset([{"list": {"elements": [{"name": "x"}]}, "clusters": {}}]))
    assert severities(two_types)["generator-type:generators[0]"] == "blocker"
    assert severities(check_appset(appset([{"nope": {}}])))["generator-type:generators[0]"] == "blocker"
    assert severities(check_appset(appset([{"list": "x"}])))["generator-type:generators[0]"] == "blocker"
    assert severities(check_appset(appset([5])))["generator-invalid:generators[0]"] == "blocker"
    assert severities(check_appset(appset([], appset_template(name="x", destination={"name": "k"})))) == {"no-generators": "blocker"}
    element = {"list": {"elements": [{"name": "x", "server": "y"}]}}
    matrix3 = check_appset(appset([{"matrix": {"generators": [element, element, element]}}]))
    assert severities(matrix3)["matrix-arity:generators[0]"] == "blocker"
    assert severities(check_appset(appset([{"matrix": {"generators": [element, element]}}]))) == {}
    assert severities(check_appset(appset([{"matrix": {}}])))["generators-missing:generators[0]"] == "blocker"
    merge = {"merge": {"mergeKeys": ["zz"], "generators": [element, element]}}
    assert severities(check_appset(appset([merge]))) == {"merge-key-missing:generators[0]:zz": "major"}
    assert severities(check_appset(appset([{"merge": {"mergeKeys": [], "generators": [element, element]}}])))["merge-keys:generators[0]"] == "blocker"
    assert severities(check_appset(appset([{"merge": {"mergeKeys": ["name"], "generators": [element]}}]))) == {"merge-single:generators[0]": "minor"}
    assert check_appset(appset([{"merge": {"mergeKeys": ["name"], "generators": [element, element]}}]))["findings"] == []
    nested = {"matrix": {"generators": [{"matrix": {"generators": [{"matrix": {"generators": [{"matrix": {"generators": [{"matrix": {"generators": [element, element]}}, element]}}, element]}}, element]}}, element]}}
    assert "generators-too-deep" in " ".join(severities(check_appset(appset([nested]))))
    assert check_appset(appset([{"matrix": {"generators": [element, element]}}]))["generator_count"] == 3


def test_appset_git_generator_requirements_and_keys():
    no_repo = appset([{"git": {"directories": [{"path": "a"}]}}], appset_template(name="{{path.basename}}", destination={"name": "k"}))
    assert severities(check_appset(no_repo)) == {"git-no-repo:generators[0]": "blocker"}
    result = check_appset(appset([{"git": {"repoURL": REPO}}], appset_template(name="{{path.basename}}")))
    assert severities(result)["git-no-paths:generators[0]"] == "blocker"
    fast = appset_template(name="{{path.basename}}-{{path[1]}}-{{path.basenameNormalized}}", destination={"name": "{{path}}", "namespace": "x"})
    assert check_appset(appset([{"git": {"repoURL": REPO, "directories": [{"path": "apps/*"}]}}], fast))["findings"] == []
    go_template = appset_template(name="{{ .path.basename }}-{{ .path.path | base }}-{{ .path.segments }}", destination={"name": "{{ .path.basenameNormalized }}"})
    go = check_appset(appset([{"git": {"repoURL": REPO, "directories": [{"path": "apps/*"}]}}], go_template, goTemplate=True, goTemplateOptions=["missingkey=error"]))
    assert go["findings"] == []
    files = check_appset(appset([{"git": {"repoURL": REPO, "files": [{"path": "cfg/*.json"}]}}], appset_template(name="{{whatever.from.file}}-{{path.filename}}")))
    assert files["findings"] == []
    wrong_mode = check_appset(appset([{"git": {"repoURL": REPO, "directories": [{"path": "a"}]}}], appset_template(name="{{path.path}}", destination={"name": "k"})))
    assert severities(wrong_mode) == {"unknown-template-key:path.path": "major"}


def test_appset_go_template_options_and_label_keys():
    template = appset_template(name="{{ .name }}-{{ index .metadata.labels \"env\" }}-{{ .metadata.labels.env }}")
    gens = [{"clusters": {"selector": {"matchExpressions": [{"key": "env", "operator": "Exists"}]}}}]
    result = check_appset(appset(gens, template, goTemplate=True))
    assert severities(result) == {"go-template-missingkey": "minor"}
    assert check_appset(appset(gens, template, goTemplate=True, goTemplateOptions=["missingkey=error"]))["findings"] == []
    lacking = check_appset(appset([{"clusters": {}}], template, goTemplate=True, goTemplateOptions=["missingkey=error"]))
    assert severities(lacking) == {"all-clusters-selected:generators[0]": "major", "label-missing-on-cluster:env": "major"}
    by_id = {f["id"]: f["summary"] for f in lacking["findings"]}
    assert by_id["label-missing-on-cluster:env"] == "1 selected cluster(s) lack label env used by the template"
    values = appset([{"clusters": {"values": {"tier": "x"}}}], appset_template(name="{{name}}-{{values.tier}}-{{metadata.annotations.foo}}"))
    assert [k for k in severities(check_appset(values)) if k.startswith("unknown")] == []
    assert severities(check_appset(appset([{"clusters": {}}], appset_template(name="{{name}}-{{values.nope}}"))))["unknown-template-key:values.nope"] == "major"


def test_appset_rolling_sync_and_policies():
    template = appset_template()
    template["metadata"]["labels"] = {"tier": "{{metadata.labels.env}}"}
    gens = [{"clusters": {"selector": {"matchLabels": {"env": "prod"}}}}]

    def steps(*keys):
        return {"type": "RollingSync", "rollingSync": {"steps": [{"matchExpressions": [{"key": k, "operator": "In", "values": ["x"]}]} for k in keys]}}

    assert check_appset(appset(gens, template, strategy=steps("tier")))["findings"] == []
    assert severities(check_appset(appset(gens, template, strategy=steps("tier", "ring")))) == {"rolling-sync-label-missing:1:ring": "major"}
    assert severities(check_appset(appset(gens, template, strategy={"type": "RollingSync"}))) == {"rolling-sync-empty": "blocker"}
    assert severities(check_appset(appset(gens, template, strategy={"type": "Wave"}))) == {"strategy-type": "blocker"}
    assert check_appset(appset(gens, template, strategy={"type": "AllAtOnce"}))["findings"] == []
    many = [{"clusters": {"selector": {"matchLabels": {"env": "prod"}}}}, {"clusters": {"selector": {"matchLabels": {"env": "stg"}}}}]
    assert severities(check_appset(appset(many), environment="prod")) == {"no-progressive-sync": "minor"}
    assert check_appset(appset(gens), environment="prod")["findings"] == []  # a single cluster needs no progressive sync
    assert check_appset(appset(many), environment="nonprod")["findings"] == []
    assert severities(check_appset(appset(gens, syncPolicy={"applicationsSync": "wipe"}))) == {"applications-sync": "major"}
    for policy in ("create-only", "create-update", "create-delete", "sync"):
        assert check_appset(appset(gens, syncPolicy={"applicationsSync": policy}))["findings"] == []
    cascade = appset_template()
    cascade["metadata"]["finalizers"] = ["resources-finalizer.argocd.argoproj.io"]
    assert severities(check_appset(appset(gens, cascade), environment="prod")) == {"appset-cascade-delete": "minor", "template-cascade-delete": "info"}
    keep = check_appset(appset(gens, cascade, syncPolicy={"preserveResourcesOnDeletion": True}), environment="prod")
    assert severities(keep) == {"template-cascade-delete": "info"}


def test_appset_applies_the_application_rules_to_the_template():
    template = appset_template(syncPolicy={"automated": {"prune": True}, "retry": {"limit": 2}}, project="default")
    result = check_appset(appset([{"clusters": {"selector": {"matchLabels": {"env": "stg"}}}}], template), environment="prod")
    assert severities(result) == {"template-prod-auto-sync": "major", "template-prod-auto-prune": "blocker", "template-default-project": "minor"}
    go_gens = [{"list": {"elements": [{"name": "x", "server": "y", "rev": "v1"}]}}]
    templated = appset_template(name="web-{{ .name }}", source={"repoURL": REPO, "path": "x", "targetRevision": "{{ .rev }}"},
                                destination={"server": "{{ .server }}"})
    assert check_appset(appset(go_gens, templated, goTemplate=True, goTemplateOptions=["missingkey=error"]), environment="prod")["findings"] == []


def test_appset_rejects_malformed_inputs():
    invalid("gitops_appset_check", {"applicationset": {"kind": "ApplicationSet"}, "clusters": [], "environment": "prod"}, "applicationset")
    wrong = appset([])
    wrong["kind"] = "Application"
    invalid("gitops_appset_check", {"applicationset": wrong, "clusters": [], "environment": "prod"}, "applicationset")
    invalid("gitops_appset_check", {"applicationset": appset([]), "clusters": [{"name": "a"}, {"name": "a"}], "environment": "prod"}, "clusters")
    invalid("gitops_appset_check", {"applicationset": appset([]), "clusters": [{"name": "a", "labels": {"x": 1}}], "environment": "prod"}, "clusters")


# --- gitops_sync_wave_check -----------------------------------------------------------------------------------

WAVE = "argocd.argoproj.io/sync-wave"
HOOK = "argocd.argoproj.io/hook"


def obj(kind, name, api="v1", ns="app", annotations=None, spec=None):
    metadata = {"name": name, **({"namespace": ns} if ns else {}), **({"annotations": annotations} if annotations else {})}
    return {"apiVersion": api, "kind": kind, "metadata": metadata, **({"spec": spec} if spec is not None else {})}


def waves(manifests):
    return ok("gitops_sync_wave_check", {"manifests": manifests})


def pod_template(**pod):
    return {"template": {"spec": {"containers": [{"name": "c", "image": "i", **pod.pop("container", {})}], **pod}}}


def test_sync_waves_default_to_zero_and_group_by_phase_and_wave():
    result = waves([obj("Deployment", "web", "apps/v1", spec=pod_template()), obj("Service", "web", annotations={WAVE: "-2"}),
                    obj("Job", "post", "batch/v1", annotations={HOOK: "PostSync"}), obj("Job", "pre", "batch/v1", annotations={HOOK: "PreSync", WAVE: "5"}),
                    obj("Job", "skipped", "batch/v1", annotations={HOOK: "Skip"}), obj("Job", "fail", "batch/v1", annotations={HOOK: "SyncFail"}),
                    obj("Job", "both", "batch/v1", annotations={HOOK: "PreSync,PostSync"})])
    assert result["plan"] == [
        {"phase": "PreSync", "wave": 0, "resource_count": 1, "kinds": ["Job"]}, {"phase": "PreSync", "wave": 5, "resource_count": 1, "kinds": ["Job"]},
        {"phase": "Sync", "wave": -2, "resource_count": 1, "kinds": ["Service"]}, {"phase": "Sync", "wave": 0, "resource_count": 1, "kinds": ["Deployment"]},
        {"phase": "PostSync", "wave": 0, "resource_count": 2, "kinds": ["Job"]}, {"phase": "SyncFail", "wave": 0, "resource_count": 1, "kinds": ["Job"]}]
    assert (result["resource_count"], result["wave_count"], result["findings"], result["verdict"]) == (6, 6, [], "pass")


@pytest.mark.parametrize("value,wave,flagged", [("0", 0, False), ("-3", -3, False), ("+2", 2, False), ("10", 10, False), ("1.5", 0, True),
                                                (" 2", 0, True), ("2 ", 0, True), ("two", 0, True), ("", 0, True), ("1_0", 0, True)])
def test_sync_wave_values_follow_go_atoi(value, wave, flagged):
    result = waves([obj("ConfigMap", "c", annotations={WAVE: value})])
    assert [g["wave"] for g in result["plan"]] == [wave]
    assert severities(result) == ({"wave-invalid:ConfigMap/app/c": "major"} if flagged else {})


def test_sync_wave_hook_annotations_are_validated():
    result = waves([obj("Job", "j", "batch/v1", annotations={HOOK: "PreSync, Bogus", "argocd.argoproj.io/hook-delete-policy": "HookSucceeded,Nope"})])
    assert severities(result) == {"hook-unknown-type:Job/app/j:Bogus": "major", "hook-delete-policy:Job/app/j:Nope": "major"}
    assert result["plan"][0]["phase"] == "PreSync"
    only_bogus = waves([obj("Job", "j", "batch/v1", annotations={HOOK: "Bogus"})])
    assert only_bogus["plan"][0]["phase"] == "Sync"
    for policy in ("HookSucceeded", "HookFailed", "BeforeHookCreation", "HookFailed, BeforeHookCreation"):
        assert waves([obj("Job", "j", "batch/v1", annotations={HOOK: "PostSync", "argocd.argoproj.io/hook-delete-policy": policy})])["findings"] == []
    for hook in ("PreSync", "Sync", "PostSync", "SyncFail", "PostDelete", "Skip"):
        assert waves([obj("Job", "j", "batch/v1", annotations={HOOK: hook})])["findings"] == []


def test_sync_wave_namespace_must_not_come_after_its_contents():
    def manifests(ns_wave, dep_wave):
        return [obj("Namespace", "app", ns=None, annotations={WAVE: ns_wave}), obj("ConfigMap", "c", annotations={WAVE: dep_wave})]

    assert severities(waves(manifests("1", "0"))) == {"namespace-after-use:ConfigMap/app/c": "blocker"}
    assert waves(manifests("0", "0"))["findings"] == []  # the same wave is fine: Namespaces sort first
    assert waves(manifests("-1", "0"))["findings"] == []
    assert waves([obj("Namespace", "app", ns=None, annotations={WAVE: "9"}), obj("ConfigMap", "c", ns="other")])["findings"] == []
    assert waves([obj("Namespace", "app", ns=None, annotations={WAVE: "9"}), obj("ClusterRole", "r", "rbac.authorization.k8s.io/v1", ns="app")])["findings"] == []


def test_sync_wave_custom_resources_need_their_crd_first():
    def crd(wave):
        return obj("CustomResourceDefinition", "widgets.acme.io", "apiextensions.k8s.io/v1", ns=None, annotations={WAVE: wave},
                   spec={"group": "acme.io", "names": {"kind": "Widget"}})

    def cr(wave):
        return obj("Widget", "w", "acme.io/v1", annotations={WAVE: wave})

    assert severities(waves([crd("1"), cr("0")])) == {"cr-before-crd:Widget/app/w": "blocker"}
    same = waves([crd("1"), cr("1")])
    assert severities(same) == {"cr-with-crd-same-wave:Widget/app/w": "major"} and "SkipDryRunOnMissingResource=true" in same["findings"][0]["summary"]
    assert waves([crd("0"), cr("1")])["findings"] == []
    assert waves([cr("0")])["findings"] == []  # CRD not part of this application
    other_version = obj("Widget", "w", "other.io/v1", annotations={WAVE: "0"})
    assert waves([crd("1"), other_version])["findings"] == []  # same kind name, different API group


def test_sync_wave_workloads_need_their_config_applied_first():
    pod = pod_template(serviceAccountName="sa", container={"envFrom": [{"configMapRef": {"name": "cfg"}}, {"secretRef": {"name": "opt", "optional": True}}],
                                                           "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "sec", "key": "k"}}}]},
                       volumes=[{"name": "v", "persistentVolumeClaim": {"claimName": "data"}}, {"name": "p", "projected": {"sources": [{"configMap": {"name": "proj"}}]}}],
                       imagePullSecrets=[{"name": "pull"}])
    deploy = obj("Deployment", "web", "apps/v1", annotations={WAVE: "0"}, spec=pod)
    deps = [obj("ConfigMap", "cfg", annotations={WAVE: "1"}), obj("Secret", "sec", annotations={WAVE: "1"}), obj("Secret", "opt", annotations={WAVE: "1"}),
            obj("ServiceAccount", "sa", annotations={WAVE: "1"}), obj("PersistentVolumeClaim", "data", annotations={WAVE: "1"}),
            obj("ConfigMap", "proj", annotations={WAVE: "1"}), obj("Secret", "pull", annotations={WAVE: "1"})]
    result = waves([deploy, *deps])
    assert severities(result) == {f"dependency-after-workload:Deployment/app/web:{ref}": "blocker" for ref in
                                  ("ConfigMap/cfg", "ConfigMap/proj", "PersistentVolumeClaim/data", "Secret/pull", "Secret/sec", "ServiceAccount/sa")}
    assert waves([deploy, *[obj(d["kind"], d["metadata"]["name"], annotations={WAVE: "0"}) for d in deps]])["findings"] == []
    assert waves([deploy, *[obj(d["kind"], d["metadata"]["name"], annotations={WAVE: "-1"}) for d in deps]])["findings"] == []
    elsewhere = obj("ConfigMap", "cfg", ns="other", annotations={WAVE: "9"})
    assert waves([obj("Deployment", "web", "apps/v1", spec=pod_template(container={"envFrom": [{"configMapRef": {"name": "cfg"}}]})), elsewhere])["findings"] == []


def test_sync_wave_dependencies_are_ordered_by_phase_before_wave():
    pre_hook = obj("Job", "migrate", "batch/v1", annotations={HOOK: "PreSync", WAVE: "9"}, spec=pod_template(serviceAccountName="sa"))
    sa = obj("ServiceAccount", "sa", annotations={WAVE: "-9"})
    assert severities(waves([pre_hook, sa])) == {"dependency-after-workload:Job/app/migrate:ServiceAccount/sa": "blocker"}
    post = obj("Job", "notify", "batch/v1", annotations={HOOK: "PostSync", WAVE: "-9"}, spec=pod_template(serviceAccountName="sa"))
    assert waves([post, obj("ServiceAccount", "sa", annotations={WAVE: "9"})])["findings"] == []


def test_sync_wave_cron_jobs_and_pods_are_inspected():
    cron = obj("CronJob", "nightly", "batch/v1", annotations={WAVE: "0"},
               spec={"jobTemplate": {"spec": {"template": {"spec": {"containers": [], "serviceAccountName": "sa"}}}}})
    pod = obj("Pod", "p", annotations={WAVE: "0"}, spec={"containers": [], "serviceAccountName": "sa"})
    sa = obj("ServiceAccount", "sa", annotations={WAVE: "1"})
    assert set(severities(waves([cron, pod, sa]))) == {"dependency-after-workload:CronJob/app/nightly:ServiceAccount/sa", "dependency-after-workload:Pod/app/p:ServiceAccount/sa"}


def test_sync_wave_rejects_malformed_manifests():
    invalid("gitops_sync_wave_check", {"manifests": [{"kind": "Namespace"}]}, "manifests")
    invalid("gitops_sync_wave_check", {"manifests": [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": ""}}]}, "manifests")


# --- gitops_prune_risk ----------------------------------------------------------------------------------------


def prune(pruned, environment="nonprod", automated=False, desired_count=5, allow_empty=False):
    return ok("gitops_prune_risk", {"pruned": pruned, "environment": environment, "automated": automated, "desired_count": desired_count, "allow_empty": allow_empty})


def test_prune_of_nothing_needs_only_the_sync_level():
    assert prune([])["autonomy_required"] == "L2" and prune([], "prod")["autonomy_required"] == "L3"
    result = prune([], "prod")
    assert (result["pruned_count"], result["protected_count"], result["unprotected_count"], result["human_approval_required"], result["verdict"]) == (0, 0, 0, True, "pass")
    assert prune([])["human_approval_required"] is False


@pytest.mark.parametrize("kind,api,spec,prod,nonprod", [
    ("Namespace", "v1", None, "blocker", "blocker"),
    ("CustomResourceDefinition", "apiextensions.k8s.io/v1", None, "blocker", "blocker"),
    ("PersistentVolumeClaim", "v1", None, "blocker", "major"),
    ("PersistentVolume", "v1", None, "major", "major"),
    ("StatefulSet", "apps/v1", {}, "major", "minor"),
    ("StatefulSet", "apps/v1", {"persistentVolumeClaimRetentionPolicy": {"whenDeleted": "Retain"}}, "major", "minor"),
    ("StatefulSet", "apps/v1", {"persistentVolumeClaimRetentionPolicy": {"whenDeleted": "Delete"}}, "blocker", "blocker"),
    ("Secret", "v1", None, "major", "minor"),
    ("ClusterRole", "rbac.authorization.k8s.io/v1", None, "major", "minor"),
    ("ClusterRoleBinding", "rbac.authorization.k8s.io/v1", None, "major", "minor"),
    ("Role", "rbac.authorization.k8s.io/v1", None, "major", "minor"),
    ("RoleBinding", "rbac.authorization.k8s.io/v1", None, "major", "minor"),
    ("NetworkPolicy", "networking.k8s.io/v1", None, "major", "minor"),
    ("MutatingWebhookConfiguration", "admissionregistration.k8s.io/v1", None, "major", "minor"),
    ("ValidatingWebhookConfiguration", "admissionregistration.k8s.io/v1", None, "major", "minor"),
    ("ValidatingAdmissionPolicy", "admissionregistration.k8s.io/v1", None, "major", "minor"),
    ("ValidatingAdmissionPolicyBinding", "admissionregistration.k8s.io/v1", None, "major", "minor"),
    ("Bucket", "s3.aws.upbound.io/v1beta1", {}, "blocker", "major"),
    ("Cluster", "rds.aws.crossplane.io/v1alpha1", {"deletionPolicy": "Delete"}, "blocker", "major"),
])
def test_prune_severity_by_kind_and_environment(kind, api, spec, prod, nonprod):
    item = obj(kind, "x", api, spec=spec)
    for environment, expected in (("prod", prod), ("nonprod", nonprod)):
        findings = [f for f in prune([item], environment)["findings"] if not f["id"].startswith("prod-auto")]
        assert [f["severity"] for f in findings] == [expected], (kind, environment)


def test_prune_ignores_harmless_kinds_orphaned_cloud_resources_and_crossplane_configuration():
    harmless = [obj("Deployment", "d", "apps/v1"), obj("Service", "s"), obj("ConfigMap", "c"), obj("Ingress", "i", "networking.k8s.io/v1")]
    assert prune(harmless, "prod")["findings"] == []
    orphan = prune([obj("Bucket", "b", "s3.aws.upbound.io/v1beta1", spec={"deletionPolicy": "Orphan"})], "prod")
    assert severities(orphan) == {"prune-managed-orphan:Bucket/app/b": "info"}
    assert prune([obj("Composition", "c", "apiextensions.crossplane.io/v1", spec={})], "prod")["findings"] == []
    assert prune([obj("Provider", "p", "pkg.crossplane.io/v1", spec={})], "prod")["findings"] == []


def test_prune_protection_annotations():
    key = "argocd.argoproj.io/sync-options"

    def pvc(value):
        return obj("PersistentVolumeClaim", "data", annotations={key: value})

    saved = prune([pvc("Prune=false")], "prod")
    assert severities(saved) == {"protected:PersistentVolumeClaim/app/data": "info"} and (saved["protected_count"], saved["unprotected_count"]) == (1, 0)
    assert saved["autonomy_required"] == "L3"
    assert prune([pvc("Validate=false, Prune=false")], "prod")["protected_count"] == 1
    confirm = prune([pvc("Prune=confirm")], "prod")
    assert "confirmation" in confirm["findings"][0]["summary"] and confirm["protected_count"] == 1
    other = prune([pvc("Prune=true")], "prod")
    assert other["protected_count"] == 0 and other["autonomy_required"] == "L4"


def test_prune_autonomy_levels():
    cm = [obj("ConfigMap", "c")]
    assert prune(cm)["autonomy_required"] == "L2" and prune(cm, "prod")["autonomy_required"] == "L4"
    assert prune(cm, "prod")["human_approval_required"] is True
    auto = prune(cm, "prod", automated=True)
    assert severities(auto) == {"prod-auto-prune": "major"}
    assert prune(cm, "nonprod", automated=True)["findings"] == []
    assert prune([obj("Namespace", "n", ns=None)], "nonprod")["autonomy_required"] == "L2"


def test_prune_empty_desired_state():
    cm = [obj("ConfigMap", "c")]
    assert severities(prune(cm, automated=True, desired_count=0, allow_empty=True)) == {"empty-desired-state": "blocker"}
    assert severities(prune(cm, automated=True, desired_count=0, allow_empty=False)) == {"empty-desired-state-refused": "info"}
    assert severities(prune(cm, automated=False, desired_count=0)) == {"empty-desired-state-manual": "major"}
    assert prune(cm, automated=True, desired_count=1, allow_empty=True)["findings"] == []
    assert prune([], automated=True, desired_count=0, allow_empty=True)["findings"] == []


def test_prune_rejects_malformed_manifests():
    invalid("gitops_prune_risk", {"pruned": [{"kind": "Secret"}], "environment": "prod", "automated": False, "desired_count": 1, "allow_empty": False}, "pruned")
    invalid("gitops_prune_risk", {"pruned": [], "environment": "prod", "automated": False, "desired_count": -1, "allow_empty": False}, "desired_count")


# --- gitops_rollout_check -------------------------------------------------------------------------------------


def rollout(canary=None, blue_green=None, replicas=None, **spec_over):
    strategy = {}
    if canary is not None:
        strategy["canary"] = canary
    if blue_green is not None:
        strategy["blueGreen"] = blue_green
    spec = {"selector": {"matchLabels": {"app": "web"}}, "template": {"spec": {"containers": []}}, "strategy": strategy, **spec_over}
    if replicas is not None:
        spec["replicas"] = replicas
    return {"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout", "metadata": {"name": "web"}, "spec": spec}


def check_rollout(document, templates=("success-rate",), **extra):
    return ok("gitops_rollout_check", {"rollout": document, "analysis_templates": list(templates), **extra})


ANALYSIS_STEP = {"analysis": {"templates": [{"templateName": "success-rate"}]}}


def test_rollout_canary_happy_path():
    result = check_rollout(rollout({"steps": [{"setWeight": 20}, ANALYSIS_STEP, {"setWeight": 50}, {"pause": {"duration": "5m"}}, {"pause": {}}, {"setWeight": 100}]}, replicas=10))
    assert result == {"state": "SUCCESS", "strategy": "canary", "step_count": 6, "effective_weights": [20, 50, 100], "manual_gate_count": 1,
                      "total_pause_seconds": 300, "analysis_count": 1, "verdict": "pass",
                      "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}


def test_rollout_traffic_routing_makes_weights_exact():
    routed = {"canaryService": "c", "stableService": "s", "trafficRouting": {"nginx": {"stableIngress": "i"}}, "steps": [{"setWeight": 10}, ANALYSIS_STEP]}
    result = check_rollout(rollout(routed, replicas=2), max_first_step_weight=100)
    assert result["effective_weights"] == [10] and result["findings"] == []
    assert severities(check_rollout(rollout({**routed, "stableService": None}, replicas=2), max_first_step_weight=100)) == {"traffic-routing-services": "blocker"}
    istio = {"steps": [ANALYSIS_STEP], "trafficRouting": {"istio": {"destinationRule": {"name": "d"}, "virtualService": {"name": "v"}}}}
    assert check_rollout(rollout(istio))["findings"] == []
    for step in ({"setHeaderRoute": {"name": "h"}}, {"setMirrorRoute": {"name": "m"}}):
        assert severities(check_rollout(rollout({"steps": [step, ANALYSIS_STEP]})))["step-needs-traffic-routing:0"] == "blocker"
    assert severities(check_rollout(rollout({"steps": [{"setCanaryScale": {"weight": 10}}, ANALYSIS_STEP]})))["step-needs-traffic-routing:0"] == "major"
    routed_istio = {**istio, "stableService": "s", "canaryService": "c", "steps": [{"setCanaryScale": {"weight": 10}}, {"setHeaderRoute": {"name": "h"}}, ANALYSIS_STEP]}
    assert severities(check_rollout(rollout(routed_istio))) == {"managed-routes-missing": "blocker"}
    managed = {**routed_istio, "trafficRouting": {**istio["trafficRouting"], "managedRoutes": [{"name": "h"}]}}
    assert check_rollout(rollout(managed))["findings"] == []


def test_rollout_weight_rules():
    assert severities(check_rollout(rollout({"steps": [{"setWeight": 10}, {"setWeight": 5}, ANALYSIS_STEP]}, replicas=100))) == {"weight-decrease:1": "major"}
    assert severities(check_rollout(rollout({"steps": [{"setWeight": 10}, {"setWeight": 10}, ANALYSIS_STEP]}, replicas=100))) == {}
    for bad in (101, -1, 5.5, "10", True, None):
        assert severities(check_rollout(rollout({"steps": [{"setWeight": bad}, ANALYSIS_STEP]}))) == {"weight-invalid:0": "blocker"}, bad
    assert check_rollout(rollout({"steps": [{"setWeight": 0}, {"setWeight": 100}, ANALYSIS_STEP]}, replicas=10), max_first_step_weight=100)["findings"] == []


def test_rollout_first_step_limit_boundary():

    def steps(weight):
        return {"steps": [{"setWeight": weight}, ANALYSIS_STEP]}

    assert check_rollout(rollout(steps(25), replicas=100))["findings"] == []
    assert severities(check_rollout(rollout(steps(26), replicas=100))) == {"large-first-step": "minor"}
    assert check_rollout(rollout(steps(26), replicas=100), max_first_step_weight=26)["findings"] == []
    assert severities(check_rollout(rollout(steps(26), replicas=100), max_first_step_weight=25)) == {"large-first-step": "minor"}


def test_rollout_weight_granularity_reports_replica_rounding():
    # 2 replicas, maxSurge 0, 10%: floor 0 pods is not allowed for a 1-99% step, so one of two pods = 50% (Argo Rollouts' own choice)
    result = check_rollout(rollout({"steps": [{"setWeight": 10}, ANALYSIS_STEP], "maxSurge": 0}, replicas=2), max_first_step_weight=100)
    assert severities(result) == {"weight-granularity:0": "minor"} and result["effective_weights"] == [50]
    assert "asks for 10% but 2 replica(s) without traffic routing expose 50%" in result["findings"][0]["summary"]
    assert check_rollout(rollout({"steps": [{"setWeight": 20}, {"setWeight": 45}, ANALYSIS_STEP]}, replicas=5))["effective_weights"] == [20, 40]


def test_rollout_pause_durations():
    def pauses(*durations):
        return check_rollout(rollout({"steps": [{"pause": {"duration": d}} for d in durations] + [ANALYSIS_STEP]}))

    result = pauses(30, "45", "10s", "1m", "1h30m", "2h")
    assert result["total_pause_seconds"] == 30 + 45 + 10 + 60 + 5400 + 7200 and result["findings"] == [] and result["manual_gate_count"] == 0
    for bad in ("bad", "10x", -5, 1.5, "", True):
        assert severities(pauses(bad)) == {"pause-invalid:0": "blocker"}, bad
    assert check_rollout(rollout({"steps": [{"pause": {}}, {"pause": {}}, ANALYSIS_STEP]}))["manual_gate_count"] == 2


def test_rollout_step_shapes():
    assert severities(check_rollout(rollout({"steps": [{}, ANALYSIS_STEP]}))) == {"step-invalid:0": "blocker"}
    assert severities(check_rollout(rollout({"steps": [{"setWeight": 10, "pause": {}}, ANALYSIS_STEP]}))) == {"step-invalid:0": "blocker"}
    assert severities(check_rollout(rollout({"steps": [{"setWeight": 10, "foo": 1}, ANALYSIS_STEP]}))) == {"step-invalid:0": "blocker"}
    assert severities(check_rollout(rollout({"steps": ["x", ANALYSIS_STEP]}))) == {"step-invalid:0": "blocker"}
    assert severities(check_rollout(rollout({"steps": "x"}))) == {"steps-invalid": "blocker", "no-steps": "major", "no-analysis": "major"}
    assert severities(check_rollout(rollout({}))) == {"no-steps": "major", "no-analysis": "major"}
    assert check_rollout(rollout({"steps": [{"experiment": {"duration": "5m"}}, ANALYSIS_STEP]}))["findings"] == []
    assert check_rollout(rollout({"steps": [{"plugin": {"name": "p"}}, ANALYSIS_STEP]}))["findings"] == []


def test_rollout_analysis_templates_must_exist():
    missing = {"steps": [{"analysis": {"templates": [{"templateName": "success-rate"}, {"templateName": "latency"}]}}]}
    result = check_rollout(rollout(missing))
    assert severities(result) == {"analysis-template-missing:latency": "blocker"} and result["analysis_count"] == 2
    assert check_rollout(rollout(missing), templates=["success-rate", "latency"])["findings"] == []
    assert severities(check_rollout(rollout({"steps": [{"analysis": {}}]}))) == {"analysis-no-templates:step 0 analysis": "blocker", "no-analysis": "major"}
    assert severities(check_rollout(rollout({"steps": [{"analysis": {"templates": [{}]}}]})))["analysis-template-name:step 0 analysis"] == "blocker"
    background = check_rollout(rollout({"steps": [{"setWeight": 50}, {"pause": {}}], "analysis": {"templates": [{"templateName": "success-rate"}], "startingStep": 1}}, replicas=100),
                               max_first_step_weight=50)
    assert background["findings"] == [] and background["analysis_count"] == 1
    late = check_rollout(rollout({"steps": [{"setWeight": 50}, {"pause": {}}], "analysis": {"templates": [{"templateName": "success-rate"}], "startingStep": 2}}, replicas=100),
                         max_first_step_weight=50)
    assert severities(late) == {"background-never-starts": "minor"}
    never = check_rollout(rollout({"steps": [{"setWeight": 50}, {"pause": {}}], "analysis": {"templates": [{"templateName": "gone"}]}}, replicas=100), max_first_step_weight=50)
    assert severities(never) == {"analysis-template-missing:gone": "blocker"}


def test_rollout_surge_and_unavailable():
    def limits(**kw):
        return severities(check_rollout(rollout({"steps": [ANALYSIS_STEP], **kw})))

    assert limits(maxSurge=0, maxUnavailable=0) == {"surge-and-unavailable-zero": "blocker"}
    assert limits(maxSurge="0%", maxUnavailable=0) == {"surge-and-unavailable-zero": "blocker"}
    assert limits(maxSurge=0, maxUnavailable=1) == {} and limits(maxSurge=0) == {} and limits(maxUnavailable=0) == {}
    assert limits(maxSurge="25%", maxUnavailable="25%") == {} and limits(maxSurge="300%") == {}
    assert limits(maxUnavailable="100%") == {} and limits(maxUnavailable="101%") == {"maxUnavailable-over-100": "major"}
    for bad in (-1, "x", "10", 1.5, True):
        assert limits(maxSurge=bad) == {"maxSurge-invalid": "blocker"}, bad
    assert limits(maxUnavailable="-5%") == {"maxUnavailable-invalid": "blocker"}


def test_rollout_blue_green_rules():
    good = {"activeService": "web", "previewService": "web-preview", "prePromotionAnalysis": ANALYSIS_STEP["analysis"], "scaleDownDelaySeconds": 30}
    result = check_rollout(rollout(blue_green=good))
    assert (result["strategy"], result["findings"], result["analysis_count"], result["step_count"]) == ("blueGreen", [], 1, 0)
    assert severities(check_rollout(rollout(blue_green={"activeService": "web", "previewService": "web-preview"}))) == {"auto-promote-unverified": "major"}
    manual = check_rollout(rollout(blue_green={"activeService": "web", "previewService": "p", "autoPromotionEnabled": False}))
    assert manual["findings"] == [] and manual["manual_gate_count"] == 1
    assert severities(check_rollout(rollout(blue_green={**good, "previewService": "web"}))) == {"preview-equals-active": "blocker"}
    assert severities(check_rollout(rollout(blue_green={**good, "previewService": None}))) == {"no-preview-service": "minor"}
    assert severities(check_rollout(rollout(blue_green={**good, "activeService": None}))) == {"no-active-service": "blocker"}
    assert severities(check_rollout(rollout(blue_green={**good, "scaleDownDelaySeconds": 0}))) == {"scale-down-immediately": "minor"}
    assert severities(check_rollout(rollout(blue_green={**good, "scaleDownDelaySeconds": -1}))) == {"scale-down-delay-invalid": "blocker"}
    assert check_rollout(rollout(blue_green={**good, "postPromotionAnalysis": ANALYSIS_STEP["analysis"]}))["analysis_count"] == 2
    gone = {"templates": [{"templateName": "gone"}]}
    assert severities(check_rollout(rollout(blue_green={**good, "prePromotionAnalysis": gone}))) == {"analysis-template-missing:gone": "blocker"}


def test_rollout_top_level_rules():
    both = check_rollout(rollout({"steps": [ANALYSIS_STEP]}, {"activeService": "x"}))
    assert both["strategy"] == "invalid" and severities(both) == {"strategy-ambiguous": "blocker"}
    none = check_rollout(rollout())
    assert none["strategy"] == "invalid" and severities(none) == {"strategy-missing": "blocker"}
    bare = rollout({"steps": [ANALYSIS_STEP]})
    del bare["spec"]["selector"]
    del bare["spec"]["template"]
    assert severities(check_rollout(bare)) == {"no-selector": "blocker", "no-template": "blocker"}
    bare["spec"]["workloadRef"] = {"apiVersion": "apps/v1", "kind": "Deployment", "name": "web"}
    assert severities(check_rollout(bare)) == {"no-selector": "blocker"}
    assert severities(check_rollout(rollout({"steps": [ANALYSIS_STEP]}, replicas=-1))) == {"replicas-invalid": "blocker"}
    assert severities(check_rollout(rollout({"steps": [ANALYSIS_STEP]}, progressDeadlineSeconds=0))) == {"progress-deadline": "blocker"}
    assert check_rollout(rollout({"steps": [ANALYSIS_STEP]}, progressDeadlineSeconds=600))["findings"] == []


def test_rollout_rejects_other_resources():
    invalid("gitops_rollout_check", {"rollout": {"apiVersion": "apps/v1", "kind": "Deployment", "spec": {}}, "analysis_templates": []}, "rollout")
    invalid("gitops_rollout_check", {"rollout": {"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout"}, "analysis_templates": []}, "rollout")
    invalid("gitops_rollout_check", {"rollout": rollout(), "analysis_templates": [], "max_first_step_weight": 0}, "max_first_step_weight")
    invalid("gitops_rollout_check", {"rollout": rollout(), "analysis_templates": [], "max_first_step_weight": 101}, "max_first_step_weight")


# --- gitops_canary_verdict ------------------------------------------------------------------------------------


def metric(name="m", direction="lower-is-better", aggregation="mean", failure_limit=0, samples=(1, 1, 1), **extra):
    return {"name": name, "direction": direction, "aggregation": aggregation, "failure_limit": failure_limit, "canary_samples": list(samples), **extra}


def verdict(*metrics, min_samples=3):
    return ok("gitops_canary_verdict", {"metrics": list(metrics), "min_samples": min_samples})


def test_canary_threshold_boundaries_pass_when_equal():
    equal = verdict(metric(threshold=0.02, samples=[0.02, 0.02, 0.02]))
    assert equal["metrics"] == [{"name": "m", "status": "Successful", "samples": 3, "aggregate": 0.02, "limit": 0.02, "sample_failures": 0}]
    assert (equal["verdict"], equal["action"], equal["passed_count"]) == ("Successful", "promote", 1)
    above = verdict(metric(threshold=0.02, samples=[0.02, 0.02, 0.0201]))
    assert above["metrics"][0]["status"] == "Failed" and above["metrics"][0]["sample_failures"] == 1 and above["action"] == "abort"
    higher_equal = verdict(metric(direction="higher-is-better", threshold=50, samples=[50, 50, 50]))
    assert higher_equal["verdict"] == "Successful"
    higher_below = verdict(metric(direction="higher-is-better", threshold=50, samples=[50, 50, 49.99]))
    assert (higher_below["verdict"], higher_below["metrics"][0]["sample_failures"]) == ("Failed", 1)


def test_canary_failure_limit_tolerates_outliers_but_the_aggregate_still_counts():
    tolerated = verdict(metric(threshold=10, failure_limit=1, aggregation="p50", samples=[1, 2, 11]))
    assert tolerated["metrics"][0]["status"] == "Successful" and tolerated["metrics"][0]["sample_failures"] == 1
    over = verdict(metric(threshold=10, failure_limit=1, aggregation="p50", samples=[1, 11, 12]))
    assert over["metrics"][0]["status"] == "Failed" and over["metrics"][0]["sample_failures"] == 2
    aggregate = verdict(metric(threshold=10, failure_limit=5, aggregation="mean", samples=[1, 1, 30]))
    assert aggregate["metrics"][0]["status"] == "Failed" and aggregate["metrics"][0]["aggregate"] == 10.666667


@pytest.mark.parametrize("aggregation,expected", [("mean", 5.5), ("min", 1), ("max", 10), ("p50", 5), ("p90", 9), ("p95", 10), ("p99", 10)])
def test_canary_aggregations_use_nearest_rank_percentiles(aggregation, expected):
    result = verdict(metric(aggregation=aggregation, threshold=1000, samples=list(range(10, 0, -1))), min_samples=10)
    assert result["metrics"][0]["aggregate"] == expected


def test_canary_percentile_rank_is_ceiling_of_p_times_n():
    one = verdict(metric(aggregation="p99", threshold=100, samples=[7]), min_samples=1)
    assert one["metrics"][0]["aggregate"] == 7
    hundred = verdict(metric(aggregation="p95", threshold=1000, samples=list(range(1, 101))), min_samples=1)
    assert hundred["metrics"][0]["aggregate"] == 95
    plus_one = verdict(metric(aggregation="p95", threshold=1000, samples=list(range(1, 102))), min_samples=1)
    assert plus_one["metrics"][0]["aggregate"] == 96  # ceil(0.95 * 101) = 96
    even = verdict(metric(aggregation="p50", threshold=1000, samples=[4, 1, 3, 2]), min_samples=1)
    assert even["metrics"][0]["aggregate"] == 2  # rank ceil(0.5 * 4) = 2: nearest rank, never the mean of the middle pair


def test_canary_regression_against_the_baseline():
    base = [100, 100, 100]
    within = verdict(metric(aggregation="p50", max_regression_pct=10, samples=[110, 110, 110], baseline_samples=base))
    assert within["metrics"][0]["limit"] == 110.0 and within["verdict"] == "Successful"  # 10% worse is allowed, not more
    beyond = verdict(metric(aggregation="p50", max_regression_pct=10, samples=[110.01, 110.01, 110.01], baseline_samples=base))
    assert beyond["verdict"] == "Failed"
    zero_baseline = verdict(metric(max_regression_pct=50, samples=[0, 0, 0], baseline_samples=[0, 0, 0]))
    assert zero_baseline["verdict"] == "Successful" and zero_baseline["metrics"][0]["limit"] == 0
    assert verdict(metric(max_regression_pct=50, samples=[0.001, 0, 0], baseline_samples=[0, 0, 0]))["verdict"] == "Failed"
    higher = verdict(metric(direction="higher-is-better", aggregation="p50", max_regression_pct=10, samples=[90, 90, 90], baseline_samples=base))
    assert higher["metrics"][0]["limit"] == 90.0 and higher["verdict"] == "Successful"
    assert verdict(metric(direction="higher-is-better", aggregation="p50", max_regression_pct=10, samples=[89.9, 89.9, 89.9], baseline_samples=base))["verdict"] == "Failed"


def test_canary_uses_the_tighter_of_threshold_and_regression_limit():
    tight_threshold = verdict(metric(aggregation="p50", threshold=105, max_regression_pct=10, samples=[106, 106, 106], baseline_samples=[100, 100, 100]))
    assert tight_threshold["metrics"][0]["limit"] == 105.0 and tight_threshold["verdict"] == "Failed"
    tight_regression = verdict(metric(aggregation="p50", threshold=500, max_regression_pct=10, samples=[111, 111, 111], baseline_samples=[100, 100, 100]))
    assert tight_regression["metrics"][0]["limit"] == 110.0 and tight_regression["verdict"] == "Failed"
    higher = verdict(metric(direction="higher-is-better", aggregation="p50", threshold=95, max_regression_pct=10, samples=[92, 92, 92], baseline_samples=[100, 100, 100]))
    assert higher["metrics"][0]["limit"] == 95.0 and higher["verdict"] == "Failed"


def test_canary_inconclusive_until_min_samples_unless_already_failed():
    short = verdict(metric(threshold=10, samples=[1, 1]), min_samples=3)
    assert (short["verdict"], short["action"], short["inconclusive_count"]) == ("Inconclusive", "pause", 1)
    assert verdict(metric(threshold=10, samples=[1, 1, 1]), min_samples=3)["verdict"] == "Successful"
    assert verdict(metric(threshold=10, samples=[]), min_samples=1)["metrics"][0] == {
        "name": "m", "status": "Inconclusive", "samples": 0, "aggregate": 0.0, "limit": 10.0, "sample_failures": 0}
    early_fail = verdict(metric(threshold=10, samples=[50]), min_samples=3)
    assert early_fail["verdict"] == "Failed"
    mixed = verdict(metric("a", threshold=10, samples=[1, 1]), metric("b", threshold=10, samples=[99, 99, 99]), metric("c", threshold=10, samples=[1, 1, 1]))
    assert (mixed["verdict"], mixed["failed_count"], mixed["inconclusive_count"], mixed["passed_count"]) == ("Failed", 1, 1, 1)
    assert [m["status"] for m in mixed["metrics"]] == ["Inconclusive", "Failed", "Successful"]
    pause = verdict(metric("a", threshold=10, samples=[1, 1]), metric("c", threshold=10, samples=[1, 1, 1]))
    assert (pause["verdict"], pause["action"]) == ("Inconclusive", "pause")


def test_canary_rejects_inconsistent_metrics():
    invalid("gitops_canary_verdict", {"metrics": [metric()], "min_samples": 3}, "metrics")  # neither threshold nor regression limit
    invalid("gitops_canary_verdict", {"metrics": [metric(max_regression_pct=10)], "min_samples": 3}, "metrics")  # no baseline
    invalid("gitops_canary_verdict", {"metrics": [metric(max_regression_pct=10, baseline_samples=[])], "min_samples": 3}, "metrics")
    invalid("gitops_canary_verdict", {"metrics": [metric(threshold=1), metric(threshold=1)], "min_samples": 3}, "metrics")
    invalid("gitops_canary_verdict", {"metrics": [], "min_samples": 3}, "metrics")
    invalid("gitops_canary_verdict", {"metrics": [metric(threshold=1)], "min_samples": 0}, "min_samples")
    invalid("gitops_canary_verdict", {"metrics": [metric(threshold=1e13)], "min_samples": 1}, "metrics")
    over = metric(direction="higher-is-better", max_regression_pct=100.5, baseline_samples=[1, 1, 1])
    invalid("gitops_canary_verdict", {"metrics": [over], "min_samples": 1}, "metrics")
    edge = metric(direction="higher-is-better", max_regression_pct=100, baseline_samples=[1, 1, 1], samples=[0, 0, 0])
    assert verdict(edge)["metrics"][0]["limit"] == 0.0 and verdict(edge)["verdict"] == "Successful"  # 100% regression allows down to zero
    assert verdict(metric(max_regression_pct=500, baseline_samples=[1, 1, 1], samples=[6, 6, 6]))["verdict"] == "Successful"  # lower-is-better may exceed 100%
    invalid("gitops_canary_verdict", {"metrics": [metric(threshold=1, aggregation="p42")], "min_samples": 1}, "metrics")


# --- gitops_promotion_gate ------------------------------------------------------------------------------------

HEAD = "abcdef1234567"


def gate(**over):
    payload = {
        "now": "2026-03-10T12:00:00+00:00", "env_order": ["dev", "staging", "prod"], "source_env": "staging", "target_env": "prod",
        "deployed_at": "2026-03-10T10:00:00+00:00", "min_soak_minutes": 120, "checks": [{"name": "e2e", "status": "success"}], "required_checks": ["e2e"],
        "approvals": [{"user": "alice", "state": "approved", "commit_sha": HEAD, "at": "2026-03-10T09:00:00+00:00"}],
        "required_approvals": 1, "pr_author": "bob", "head_sha": HEAD, "target_is_production": True}
    payload.update(over)
    return ok("gitops_promotion_gate", payload)


def approval(user, state="approved", sha=HEAD, at="2026-03-10T09:00:00+00:00"):
    return {"user": user, "state": state, "commit_sha": sha, "at": at}


def test_promotion_ready_when_everything_is_met():
    result = gate()
    assert result == {"state": "SUCCESS", "verdict": "ready", "soak_elapsed_minutes": 120.0, "soak_remaining_minutes": 0, "valid_approval_count": 1,
                      "autonomy_level": "L3", "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}
    assert gate(target_is_production=False)["autonomy_level"] == "L2"


def test_promotion_soak_boundary_is_inclusive():
    assert gate(deployed_at="2026-03-10T10:00:00+00:00")["verdict"] == "ready"  # exactly 120 minutes
    short = gate(deployed_at="2026-03-10T10:00:01+00:00")
    assert (short["verdict"], short["soak_remaining_minutes"], short["soak_elapsed_minutes"]) == ("waiting", 1, 119.98)
    assert severities(short) == {"soak-incomplete": "major"}
    assert gate(deployed_at="2026-03-10T11:30:00+00:00")["soak_remaining_minutes"] == 90
    assert gate(min_soak_minutes=0, deployed_at="2026-03-10T12:00:00+00:00")["verdict"] == "ready"
    offset = gate(now="2026-03-10T14:00:00+02:00", deployed_at="2026-03-10T10:00:00Z")
    assert offset["verdict"] == "ready" and offset["soak_elapsed_minutes"] == 120.0


def test_promotion_required_checks():
    assert severities(gate(checks=[{"name": "e2e", "status": "failure"}])) == {"required-check-failed:e2e": "blocker"}
    assert severities(gate(checks=[{"name": "e2e", "status": "pending"}])) == {"required-check-pending:e2e": "major"}
    assert gate(checks=[{"name": "e2e", "status": "pending"}])["verdict"] == "waiting"
    assert severities(gate(checks=[])) == {"required-check-missing:e2e": "major"}
    for passing in ("success", "neutral", "skipped"):
        assert gate(checks=[{"name": "e2e", "status": passing}])["findings"] == []
    worst = gate(checks=[{"name": "e2e", "status": "success"}, {"name": "e2e", "status": "failure"}, {"name": "e2e", "status": "pending"}])
    assert severities(worst) == {"required-check-failed:e2e": "blocker"} and worst["verdict"] == "blocked"
    optional = gate(checks=[{"name": "e2e", "status": "success"}, {"name": "lint", "status": "failure"}, {"name": "docs", "status": "pending"}])
    assert severities(optional) == {"optional-check-failed:lint": "minor"} and optional["verdict"] == "ready"


def test_promotion_approvals():
    assert gate(approvals=[], required_approvals=0, target_is_production=False)["verdict"] == "ready"
    missing = gate(approvals=[], required_approvals=1)
    assert severities(missing) == {"approvals-missing": "major"} and missing["verdict"] == "waiting"
    two = gate(approvals=[approval("alice"), approval("carol")], required_approvals=2)
    assert (two["verdict"], two["valid_approval_count"]) == ("ready", 2)
    assert severities(gate(approvals=[approval("alice")], required_approvals=2)) == {"approvals-missing": "major"}
    stale = gate(approvals=[approval("alice", sha="1111111aaaaaa")])
    assert severities(stale) == {"stale-approval:alice": "info", "approvals-missing": "major"}
    selfish = gate(approvals=[approval("BOB")])
    assert severities(selfish) == {"self-approval:bob": "info", "approvals-missing": "major"}  # logins compare case-insensitively
    twice = gate(approvals=[approval("alice"), approval("ALICE")], required_approvals=2)
    assert twice["valid_approval_count"] == 1  # one reviewer counts once


def test_promotion_latest_review_per_user_decides():
    later_changes = gate(approvals=[approval("alice", at="2026-03-10T09:00:00+00:00"), approval("alice", "changes_requested", at="2026-03-10T09:30:00+00:00")])
    assert severities(later_changes) == {"changes-requested:alice": "blocker", "approvals-missing": "major"} and later_changes["verdict"] == "blocked"
    later_approval = gate(approvals=[approval("alice", "changes_requested", at="2026-03-10T09:00:00+00:00"), approval("alice", at="2026-03-10T09:30:00+00:00")])
    assert later_approval["verdict"] == "ready"
    input_order = gate(approvals=[approval("alice", at="2026-03-10T09:30:00+00:00"), approval("alice", "changes_requested", at="2026-03-10T09:00:00+00:00")])
    assert input_order["verdict"] == "ready"  # ordered by time, not by position
    tie = gate(approvals=[approval("alice", "changes_requested"), approval("alice")])
    assert tie["verdict"] == "ready"  # equal timestamps: the later entry wins
    dismissed = gate(approvals=[approval("alice", "dismissed")])
    assert severities(dismissed) == {"approvals-missing": "major"}
    assert gate(approvals=[approval("alice"), approval("carol", "commented")])["verdict"] == "ready"


def test_promotion_stage_order_and_production_rules():
    skipped = gate(source_env="dev", target_env="prod", deployed_at="2026-03-10T08:00:00+00:00")
    assert severities(skipped) == {"stage-skipped": "blocker"} and "staging comes first" in skipped["findings"][0]["summary"]
    assert gate(source_env="dev", target_env="staging", target_is_production=False)["verdict"] == "ready"
    assert severities(gate(required_approvals=0, approvals=[])) == {"prod-without-approval": "major"}
    assert gate(required_approvals=0, approvals=[], target_is_production=False)["findings"] == []


def test_promotion_change_freeze_start_inclusive_end_exclusive():
    def frozen(now):
        return gate(now=now, deployed_at="2026-03-10T00:00:00+00:00", freeze_start="2026-03-10T12:00:00+00:00", freeze_end="2026-03-10T18:00:00+00:00")

    assert frozen("2026-03-10T11:59:59+00:00")["verdict"] == "ready"
    assert severities(frozen("2026-03-10T12:00:00+00:00")) == {"change-freeze": "blocker"}
    assert frozen("2026-03-10T17:59:59+00:00")["verdict"] == "blocked"
    assert frozen("2026-03-10T18:00:00+00:00")["verdict"] == "ready"
    assert frozen("2026-03-10T14:00:00-04:00")["verdict"] == "ready"  # 18:00 UTC


def test_promotion_blocked_beats_waiting_beats_ready():
    both = gate(checks=[{"name": "e2e", "status": "failure"}], approvals=[])
    assert both["verdict"] == "blocked" and both["severity_counts"] == {"blocker": 1, "major": 1, "minor": 0, "info": 0}
    assert [f["severity"] for f in both["findings"]] == ["blocker", "major"]


def test_promotion_rejects_inconsistent_inputs_naming_the_field():
    def bad(field, **over):
        payload = {"now": "2026-03-10T12:00:00+00:00", "env_order": ["dev", "staging", "prod"], "source_env": "staging", "target_env": "prod",
                   "deployed_at": "2026-03-10T10:00:00+00:00", "min_soak_minutes": 0, "checks": [], "required_checks": [], "approvals": [],
                   "required_approvals": 0, "pr_author": "bob", "head_sha": HEAD, "target_is_production": False, **over}
        invalid("gitops_promotion_gate", payload, field)

    bad("now", now="2026-03-10T12:00:00")  # no offset
    bad("now", now="yesterday")
    bad("deployed_at", deployed_at="2026-03-10T10:00:00")
    bad("deployed_at", deployed_at="2026-03-10T12:00:01+00:00")  # in the future
    bad("env_order", env_order=["dev", "dev", "prod"])
    bad("source_env", source_env="qa")
    bad("target_env", target_env="qa")
    bad("target_env", target_env="staging")  # same stage
    bad("target_env", source_env="prod", target_env="dev")  # backwards
    bad("approvals", approvals=[{"user": "a", "state": "approved", "commit_sha": HEAD, "at": "2026-03-10T09:00:00"}])
    bad("freeze_start", freeze_start="2026-03-10T12:00:00+00:00")
    bad("freeze_start", freeze_end="2026-03-10T12:00:00+00:00")
    bad("freeze_start", freeze_start="2026-03-10T12:00:00", freeze_end="2026-03-10T13:00:00+00:00")
    bad("freeze_end", freeze_start="2026-03-10T12:00:00+00:00", freeze_end="2026-03-10T12:00:00+00:00")
    bad("freeze_end", freeze_start="2026-03-10T12:00:00+00:00", freeze_end="2026-03-10T11:00:00+00:00")
    bad("freeze_end", freeze_start="2026-03-10T11:00:00+00:00", freeze_end="2026-03-10T12:00:00")


# --- gitops_drift_classify ------------------------------------------------------------------------------------


def diff(path, change="changed", kind="Deployment", **extra):
    return {"kind": kind, "name": "web", "path": path, "change": change, **extra}


def classify(*diffs, ignore=()):
    return ok("gitops_drift_classify", {"diffs": list(diffs), "ignore_differences": list(ignore)})


def klass(*args, **kw):
    return classify(diff(*args, **kw))["classified"][0]["class"]


def test_drift_empty_and_summary_shape():
    assert classify() == {"state": "SUCCESS", "benign_count": 0, "drift_count": 0, "dangerous_count": 0, "verdict": "clean", "recommended_action": "ignore", "classified": []}
    result = classify(diff("spec.minReadySeconds"), diff("status.replicas"), diff("spec.template.spec.containers[0].image"),
                      diff("$", "removed", kind="ConfigMap", namespace="shop"))
    assert (result["benign_count"], result["drift_count"], result["dangerous_count"]) == (1, 2, 1)
    assert (result["verdict"], result["recommended_action"]) == ("dangerous", "investigate")
    assert [c["class"] for c in result["classified"]] == ["dangerous", "drift", "drift", "benign"]
    assert result["classified"][1] == {"resource": "ConfigMap/shop/web", "path": "$", "class": "drift", "reason": "resource is missing from the cluster"}
    assert classify(diff("spec.minReadySeconds"))["recommended_action"] == "self-heal"
    assert classify(diff("status.x"))["verdict"] == "clean"


@pytest.mark.parametrize("path", ["metadata.resourceVersion", "metadata.uid", "metadata.generation", "metadata.creationTimestamp", "metadata.selfLink",
                                  "metadata.managedFields", "metadata.managedFields[0].time", "status", "status.conditions[0].status",
                                  "metadata.annotations.kubectl.kubernetes.io/last-applied-configuration",
                                  "metadata.annotations.deployment.kubernetes.io/revision",
                                  "metadata.annotations.deprecated.daemonset.template.generation"])
def test_drift_server_populated_fields_are_benign(path):
    assert klass(path) == "benign" and klass(path, kind="ClusterRoleBinding") == "benign"


@pytest.mark.parametrize("path", ["spec.template.spec.containers[0].terminationMessagePath", "spec.template.spec.containers[name=app].terminationMessagePolicy",
                                  "spec.template.spec.containers[0].imagePullPolicy", "spec.template.spec.containers[0].ports[0].protocol",
                                  "spec.template.spec.dnsPolicy", "spec.template.spec.restartPolicy", "spec.template.spec.schedulerName",
                                  "spec.template.spec.securityContext", "spec.template.spec.terminationGracePeriodSeconds", "spec.revisionHistoryLimit",
                                  "spec.progressDeadlineSeconds", "spec.strategy", "spec.clusterIP", "spec.clusterIPs", "spec.ipFamilies", "spec.ipFamilyPolicy",
                                  "spec.internalTrafficPolicy", "spec.sessionAffinity"])
def test_drift_api_defaults_are_benign_only_when_the_live_object_added_them(path):
    assert klass(path, "added") == "benign"
    assert klass(path, "changed") != "benign" and klass(path, "removed") != "benign"


def test_drift_default_suffix_matching_is_exact():
    assert klass("spec.template.spec.securityContext.runAsUser", "added") == "dangerous"
    assert klass("spec.template.spec.dnsPolicyX", "added") == "drift"
    assert klass("spec.extra.protocol", "added") == "benign" and klass("spec.protocolVersion", "added") == "drift"


def test_drift_replica_count_is_benign_only_for_the_autoscaler():
    assert klass("spec.replicas", field_manager="kube-controller-manager") == "benign"
    assert klass("spec.replicas", field_manager="kubectl-edit") == "drift"
    assert klass("spec.replicas") == "drift"
    assert klass("spec.replicas", kind="Service", field_manager="kube-controller-manager") == "drift"
    for kind in ("Deployment", "ReplicaSet", "StatefulSet"):
        assert klass("spec.replicas", kind=kind, field_manager="kube-controller-manager") == "benign"
    assert klass("spec.replicas", kind="DaemonSet", field_manager="kube-controller-manager") == "drift"


GUARDRAILS = ["ClusterRole", "ClusterRoleBinding", "MutatingWebhookConfiguration", "NetworkPolicy", "Role", "RoleBinding", "ValidatingAdmissionPolicy",
              "ValidatingAdmissionPolicyBinding", "ValidatingWebhookConfiguration"]


@pytest.mark.parametrize("kind", GUARDRAILS)
def test_drift_guardrail_kinds_are_dangerous_for_any_change(kind):
    assert klass("rules[0].verbs", kind=kind) == "dangerous" and klass("$", "added", kind=kind) == "dangerous" and klass("$", "removed", kind=kind) == "dangerous"
    assert klass("spec.anything", "added", kind=kind) == "dangerous"


@pytest.mark.parametrize("fragment", ["securityContext.privileged", "spec.template.spec.hostNetwork", "spec.template.spec.hostPID", "spec.template.spec.hostIPC",
                                      "spec.template.spec.volumes[0].hostPath.path", "spec.template.spec.containers[0].securityContext.capabilities.add",
                                      "spec.template.spec.serviceAccountName", "spec.template.spec.automountServiceAccountToken",
                                      "spec.template.spec.imagePullSecrets[0].name", "spec.template.spec.containers[0].securityContext.runAsUser"])
def test_drift_security_relevant_fields_are_dangerous(fragment):
    assert klass(fragment) == "dangerous"


@pytest.mark.parametrize("path", ["spec.template.spec.containers[0].image", "spec.template.spec.initContainers[name=init].image", "image"])
def test_drift_image_changes_are_dangerous_but_other_image_fields_are_not(path):
    assert klass(path) == "dangerous"
    assert klass(path + "PullPolicy") == "drift" and klass("spec.imageRef") == "drift"


def test_drift_secret_and_service_specific_rules():
    assert klass("data.password", kind="Secret") == "dangerous" and klass("stringData.x", kind="Secret") == "dangerous" and klass("data", kind="Secret") == "dangerous"
    assert klass("metadata.labels.x", kind="Secret") == "drift" and klass("type", kind="Secret") == "drift"
    assert klass("data.x", kind="ConfigMap") == "drift"
    for path in ("spec.type", "spec.externalIPs", "spec.loadBalancerSourceRanges"):
        assert klass(path, kind="Service") == "dangerous"
    # externalIPs and loadBalancerSourceRanges are security-relevant on any kind; spec.type only on a Service
    assert klass("spec.externalIPs", kind="Deployment") == "dangerous" and klass("spec.loadBalancerSourceRanges", kind="Deployment") == "dangerous"
    assert klass("spec.type", kind="Deployment") == "drift" and klass("spec.ports[0].port", kind="Service") == "drift"


def test_drift_ignore_differences_prefixes():
    ignore = ["spec.template.metadata.annotations", "spec.replicas", ".metadata.labels"]
    assert klass("spec.template.metadata.annotations.kubectl.kubernetes.io/restartedAt") == "drift"
    result = classify(diff("spec.template.metadata.annotations.x"), diff("spec.template.metadata.annotations"), diff("spec.replicas"),
                      diff("metadata.labels[0]"), diff("spec.replicasX"), ignore=ignore)
    assert [(c["path"], c["class"]) for c in result["classified"]] == [
        ("spec.replicasX", "drift"), ("metadata.labels[0]", "benign"), ("spec.replicas", "benign"),
        ("spec.template.metadata.annotations", "benign"), ("spec.template.metadata.annotations.x", "benign")]
    assert classify(diff("spec.template.spec.containers[0].image"), ignore=["spec.template.spec.containers[*].image"])["verdict"] == "clean"
    assert classify(diff("spec.template.spec.containers[0].image"), ignore=["spec.template.spec.containers"])["verdict"] == "clean"
    assert result["classified"][1]["reason"] == "covered by ignore_differences"


def test_drift_path_normalisation():
    assert klass("$.spec.template.spec.dnsPolicy", "added") == "benign" and klass(".spec.template.spec.dnsPolicy", "added") == "benign"
    assert klass("status") == "benign" and klass("$") == "drift" and klass("") == "drift"
    assert klass("spec.containers[image=x].name") == "drift"
    invalid("gitops_drift_classify", {"diffs": [diff("x", change="moved")], "ignore_differences": []}, "diffs")
    invalid("gitops_drift_classify", {"diffs": [], "ignore_differences": [""]}, "ignore_differences")


# --- gitops_image_policy --------------------------------------------------------------------------------------

DIGEST = "sha256:" + "a" * 64
TAGS = ["1.4.2", "v1.5.0", "1.5.1-rc.1", "2.0.0", "latest", "1.10.0", "1.9", "nope"]


def image(ref, tags=(), policy=None, require_digest=False):
    payload = {"image": ref, "candidate_tags": list(tags), "require_digest": require_digest}
    if policy is not None:
        payload["policy"] = policy
    return ok("gitops_image_policy", payload)


def semver(range_, ref="r.io/a/web:1.4.2", tags=TAGS):
    return image(ref, tags, {"type": "semver", "range": range_})


def test_image_reference_parsing():
    result = image("localhost:5000/team/app:v1.2@" + DIGEST)
    assert (result["repository"], result["tag"], result["digest"], result["digest_pinned"], result["floating"]) == ("localhost:5000/team/app", "v1.2", DIGEST, True, False)
    assert severities(result) == {"tag-ignored-with-digest": "info"} and result["verdict"] == "pass"
    bare = image("nginx")
    assert (bare["repository"], bare["tag"], bare["floating"]) == ("nginx", "", True)
    port_only = image("registry.local:5000/app")
    assert (port_only["repository"], port_only["tag"]) == ("registry.local:5000/app", "")
    only_digest = image("app@" + DIGEST)
    assert (only_digest["tag"], only_digest["digest_pinned"], only_digest["floating"], only_digest["findings"]) == ("", True, False, [])


@pytest.mark.parametrize("ref", ["", "has space:1", "app@sha256:abc", "app@md5:" + "a" * 32, "app@sha256:" + "A" * 64, "app:" + "x" * 129, "app:-bad", "app:", "/app:1", "app/:1", ":1"])
def test_image_rejects_invalid_references(ref):
    payload = {"image": ref, "candidate_tags": [], "require_digest": False}
    assert call("gitops_image_policy", payload)["state"] == "INVALID_INPUT" and call("gitops_image_policy", payload)["field"] == "image"


def test_image_sha512_digest_is_accepted():
    assert image("app:1@sha512:" + "b" * 128)["digest_pinned"] is True


@pytest.mark.parametrize("tag", ["latest", "edge", "nightly", "main", "master", "dev", "develop", "stable"])
def test_image_mutable_tags_are_floating_unless_a_digest_pins_them(tag):
    assert severities(image(f"app:{tag}")) == {"floating-tag": "major"} and image(f"app:{tag}")["floating"] is True
    assert image(f"app:{tag}@{DIGEST}")["floating"] is False


def test_image_tag_pinning_policy():
    assert severities(image("app:1.2.3")) == {"tag-not-immutable": "info"} and image("app:1.2.3")["verdict"] == "pass"
    strict = image("app:1.2.3", require_digest=True)
    assert severities(strict) == {"digest-required": "major"} and strict["verdict"] == "fail"
    assert image("app@" + DIGEST, require_digest=True)["findings"] == []
    assert severities(image("app:latest", require_digest=True)) == {"floating-tag": "major"}


@pytest.mark.parametrize("range_,selected,eligible,in_range", [
    ("^1.4.0", "1.10.0", 4, True), ("~1.4", "1.4.2", 1, True), ("~1.4.0", "1.4.2", 1, True), ("~1", "1.10.0", 4, True), ("1.x", "1.10.0", 4, True),
    ("1.*", "1.10.0", 4, True), ("1.5.x", "v1.5.0", 1, False), ("1.5.0 - 1.9", "1.9", 2, False), ("1.4.2 - 1.5.0", "v1.5.0", 2, True),
    ("<2", "1.10.0", 4, True), ("<2.0.0", "1.10.0", 4, True), ("<=1.5.0", "v1.5.0", 2, True), ("<=1.5", "v1.5.0", 2, True), ("*", "2.0.0", 5, True),
    (">=1.4.2 <1.10.0", "1.9", 3, True), (">=1.4.2,<1.10.0", "1.9", 3, True), (">= 1.5.0", "2.0.0", 4, False), (">1.9", "2.0.0", 2, False),
    (">1.10.0", "2.0.0", 1, False), (">1.5", "2.0.0", 3, False), ("=1.4.2", "1.4.2", 1, True), ("1.4.2", "1.4.2", 1, True), ("!=1.10.0 <2", "1.9", 3, True),
    (">1.9 || 2.0.0", "2.0.0", 2, False), ("^0.2", "", 0, False), ("^1.5.0", "1.10.0", 3, False), ("^1", "1.10.0", 4, True), ("1.5.1-rc.1", "1.5.1-rc.1", 1, False),
    (">=1.5.1-rc.0", "2.0.0", 4, False), ("~> 1.4", "1.4.2", 1, True),
])
def test_image_semver_ranges(range_, selected, eligible, in_range):
    result = semver(range_)
    assert (result["selected_tag"], result["eligible_count"], result["current_in_range"]) == (selected, eligible, in_range), range_


def test_image_semver_default_range_and_prerelease_rules():
    default = image("app:1.4.2", TAGS, {"type": "semver"})
    assert (default["selected_tag"], default["eligible_count"]) == ("2.0.0", 5)
    assert "1.5.1-rc.1" not in semver("*")["selected_tag"] and semver("^1.5.0-0")["selected_tag"] == "1.10.0"
    only_pre = image("app:1.0.0", ["1.5.1-rc.1", "1.5.1-rc.2", "1.5.1-beta"], {"type": "semver", "range": ">=1.5.1-0"})
    assert only_pre["selected_tag"] == "1.5.1-rc.2" and only_pre["eligible_count"] == 3  # rc.2 > rc.1 > beta
    ordering = image("app:1", ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"],
                     {"type": "semver", "range": ">=1.0.0-0 <1.0.0-zzz"})
    assert ordering["selected_tag"] == "1.0.0-rc.1" and ordering["eligible_count"] == 7  # the semver.org precedence example
    numeric_vs_alpha = image("app:1", ["1.0.0-2", "1.0.0-alpha", "1.0.0-10"], {"type": "semver", "range": ">=1.0.0-0"})
    assert numeric_vs_alpha["selected_tag"] == "1.0.0-alpha"  # alphanumeric identifiers outrank numeric ones


def test_image_semver_ignores_non_versions_and_breaks_ties_deterministically():
    tags = ["latest", "nope", "1.2.3_x", "v1", "1.0", "1.0.0", "01.2.3", "x.1.2"]
    result = image("app:1.0.0", tags, {"type": "semver", "range": "*"})
    assert result["selected_tag"] == "01.2.3" and result["eligible_count"] == 4  # v1, 1.0, 1.0.0 and 01.2.3 (= 1.2.3) are versions
    tie = image("app:1.0.0", ["1.0", "v1.0.0", "1.0.0"], {"type": "semver", "range": "*"})
    assert tie["selected_tag"] == "v1.0.0"  # equal versions: the greater tag string wins


def test_image_semver_findings():
    older = image("app:1.10.0", ["1.4.2", "1.9.0"], {"type": "semver", "range": "^1.0.0"})
    assert older["selected_tag"] == "1.9.0" and severities(older) == {"selected-older-than-current": "minor", "tag-not-immutable": "info"}
    assert older["update_available"] is True
    unbounded = semver("*")
    assert severities(unbounded)["range-unbounded-major"] == "minor" and "range-unbounded-major" in severities(semver(">=1.0.0"))
    assert "range-unbounded-major" not in severities(semver("^1.0.0")) and "range-unbounded-major" not in severities(semver("<3"))
    assert "range-unbounded-major" in severities(semver("!=1.0.0"))
    none = semver("^5.0.0")
    assert severities(none)["no-eligible-candidate"] == "major" and none["selected_tag"] == "" and none["update_available"] is False
    not_semver = image("app:latest", ["1.0.0"], {"type": "semver", "range": "^1.0.0"})
    assert "current-tag-not-semver" in severities(not_semver) and not_semver["current_in_range"] is False
    assert image("app:1.4.2", ["1.4.2"], {"type": "semver", "range": "^1.0.0"})["update_available"] is False
    assert image("app:1.4.2", [], {"type": "semver", "range": "^1.0.0"})["findings"][0]["id"] != "no-eligible-candidate"


@pytest.mark.parametrize("range_", ["^", ">>1", "1.2.3.4", "a.b.c", "1.2.3 ||", "|| 1", ">=", "1.2.3 - ", "~"])  # x.1.2 and >1.x.3-rc are valid in Masterminds
def test_image_rejects_malformed_semver_ranges(range_):
    result = call("gitops_image_policy", {"image": "app:1", "candidate_tags": ["1.0.0"], "policy": {"type": "semver", "range": range_}, "require_digest": False})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "policy"


def test_image_alphabetical_and_numerical_policies():
    stamps = ["20240101", "20240301", "20240201"]
    alpha = image("app:20240101", stamps, {"type": "alphabetical"})
    assert (alpha["selected_tag"], alpha["update_available"], alpha["eligible_count"]) == ("20240301", True, 3)
    assert image("app:x", stamps, {"type": "alphabetical", "order": "desc"})["selected_tag"] == "20240101"
    assert image("app:x", ["b", "B", "a"], {"type": "alphabetical", "order": "asc"})["selected_tag"] == "b"
    assert image("app:x", ["b", "B", "a"], {"type": "alphabetical", "order": "desc"})["selected_tag"] == "B"
    numeric = image("app:9", ["9", "10", "100", "2.5", "abc", "1e3", "10.0"], {"type": "numerical", "order": "asc"})
    assert (numeric["selected_tag"], numeric["eligible_count"], numeric["current_in_range"]) == ("100", 5, True)
    assert image("app:9", ["9", "10", "100", "2.5", "abc"], {"type": "numerical", "order": "desc"})["selected_tag"] == "2.5"
    assert image("app:9", ["10", "10.0"], {"type": "numerical"})["selected_tag"] == "10.0"  # equal numbers: greater string
    assert image("app:abc", ["1"], {"type": "numerical"})["current_in_range"] is False
    assert severities(image("app:9", ["abc"], {"type": "numerical"}))["no-eligible-candidate"] == "major"
    assert image("app:9", ["9"], {"type": "numerical"})["update_available"] is False


def test_image_without_policy_or_candidates_selects_nothing():
    result = image("app:1.0.0", ["2.0.0"])
    assert (result["policy_applied"], result["selected_tag"], result["update_available"], result["eligible_count"]) == (False, "", False, 0)
    assert image("app:1.0.0", [], {"type": "alphabetical"})["findings"][0]["id"] == "tag-not-immutable"
    invalid("gitops_image_policy", {"image": "app:1", "candidate_tags": ["bad tag"], "require_digest": False}, "candidate_tags")
    invalid("gitops_image_policy", {"image": "app:1", "candidate_tags": [], "require_digest": False, "policy": {"type": "calver"}}, "policy")


# --- gitops_secret_scan ---------------------------------------------------------------------------------------


def scan(*manifests):
    return ok("gitops_secret_scan", {"manifests": list(manifests)})


def secret(name="s", **sections):
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": "app"}, **sections}


def deployment_with_env(*env):
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "d", "namespace": "app"},
            "spec": {"template": {"spec": {"containers": [{"name": "c", "image": "i", "env": list(env)}]}}}}


def test_secret_scan_plaintext_secrets():
    result = scan(secret(data={"password": "aHVudGVyMg==", "user": "YWRtaW4="}, stringData={"token": "abc"}))
    assert severities(result) == {"plaintext-secret:Secret/app/s": "blocker"} and (result["exposure_count"], result["reference_count"]) == (1, 0)
    assert result["findings"][0]["summary"] == "Secret/app/s commits 3 secret value(s) in clear (base64 is not encryption): data.password, data.user, stringData.token"
    assert scan(secret(data={}))["findings"] == [] and scan(secret())["findings"] == []
    assert scan(secret(stringData={"a": "", "b": "${PASSWORD}", "c": "<token>", "d": "{{ .Values.x }}", "e": "CHANGEME", "f": "$(cmd)"}))["findings"] == []
    assert scan({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s"}, "type": "kubernetes.io/tls", "data": {"tls.key": "LS0t"}})["verdict"] == "fail"


def test_secret_scan_sops_encrypted_secrets_are_references():
    sealed = scan(secret(sops={"version": "3.8.1"}, stringData={"a": "ENC[AES256_GCM,data:abc,tag:def]", "b": "ENC[AES256_GCM,data:x]"}))
    assert (sealed["findings"], sealed["reference_count"], sealed["exposure_count"]) == ([], 1, 0)
    partial = scan(secret(sops={"version": "3.8.1"}, stringData={"a": "ENC[AES256_GCM,data:abc]", "b": "clear"}))
    assert severities(partial) == {"sops-partially-encrypted:Secret/app/s": "blocker"} and partial["exposure_count"] == 1
    assert scan(secret(sops="not-a-block", stringData={"a": "ENC[x]"}))["verdict"] == "fail"


def test_secret_scan_references_are_not_exposures():
    sealed = {"apiVersion": "bitnami.com/v1alpha1", "kind": "SealedSecret", "metadata": {"name": "s"}, "spec": {"encryptedData": {"password": "AgBy3i4OJSWK+PiTySYZZA9rO43cGDEq"}}}
    external = {"apiVersion": "external-secrets.io/v1beta1", "kind": "ExternalSecret", "metadata": {"name": "e"},
                "spec": {"secretStoreRef": {"name": "vault"}, "data": [{"secretKey": "p", "remoteRef": {"key": "k"}}]}}
    provider = {"apiVersion": "secrets-store.csi.x-k8s.io/v1", "kind": "SecretProviderClass", "metadata": {"name": "p"}, "spec": {"provider": "vault"}}
    result = scan(sealed, external, provider)
    assert (result["findings"], result["reference_count"], result["exposure_count"], result["verdict"]) == ([], 3, 0, "pass")


def test_secret_scan_reference_problems():
    empty = {"apiVersion": "bitnami.com/v1alpha1", "kind": "SealedSecret", "metadata": {"name": "s"}, "spec": {"encryptedData": {}}}
    assert severities(scan(empty)) == {"empty-sealed-secret:SealedSecret/s": "major"}
    bare = {"apiVersion": "external-secrets.io/v1beta1", "kind": "ExternalSecret", "metadata": {"name": "e"}, "spec": {}}
    assert severities(scan(bare)) == {"external-secret-no-store:ExternalSecret/e": "major", "external-secret-empty:ExternalSecret/e": "minor"}
    dataFrom = {**bare, "spec": {"secretStoreRef": {"name": "v"}, "dataFrom": [{"extract": {"key": "k"}}]}}
    assert scan(dataFrom)["findings"] == []
    other_group = {"apiVersion": "example.com/v1", "kind": "SealedSecret", "metadata": {"name": "s"}, "spec": {}}
    assert scan(other_group)["reference_count"] == 0


@pytest.mark.parametrize("name,flagged", [("PASSWORD", True), ("db_passwd", True), ("API_KEY", True), ("apiKey", True), ("api-key", True), ("GITHUB_TOKEN", True),
                                          ("clientSecret", True), ("SECRET_KEY", True), ("PRIVATE_KEY", True), ("accessKey", True), ("DB_CREDENTIALS", True),
                                          ("SECRET_NAME", False), ("passwordFile", False), ("TOKEN_PATH", False), ("secretRef", False), ("TOKEN_URL", False),
                                          ("AWS_ACCESS_KEY_ID", False), ("existingSecret", False), ("PASSWORD_ENABLED", False), ("CLIENT_SECRET_ARN", False),
                                          ("SECRET_NAMESPACE", False), ("LOG_LEVEL", False), ("HOSTNAME", False)])
def test_secret_scan_env_names(name, flagged):
    result = scan(deployment_with_env({"name": name, "value": "s3cr3tvalue"}))
    expected = {"plaintext-env:Deployment/app/d:spec.template.spec.containers[0].env[0]": "major"} if flagged else {}
    assert severities(result) == expected and result["exposure_count"] == int(flagged)


def test_secret_scan_env_values_that_are_not_secrets():
    env = [{"name": "API_KEY", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}, {"name": "API_KEY", "value": ""}, {"name": "API_KEY", "value": "${KEY}"},
           {"name": "API_KEY", "value": "<key>"}, {"name": "API_KEY", "value": "REPLACE_ME"}, {"name": "API_KEY"}, {"name": "LOG", "value": "x"}]
    assert scan(deployment_with_env(*env))["findings"] == []


def test_secret_scan_configmap_keys():

    def cm(**data):
        return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c", "namespace": "app"}, "data": data}

    assert severities(scan(cm(db_password="correcthorse"))) == {"secret-in-configmap:ConfigMap/app/c:db_password": "major"}
    assert scan(cm(db_password="short"))["findings"] == []  # fewer than 8 characters: a policy name, not a secret
    assert scan(cm(db_password="exactly8"))["exposure_count"] == 1 and scan(cm(db_password="seven77"))["exposure_count"] == 0
    assert scan(cm(token="${TOKEN_VALUE}", secretName="my-secret-name", password_file="/etc/pw/file"))["findings"] == []
    assert scan({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}})["findings"] == []


@pytest.mark.parametrize("name,value", [
    ("aws-access-key-id", "AKIAIOSFODNN7EXAMPLE"), ("aws-access-key-id", "ASIAIOSFODNN7EXAMPLE"), ("private-key", "-----BEGIN PRIVATE KEY-----\nMIIE"),
    ("private-key", "-----BEGIN RSA PRIVATE KEY-----"), ("private-key", "-----BEGIN OPENSSH PRIVATE KEY-----"), ("private-key", "-----BEGIN EC PRIVATE KEY-----"),
    ("github-token", "ghp_" + "a" * 36), ("github-token", "gho_" + "B" * 40), ("github-token", "ghs_" + "1" * 36), ("github-token", "github_pat_" + "a" * 22),
    ("slack-token", "xoxb-1234567890-abcdef"), ("slack-token", "xoxp-1234567890-abcdef"), ("google-api-key", "AIza" + "a" * 35),
])
def test_secret_scan_credential_formats(name, value):
    manifest = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"note": f"see {value} here"}}
    result = scan(manifest)
    assert severities(result) == {f"secret-pattern:{name}:ConfigMap/c:data.note": "blocker"}
    assert value not in json.dumps(result)  # never echo the credential


@pytest.mark.parametrize("value", ["AKIA123", "ghp_short", "xoxb-1", "AIzaShort", "-----BEGIN CERTIFICATE-----", "-----BEGIN PUBLIC KEY-----", "AKIAIOSFODNN7EXAMPLEX1"])
def test_secret_scan_near_misses_are_not_credentials(value):
    assert scan({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"note": value}})["findings"] == []


def test_secret_scan_url_credentials_and_paths():
    app = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {"name": "a"},
           "spec": {"sources": [{"repoURL": "https://deploy:hunter2x@git.example.com/acme/x.git"}, {"repoURL": "https://git.example.com/acme/y.git"},
                                {"repoURL": "ssh://git@git.example.com/acme/z.git"}, {"repoURL": "https://u:${TOKEN}@git.example.com/w.git"}]}}
    result = scan(app)
    assert severities(result) == {"credentials-in-url:Application/a:spec.sources[0].repoURL": "blocker"}
    assert "hunter2x" not in json.dumps(result)
    nested = scan({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"a": {"b": ["x", {"c": "AKIAIOSFODNN7EXAMPLE"}]}}})
    assert list(severities(nested)) == ["secret-pattern:aws-access-key-id:ConfigMap/c:data.a.b[1].c"]


def test_secret_scan_counts_manifests_and_bounds_its_input():
    assert scan({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"k": "x"}})["manifest_count"] == 1
    assert scan(secret(), secret("t", data={"p": "aHVudGVyMg=="}))["exposure_count"] == 1
    invalid("gitops_secret_scan", {"manifests": [{"kind": "Secret"}]}, "manifests")
    invalid("gitops_secret_scan", {"manifests": [secret(data={"p": "x" * 70000})]}, "manifests")
    invalid("gitops_secret_scan", {"manifests": ["Secret"]}, "manifests")


# --- gitops_repo_layout ---------------------------------------------------------------------------------------


def layout(paths, expected=("staging", "prod"), production=("prod",), clusters=(), engine="none", codeowners=None):
    payload = {"paths": list(paths), "expected_envs": list(expected), "production_envs": list(production), "clusters": list(clusters), "engine": engine}
    if codeowners is not None:
        payload["codeowners"] = codeowners
    return ok("gitops_repo_layout", payload)


GOOD = ["apps/web/base/kustomization.yaml", "apps/web/overlays/staging/kustomization.yaml", "apps/web/overlays/prod/kustomization.yaml", ".github/CODEOWNERS"]


def test_layout_clean_repository():
    result = layout(GOOD, codeowners="apps/ @acme/platform\n")
    assert result == {"state": "SUCCESS", "path_count": 4, "envs_found": ["prod", "staging"], "missing_envs": [], "cluster_dirs": [], "codeowners_evaluated": True,
                      "verdict": "pass", "severity_counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "findings": []}


def test_layout_environment_detection_conventions():
    paths = ["overlays/staging/a.yaml", "environments/prod/a.yaml", "envs/qa/a.yaml", "env/dev/a.yaml", "values-eu.yaml", "chart/values-us.yml", "chart/values/asia.yaml",
             "docs/overlays/readme.md", "values-.yaml", "environments/loose.yaml", "CODEOWNERS"]
    result = layout(paths, expected=("staging", "prod", "qa", "dev", "eu", "us", "asia"), production=())
    assert result["envs_found"] == ["asia", "dev", "eu", "prod", "qa", "staging", "us"] and result["missing_envs"] == []
    assert severities(layout(["a/overlays/staging/x.yaml", "CODEOWNERS"], expected=("staging", "prod"), production=()))["env-missing:prod"] == "major"
    unexpected = layout(GOOD + ["apps/web/overlays/qa/kustomization.yaml"], codeowners="* @a\n")
    assert severities(unexpected) == {"env-unexpected:qa": "info"}
    assert layout(["overlays/staging"] + ["CODEOWNERS"], expected=("staging",), production=())["missing_envs"] == ["staging"]  # a file called staging is not an environment


def test_layout_overlay_structure():
    result = layout(["apps/api/overlays/staging/kustomization.yaml", "apps/api/overlays/prod/patch.yaml", "CODEOWNERS"], production=())
    assert severities(result) == {"overlay-without-base:apps/api": "minor", "overlay-no-kustomization:apps/api/overlays/prod": "minor"}
    assert severities(layout(["apps/api/base/x.yaml", "apps/api/overlays/staging/Kustomization", "apps/api/overlays/prod/kustomization.yml", "CODEOWNERS"],
                             production=())) == {}
    gap = layout(GOOD + ["apps/api/base/x.yaml", "apps/api/overlays/staging/kustomization.yaml"], production=(), codeowners="* @a\n")
    assert severities(gap) == {"app-env-gap:apps/api:prod": "minor"}
    root = layout(["base/k.yaml", "overlays/staging/kustomization.yaml", "CODEOWNERS"], production=())
    assert severities(root) == {"app-env-gap:.:prod": "minor", "env-missing:prod": "major"}
    assert severities(layout(["overlays/staging/kustomization.yaml", "CODEOWNERS"], expected=("staging",), production=())) == {"overlay-without-base:.": "minor"}


def test_layout_cluster_registration():
    paths = ["clusters/prod-1/flux-system/gotk-sync.yaml", "clusters/prod-1/flux-system/gotk-components.yaml", "clusters/stg-1/apps.yaml", "clusters/old/apps.yaml",
             "clusters/README.md", "CODEOWNERS"]
    flux = layout(paths, expected=("prod",), production=(), clusters=("prod-1", "stg-1", "gone"), engine="flux")
    assert severities(flux) == {"cluster-dir-missing:gone": "major", "flux-not-bootstrapped:stg-1:gotk-sync.yaml": "major",
                                "flux-not-bootstrapped:stg-1:gotk-components.yaml": "major", "cluster-dir-unregistered:old": "minor", "env-missing:prod": "major"}
    assert flux["cluster_dirs"] == ["old", "prod-1", "stg-1"]
    argo = layout(paths, expected=("prod",), production=(), clusters=("prod-1", "stg-1"), engine="argocd")
    assert severities(argo) == {"cluster-dir-unregistered:old": "minor", "env-missing:prod": "major"}
    none = layout(paths, expected=("prod",), production=(), clusters=("prod-1",), engine="none")
    assert "flux-not-bootstrapped:stg-1:gotk-sync.yaml" not in severities(none)


@pytest.mark.parametrize("name,major", [(".env", True), ("id_rsa", True), ("id_ed25519", True), ("id_ecdsa", True), ("kubeconfig", True), ("tls.key", True),
                                        ("cert.pfx", True), ("cert.p12", True), ("prod.env", True), ("ca.pem", False), ("id_rsa.pub", False), ("keys.md", False)])
def test_layout_private_key_files(name, major):
    result = layout(GOOD + [f"deploy/{name}"], codeowners="* @a\n")
    expected = {"secret-like-file:deploy/" + name: "major"} if major and name != "ca.pem" else {}
    if name == "ca.pem":
        expected = {"pem-file:deploy/ca.pem": "minor"}
    assert severities(result) == expected


def test_layout_codeowners_presence_and_content():
    assert severities(layout(GOOD[:3])) == {"codeowners-missing": "major"}
    for location in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS"):
        assert layout(GOOD[:3] + [location])["findings"] == []
    assert layout(GOOD[:3] + ["src/CODEOWNERS"])["findings"][0]["id"] == "codeowners-missing"
    supplied = layout(GOOD[:3], codeowners="apps/ @acme/platform\n")  # the text is evidence enough
    assert supplied["findings"] == [] and supplied["codeowners_evaluated"] is True
    not_evaluated = layout(GOOD)
    assert not_evaluated["codeowners_evaluated"] is False and not_evaluated["findings"] == []


def test_layout_codeowners_ownership_of_production_and_clusters():
    paths = GOOD + ["clusters/prod-1/apps.yaml"]
    unowned = layout(paths, clusters=("prod-1",), codeowners="/apps/web/overlays/staging/ @acme/stg\n")
    assert severities(unowned) == {"prod-env-unowned:prod": "major", "clusters-unowned": "minor"}
    assert "apps/web/overlays/prod/kustomization.yaml" in unowned["findings"][0]["summary"]
    reset = layout(paths, clusters=("prod-1",), codeowners="* @acme/all\n/apps/web/overlays/prod/\n/clusters/ @acme/sre\n")
    assert severities(reset) == {"prod-env-unowned:prod": "major"}  # a pattern without owners removes ownership
    last = layout(paths, clusters=("prod-1",), codeowners="/apps/web/overlays/prod/ @acme/prod\n* @acme/all\n/clusters/ @acme/sre\n")
    assert severities(last) == {}  # the later * wins and still names an owner
    email = layout(GOOD, codeowners="*.yaml dev@example.com\n")
    assert email["findings"] == []
    assert layout(GOOD, production=(), codeowners="")["findings"] == []
    assert severities(layout(GOOD, codeowners="# only a comment\n")) == {"prod-env-unowned:prod": "major"}
    sections = layout(GOOD, codeowners="[Platform]\n^[Optional][2]\napps/ @acme/p\n")
    assert sections["findings"] == []


def test_layout_codeowners_syntax_findings():
    result = layout(GOOD, production=(), codeowners="*.yaml bad-owner @ok/team @ @-bad a@b\n!ignored @x\ndocs/[ab].md @x\nsrc\\ files @x\n# c\n")
    assert severities(result) == {
        "invalid-owner:1:bad-owner": "minor", "invalid-owner:1:@": "minor", "invalid-owner:1:@-bad": "minor", "invalid-owner:1:a@b": "minor",
        "codeowners-pattern-unsupported:2": "minor", "codeowners-pattern-unsupported:3": "minor", "codeowners-pattern-unsupported:4": "minor"}
    many = layout(GOOD, production=(), codeowners="a/**/b/**/c/**/d @x\n")
    assert severities(many) == {"codeowners-pattern-unsupported:1": "minor"}
    assert layout(GOOD, production=(), codeowners="a/**/b/**/c @x\n  \n")["findings"] == []
    trailing = layout(GOOD, production=(), codeowners="docs/* @x # trailing comment\n")
    assert trailing["findings"] == []


@pytest.mark.parametrize("pattern,path,expected", [
    ("*.yaml", "apps/overlays/prod/x.yaml", True), ("*.json", "apps/overlays/prod/x.yaml", False), ("*.md", "apps/overlays/prod/x.yaml", False),
    ("*", "overlays/prod/x.yaml", True), ("x.y?ml", "apps/overlays/prod/x.yaml", True), ("x.y?ml", "apps/overlays/prod/x.yml", False),
    ("/overlays/prod/", "overlays/prod/x.yaml", True), ("/overlays/prod/", "apps/overlays/prod/x.yaml", False),
    ("overlays/prod/", "apps/overlays/prod/x.yaml", False),  # a slash inside the pattern anchors it to the root
    ("prod/", "apps/overlays/prod/x.yaml", True), ("prod", "apps/overlays/prod/x.yaml", True), ("ov", "apps/overlays/prod/x.yaml", False),
    ("pro", "apps/overlays/prod/x.yaml", False), ("apps/", "apps/overlays/prod/x.yaml", True), ("apps/", "other/apps/overlays/prod/x.yaml", True),
    ("/apps/", "other/apps/overlays/prod/x.yaml", False), ("/apps", "apps/overlays/prod/x.yaml", True),
    ("apps/*", "apps/overlays/prod/x.yaml", False),  # * does not cross directories
    ("apps/**", "apps/overlays/prod/x.yaml", True), ("apps/**/x.yaml", "apps/overlays/prod/x.yaml", True), ("apps/**/y.yaml", "apps/overlays/prod/x.yaml", False),
    ("**/prod/*.yaml", "apps/overlays/prod/x.yaml", True), ("**/prod/*.yaml", "apps/overlays/prod/sub/x.yaml", False),
    ("**/overlays/**", "apps/overlays/prod/x.yaml", True), ("/apps/*/prod/", "apps/overlays/prod/x.yaml", True), ("/apps/*/*/prod/", "apps/overlays/prod/x.yaml", False),
    ("/apps/*/overlays/", "apps/overlays/prod/x.yaml", False), ("apps/overlays/prod/x.yaml", "apps/overlays/prod/x.yaml", True),
    ("overlays/prod/x.yaml", "apps/overlays/prod/x.yaml", False), ("values-*.yaml", "values-prod.yaml", True), ("/values-prod.yaml", "values-prod.yaml", True),
    ("/values-prod.yaml", "chart/values-prod.yaml", False), ("values-prod.yaml", "chart/values-prod.yaml", True),
])
def test_layout_codeowners_pattern_semantics(pattern, path, expected):
    result = layout([path, "CODEOWNERS"], expected=("prod",), production=("prod",), codeowners=f"{pattern} @acme/owner\n")
    assert result["envs_found"] == ["prod"]
    assert ("prod-env-unowned:prod" not in severities(result)) is expected, (pattern, path)


def test_layout_rejects_bad_paths_and_names():
    for bad in ("/abs/path.yaml", "a//b.yaml", "a/../b.yaml", "./a.yaml", "a\\b.yaml", "a/./b"):
        invalid("gitops_repo_layout", {"paths": [bad], "expected_envs": ["prod"], "production_envs": [], "clusters": [], "engine": "none"}, "paths")
    invalid("gitops_repo_layout", {"paths": ["a"], "expected_envs": ["a/b"], "production_envs": [], "clusters": [], "engine": "none"}, "expected_envs")
    invalid("gitops_repo_layout", {"paths": ["a"], "expected_envs": ["prod"], "production_envs": [], "clusters": ["x/y"], "engine": "none"}, "expected_envs")
    invalid("gitops_repo_layout", {"paths": ["a"], "expected_envs": ["prod"], "production_envs": ["dev"], "clusters": [], "engine": "none"}, "production_envs")
    invalid("gitops_repo_layout", {"paths": ["a"], "expected_envs": ["prod"], "production_envs": [], "clusters": [], "engine": "svn"}, "engine")


# =====================================================================================================================
# Independent review regressions. Oracles: real Kustomize (sigs.k8s.io/kustomize/api v0.21.2, krusty build), Masterminds
# semver v3.5.0 (what Flux image policies use), Argo Rollouts v1.10.1 (utils/replicaset/canary.go) and the Argo CD / Flux docs.
# =====================================================================================================================

CANARIES = {
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "github": "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "hex64": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "password": "S3cr3tPassw0rd",
    "mnemonic": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
}
CANARY_URL = "https://svc:S3cr3tPassw0rd@git.example.com/acme/deploy.git"


def assert_no_canary(result, *extra):
    text = json.dumps(result, sort_keys=True)
    for value in [*CANARIES.values(), CANARY_URL, "svc:S3cr3tPassw0rd", *extra]:
        assert value not in text, value


def test_review_kustomize_namespace_override_renames_every_namespace_object():
    two = kcheck({"namespace": "x"}, [res("Namespace", "a", None, "v1"), res("Namespace", "b", None, "v1")])
    assert severities(two) == {"namespace-collision:Namespace/x": "blocker", "namespace-renamed:a": "info", "namespace-renamed:b": "info"}
    one = kcheck({"namespace": "x"}, [res("Namespace", "y", None, "v1"), res("ConfigMap", "c", None, "v1")])
    assert severities(one) == {"namespace-renamed:y": "info"} and one["verdict"] == "pass"
    assert kcheck({"namespace": "x"}, [res("Namespace", "x", None, "v1"), res("ConfigMap", "c", "x", "v1")])["findings"] == []


def test_review_kustomize_derived_targets_match_group_version_and_namespace_exactly():
    merge = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop"}, "spec": {"replicas": 3}}
    deployment = [res("Deployment", "web", "shop")]
    assert kcheck({"patches": [{"patch": merge}]}, deployment)["findings"] == []
    not_found = {"patch-target-not-found:patches[0]": "major"}
    assert severities(kcheck({"patches": [{"patch": {**merge, "metadata": {"name": "web"}}}]}, deployment)) == not_found  # no namespace
    assert severities(kcheck({"patches": [{"patch": merge}]}, [res("Deployment", "web")])) == not_found  # the object has none
    for api in ("apps/v1beta2", "extensions/v1", None):
        patch = {key: value for key, value in {**merge, "apiVersion": api}.items() if value is not None}
        assert severities(kcheck({"patches": [{"patch": patch}]}, deployment)) == not_found, api
    explicit = {"target": {"kind": "Deployment", "name": "web"}, "patch": {"metadata": {"name": "other"}}}
    assert kcheck({"patches": [explicit]}, deployment)["findings"] == []  # an explicit target decides, not the patch body


def test_review_kustomize_two_api_versions_of_one_object_are_a_conflict_not_a_kustomize_error():
    versions = kcheck({}, [res("Deployment", "web", "shop"), res("Deployment", "web", "shop", "apps/v1beta1")])
    assert severities(versions) == {"duplicate-object-versions:apps/Deployment/shop/web": "major"} and versions["verdict"] == "fail"


@pytest.mark.parametrize("old,new", [("imageTags", "images"), ("helmChartInflationGenerator", "helmCharts")])
def test_review_kustomize_more_deprecated_fields(old, new):
    result = kcheck({old: []}, [])
    assert severities(result) == {f"deprecated-field:{old}": "minor"} and f"in favour of {new}" in result["findings"][0]["summary"]


def test_review_kustomize_vars_are_only_experimentally_migrated_by_edit_fix():
    assert "experimental" in kcheck({"vars": []}, [])["findings"][0]["summary"]


@pytest.mark.parametrize("kind", [
    "ComponentStatus", "FlowSchema", "PriorityLevelConfiguration", "PodSecurityPolicy", "ClusterTrustBundle", "IPAddress", "ServiceCIDR",
    "VolumeAttributesClass", "SelfSubjectReview", "TokenReview", "SelfSubjectAccessReview", "SelfSubjectRulesReview", "SubjectAccessReview",
    "MutatingAdmissionPolicy", "MutatingAdmissionPolicyBinding", "DeviceClass", "ResourceSlice", "StorageVersionMigration"])
def test_review_kustomize_more_cluster_scoped_kinds(kind):
    result = kcheck({}, [res(kind, "x", "a", "v1"), res(kind, "x", "b", "v1")])
    assert severities(result) == {f"duplicate-resource:{kind}/x": "blocker"}


def test_review_helm_drift_never_echoes_values_and_names_the_groups():
    staging = {"replicas": 2, "debug": True, "db": {"password": CANARIES["password"], "host": "db-a"}, "auth": {"token_ttl": 3600}}
    prod = {"replicas": 4, "debug": False, "db": {"password": CANARIES["aws"], "host": "db-b"}, "auth": {"token_ttl": 60}}
    qa = {"replicas": 2, "debug": True, "db": {"password": CANARIES["password"], "host": "db-a"}}
    result = hdrift([("staging", staging), ("prod", prod), ("qa", qa)], invariant=["db", "debug", "replicas", "auth"])
    summaries = {f["id"]: f["summary"] for f in result["findings"] if f["id"].startswith("invariant-key-differs:")}
    assert summaries == {
        "invariant-key-differs:auth.token_ttl": "auth.token_ttl must be identical in every environment but differs: staging: <number #1>; prod: <number #2>; qa: <unset>",
        "invariant-key-differs:db.host": "db.host must be identical in every environment but differs: staging, qa: <string #1>; prod: <string #2>",
        "invariant-key-differs:db.password": "db.password must be identical in every environment but differs: staging, qa: <string #1>; prod: <string #2>",
        "invariant-key-differs:debug": "debug must be identical in every environment but differs: staging, qa: true; prod: false",
        "invariant-key-differs:replicas": "replicas must be identical in every environment but differs: staging, qa: 2; prod: 4",
    }
    assert_no_canary(result)


def test_review_argo_automated_enabled_false_is_not_auto_sync():
    disabled = sync_policy(argo_app(syncPolicy={"automated": {"enabled": False, "prune": True, "selfHeal": True}}), "prod")
    assert (disabled["auto_sync"], disabled["prune_enabled"], disabled["findings"]) == (False, False, [])
    for enabled in (True, None):
        on = sync_policy(argo_app(syncPolicy={"automated": {"enabled": enabled, "prune": True}}), "prod")
        assert (on["auto_sync"], on["prune_enabled"]) == (True, True) and "prod-auto-prune" in severities(on)


def test_review_flux_external_artifact_source_and_chart_exclusivity():
    external = flux_kustomization(sourceRef={"kind": "ExternalArtifact", "name": "x"})
    assert sync_policy(external)["findings"] == []
    both = flux_helm_release(chartRef={"kind": "OCIRepository", "name": "o"})
    assert severities(sync_policy(both)) == {"chart-and-chartref": "blocker"}


def test_review_appset_go_template_nested_list_elements_are_known_keys():
    generator = {"list": {"elements": [{"cluster": {"name": "a", "url": "https://a"}, "env": "stg"}]}}
    template = appset_template(name="web-{{ .cluster.name }}", destination={"server": "{{ .cluster.url }}", "namespace": "shop"})
    options = {"goTemplate": True, "goTemplateOptions": ["missingkey=error"]}
    assert check_appset(appset([generator], template, **options))["findings"] == []
    typo = appset_template(name="web-{{ .cluster.nope }}", destination={"server": "{{ .cluster.url }}", "namespace": "shop"})
    assert severities(check_appset(appset([generator], typo, **options))) == {"unknown-template-key:cluster.nope": "major"}


def test_review_appset_rolling_sync_forces_generated_applications_to_manual_sync():
    template = appset_template(syncPolicy={"automated": {"prune": True}})
    template["metadata"]["labels"] = {"env": "x"}
    steps = {"type": "RollingSync", "rollingSync": {"steps": [{"matchExpressions": [{"key": "env", "operator": "In", "values": ["x"]}]}]}}
    document = appset([{"list": {"elements": [{"name": "a", "server": "s"}]}}], template, strategy=steps)
    assert severities(check_appset(document, environment="prod")) == {"template-auto-sync-ignored-by-rolling-sync": "minor"}


def test_review_waves_predelete_hook_is_a_documented_type():
    result = waves([obj("Job", "cleanup", "batch/v1", annotations={HOOK: "PreDelete"})])
    assert result["findings"] == [] and [p["phase"] for p in result["plan"]] == ["PreDelete"]


def test_review_waves_non_string_and_out_of_range_waves_are_reported():
    number = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c", "annotations": {"argocd.argoproj.io/sync-wave": 5}}}
    assert severities(waves([number])) == {"wave-invalid:ConfigMap/c": "major"}  # the API server refuses a non-string annotation
    huge = obj("ConfigMap", "c", annotations={"argocd.argoproj.io/sync-wave": "9223372036854775808"})
    assert severities(waves([huge])) == {"wave-invalid:ConfigMap/app/c": "major"}  # Atoi overflow: Argo CD uses 0
    edge = obj("ConfigMap", "c", annotations={"argocd.argoproj.io/sync-wave": "9223372036854775807"})
    assert waves([edge])["findings"] == []


def test_review_prune_flux_prune_disabled_protects_by_annotation_or_label():
    for key in ("annotations", "labels"):
        pvc = obj("PersistentVolumeClaim", "data")
        pvc["metadata"][key] = {"kustomize.toolkit.fluxcd.io/prune": "disabled"}
        result = prune([pvc], environment="prod")
        assert severities(result) == {"protected:PersistentVolumeClaim/app/data": "info"}
        assert (result["protected_count"], result["unprotected_count"], result["autonomy_required"]) == (1, 0, "L3")


def test_review_prune_argo_applications_cascade():
    app = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {"name": "child", "namespace": "argocd"}}
    cascading = {**app, "metadata": {**app["metadata"], "finalizers": ["resources-finalizer.argocd.argoproj.io"]}}
    assert severities(prune([cascading], environment="prod")) == {"prune-application:Application/argocd/child": "blocker"}
    assert severities(prune([cascading])) == {"prune-application:Application/argocd/child": "major"}
    assert severities(prune([app], environment="prod")) == {"prune-application:Application/argocd/child": "minor"}
    appset_doc = {"apiVersion": "argoproj.io/v1alpha1", "kind": "ApplicationSet", "metadata": {"name": "fleet", "namespace": "argocd"}}
    assert severities(prune([appset_doc], environment="prod")) == {"prune-applicationset:ApplicationSet/argocd/fleet": "blocker"}
    keep = {**appset_doc, "spec": {"syncPolicy": {"preserveResourcesOnDeletion": True}}}
    assert severities(prune([keep], environment="prod")) == {"prune-applicationset:ApplicationSet/argocd/fleet": "minor"}


# (replicas, setWeight, maxSurge or None for the 25% default, whole-percent exposure) from Argo Rollouts' own
# approximateWeightedCanaryStableReplicaCounts: the surge option is part of the choice, and 5 replicas / 90% / maxSurge 0
# falls through to "no option" (0 canary pods), exactly as upstream does.
ROLLOUT_WEIGHTS = [
    (1,1,0,0), (1,1,None,50), (1,10,1,50), (1,25,0,0), (1,25,None,50), (1,33,1,50), (1,50,0,100),
    (1,50,None,50), (1,67,1,50), (1,90,0,100), (1,90,None,50), (1,99,1,50), (2,1,0,50), (2,1,None,33),
    (2,10,1,33), (2,25,0,50), (2,25,None,33), (2,33,1,33), (2,50,0,50), (2,50,None,50), (2,67,1,67),
    (2,90,0,50), (2,90,None,67), (2,99,1,67), (3,1,0,33), (3,1,None,25), (3,10,1,25), (3,25,0,33),
    (3,25,None,25), (3,33,1,33), (3,50,0,67), (3,50,None,50), (3,67,1,67), (3,90,0,67), (3,90,None,75),
    (3,99,1,75), (4,1,0,25), (4,1,None,20), (4,10,1,20), (4,25,0,25), (4,25,None,25), (4,33,1,40),
    (4,50,0,50), (4,50,None,50), (4,67,1,60), (4,90,0,75), (4,90,None,80), (4,99,1,80), (5,1,0,20),
    (5,1,None,17), (5,10,1,17), (5,25,0,20), (5,25,None,20), (5,33,1,33), (5,50,0,60), (5,50,None,50),
    (5,67,1,67), (5,90,0,0), (5,90,None,83), (5,99,1,83), (7,1,0,14), (7,1,None,13), (7,10,1,13),
    (7,25,0,29), (7,25,None,25), (7,33,1,29), (7,50,0,57), (7,50,None,50), (7,67,1,71), (7,90,0,86),
    (7,90,None,88), (7,99,1,88), (10,1,0,10), (10,1,None,9), (10,10,1,10), (10,25,0,30), (10,25,None,27),
    (10,33,1,30), (10,50,0,50), (10,50,None,50), (10,67,1,70), (10,90,0,90), (10,90,None,90), (10,99,1,91),
]


@pytest.mark.parametrize("replicas,weight,surge,expected", ROLLOUT_WEIGHTS)
def test_review_rollout_basic_canary_weights_follow_argo_rollouts(replicas, weight, surge, expected):
    canary = {"steps": [{"setWeight": weight}, ANALYSIS_STEP]}
    if surge is not None:
        canary["maxSurge"] = surge
    result = check_rollout(rollout(canary, replicas=replicas), max_first_step_weight=100)
    assert result["effective_weights"] == [expected]


def test_review_rollout_one_replica_with_the_default_surge_exposes_half_the_pods():
    result = check_rollout(rollout({"steps": [{"setWeight": 10}, ANALYSIS_STEP]}, replicas=1), max_first_step_weight=100)
    assert result["effective_weights"] == [50] and severities(result) == {"weight-granularity:0": "minor"}
    assert "asks for 10% but 1 replica(s) without traffic routing expose 50%" in result["findings"][0]["summary"]
    assert "maxSurge" in result["findings"][0]["summary"]
    pinned = check_rollout(rollout({"steps": [{"setWeight": 10}, ANALYSIS_STEP], "maxSurge": 0}, replicas=1), max_first_step_weight=100)
    assert pinned["effective_weights"] == [0]


def test_review_rollout_surge_percentages_round_up_and_invalid_surge_counts_as_zero():
    def weights(surge):
        return check_rollout(rollout({"steps": [{"setWeight": 50}, ANALYSIS_STEP], "maxSurge": surge}, replicas=3), max_first_step_weight=100)["effective_weights"]

    assert weights("34%") == [50] and weights(1) == [50]  # 34% of 3 pods rounds up to one surge pod
    assert weights("33%") == [50] and weights(0) == [67] and weights("0%") == [67]


@pytest.mark.parametrize("constraint,version,expected", [
    ('~0.0.0', '3.4.5', True), ('~v0.0.0', '3.2', True), ('>*', '0.0.1', True),
    ('>*', '0.0.0', False), ('*', '1.0.0-rc.1', False), ('>=1.0.0-0 <2.0.0', '1.5.0-rc.1', True),
    ('>=1.0.0 <2.0.0', '1.5.0-rc.1', False), ('>=1.0.0-0 <2.0.0', '2.0.0-rc.1', True), ('<=1.x', '1.9.9', True),
    ('<=1.x', '2.0.0', False), ('^0.0', '0.0.9', True), ('^0.0', '0.1.0', False),
    ('^0.2.3', '0.2.9', True), ('^0.2.3', '0.3.0', False), ('^0.0.3', '0.0.4', False),
    ('=>1.2.3', '1.2.3', True), ('=<1.2.3', '1.2.4', False), ('!=1.2.x', '1.2.9', False),
    ('!=1.2.x', '1.3.0', True), ('1.2 - 1.4.5', '1.4.9', False), ('1.2 - 1.4', '1.4.9', True),
    ('^1.2.3-rc.1', '1.2.3-rc.2', True), ('~1.2.3', '1.3.0', False), ('~1', '1.9.9', True),
    ('1.x', '1.5.5', True), ('1.x', '2.0.0', False), ('01.2.3', '1.2.3', True),
    ('=<2.2.1+b1, ^v10', '2.2.1', False), ('>=1 !=1+b1 || =>2.2', '1.1.10', False), ('<v0.10.3, v*-rc.10', '0.2', True),
    ('~3.3.0 =<10.1.10+b1', '0', False), ('<v3.10.3 ~>x', '2-beta.2', False), ('0.1.1 - 3.2', '3', True),
    ('1.2 - 10.2.1 || <=0', 'v2', True), ('^0', '3.3.1', False), ('<10.3.3, !=10.3.2 || ~0.1.10 1.0.2-alpha.beta', '2.3.10', True),
    ('^3.0.10 >1', '10.3.1', False), ('1.1.3', '10.2.3', False), ('=1.x', '3.1.10', False),
    ('> 1-1, <=10.3.10', 'v3.1.0', True), ('>=0.0.10-0', '2.3.1', True), ('=<1.* ~2.3.0', '2.2.10', False),
    ('=<3.2.0', '2.2.0', True), ('>=0.2 || 1.3.3 - 10.0.1', '1.1', True), ('<=10.10 || <=v*', '3.1', True),
    ('=<10 || ~v1, >=1.10.3', '0.3.2', True), ('=3.2.2', '3.1.0', False), ('<2.10.0', 'v2.0.0-0', False),
    ('~>v2-alpha.beta =<2.10 || ~>3.0.3 ~ 2.0.3+b1', '0.0.3', False), ('>=1.* || => 2.10.10, =v0.10.0', '2.10.0', True),
    ('3.10.0-rc.1 - 0.10.10', '2.0.1', False), ('!=1.2', '3.2.2', True), ('>=*-alpha', 'v2.10.2', True),
    ('<=10.2.10-rc.10', '2.3-beta.2', True), ('=>2.3.3', '0.3.3', False), ('<10.x', '2.2.10', True),
    ('^3.0+b1 2.10.0-alpha.beta || ^x-alpha', '1.0.0', False), ('>=0, <=10-alpha+b1 || 0.0', '3.10.1', True),
    ('!=10.3.0-rc.1, = 2.0.2 || >=2.10.0 < x', '2.1.1', False), ('<= 1', '0.10.0', True), ('=10.1.2', '2.1.0', False),
    ('=10, > 1-rc.1', 'v10.1.0', True), ('v0 - v2', '1.0.3', True), ('=3.3.1', 'v1.3.1', False),
    ('>3.1.0, ~>x', 'v10.0.3-0', False), ('=<0.0.2-rc.10 || <3.1.10', '2.10.0', True), ('0.0.2 - x || ~>0', '3.0.1', False),
    ('<2.1.3-1', '0.3.3', True), ('~>10.10 || =3', '10.10.2', True), ('<0.3.1', '0.0.10', True),
    ('<2-rc.10', '10.10.1-beta.2', False), ('<v3, 0.3.3-alpha.beta', '10.0.2', False), ('=>2.0', '10.1.0', True),
    ('^10.10.2, => x || !=0.2.3 >=3.2', '1.3.3', False), ('2+b1', '1.10', False), ('<=v1.2.0-rc.1, <=10', 'v1', True),
    ('>=1.3.2-0, >=10.2.10-1 || <10.3.1', '1.3.3', True), ('>=3.0.1', '1.10.1-rc.10', False), ('1', '3.2.1-rc.10', False),
    ('>=0.1', '10.3', True), ('!=1.10.10-alpha || =< 1.1.10 = 0.10.10+b1', '10.1.3', True), ('=>v2.2 || <=v2.0.3', '1.1', True),
    ('=< 3.0.2 => 1.1', '2.3.1', True), ('!=* || >=1.0.3-alpha', 'v2.3.3', True), ('~>10.10 <=3.2', '10.2', False),
    ('^v1.1', 'v10.0.0', False), ('!=0-1', 'v10.3', True), ('10.0.1 - 2-rc.10', '3.2.1-rc.10', False),
    ('~>2.0 || =v0.2.1, > 0.1.0', 'v1.10.10', False), ('=x >=3.2.2', '2.2.0', False), ('<3.10.3', '3.2', True),
    ('2.1.3 - 2.3.0', 'v0', False), ('!= v0.1.1', 'v1.1', True), ('>=10.0.10', '3.2.1', False),
    ('~>1.10', '1.2.0', False), ('~>1.3.3 ^ v3.1-rc.10', '10.0.0-rc.10', False), ('>3.1.1, >=1.0.2', '1.3', False),
])
def test_review_semver_ranges_match_masterminds_v3_5_0(constraint, version, expected):
    result = image("r.io/a/web:1.0.0", [version], {"type": "semver", "range": constraint})
    assert result["eligible_count"] == int(expected) and result["selected_tag"] == (version if expected else "")


@pytest.mark.parametrize("constraint", ["", "   ", "foo", ">=", "1.2.3.4", "^^1", ">= 1.0 ||", "~>", ">=1.0 <", ",1.0", "1.0,,2.0", "1.0 - ", "- 1.0"])
def test_review_semver_rejects_what_masterminds_rejects(constraint):
    invalid("gitops_image_policy", {"image": "r.io/a:1", "candidate_tags": ["1.0.0"], "policy": {"type": "semver", "range": constraint}, "require_digest": False}, "policy")


def test_review_semver_constraint_parsing_is_linear_on_adversarial_whitespace():
    import time
    started = time.perf_counter()
    invalid("gitops_image_policy", {"image": "r.io/a:1", "candidate_tags": ["1.0.0"], "policy": {"type": "semver", "range": ">=1.0" + " " * 190 + "!"}, "require_digest": False}, "policy")
    assert time.perf_counter() - started < 1.0


def test_review_semver_long_version_strings_and_big_numbers():
    big = "1" + "0" * 30
    assert image("r.io/a:1", [big + ".0.0", "1.0.0"], {"type": "semver", "range": "*"})["selected_tag"] == "1.0.0"  # a number above uint64 is not a version
    assert image("r.io/a:1", ["1.0.0-01", "1.0.0-1"], {"type": "semver", "range": ">=1.0.0-0"})["selected_tag"] == "1.0.0-1"  # leading zero in a prerelease


def test_review_canary_baseline_regression_widens_in_the_worse_direction_for_negative_baselines():
    lower = metric(max_regression_pct=10, samples=[-9, -9, -9], baseline_samples=[-10, -10, -10])
    assert verdict(lower)["metrics"][0]["limit"] == -9.0 and verdict(lower)["verdict"] == "Successful"
    assert verdict(metric(max_regression_pct=10, samples=[-8.99] * 3, baseline_samples=[-10] * 3))["verdict"] == "Failed"
    higher = metric(direction="higher-is-better", max_regression_pct=10, samples=[-11, -11, -11], baseline_samples=[-10] * 3)
    assert verdict(higher)["metrics"][0]["limit"] == -11.0 and verdict(higher)["verdict"] == "Successful"
    assert verdict(metric(direction="higher-is-better", max_regression_pct=10, samples=[-11.01] * 3, baseline_samples=[-10] * 3))["verdict"] == "Failed"


def test_review_canary_baseline_with_too_few_samples_cannot_promote():
    thin = verdict(metric(max_regression_pct=10, samples=[100, 100, 100], baseline_samples=[100]), min_samples=3)
    assert (thin["verdict"], thin["action"], thin["metrics"][0]["status"]) == ("Inconclusive", "pause", "Inconclusive")
    enough = verdict(metric(max_regression_pct=10, samples=[100, 100, 100], baseline_samples=[100, 100, 100]), min_samples=3)
    assert enough["verdict"] == "Successful"
    assert verdict(metric(max_regression_pct=10, samples=[200, 200, 200], baseline_samples=[100]), min_samples=3)["verdict"] == "Failed"
    both = verdict(metric(threshold=500, max_regression_pct=10, samples=[100, 100, 100], baseline_samples=[100, 100]), min_samples=3)
    assert both["verdict"] == "Inconclusive"
    only_threshold = verdict(metric(threshold=500, samples=[100, 100, 100], baseline_samples=[100]), min_samples=3)
    assert only_threshold["verdict"] == "Successful"  # an unused baseline never matters


def test_review_canary_output_keeps_small_magnitudes():
    result = verdict(metric(threshold=1.5e-7, samples=[5e-8, 5e-8, 5e-8]))
    assert result["metrics"] == [{"name": "m", "status": "Successful", "samples": 3, "aggregate": 5e-8, "limit": 1.5e-7, "sample_failures": 0}]


def test_review_canary_rejects_non_finite_numbers():
    for bad in (float("nan"), float("inf"), float("-inf")):
        for built in (metric(threshold=bad), metric(threshold=1, samples=[1, bad, 1]), metric(max_regression_pct=10, baseline_samples=[1, bad, 1]),
                      metric(max_regression_pct=bad, baseline_samples=[1, 1, 1])):
            result = call("gitops_canary_verdict", {"metrics": [built], "min_samples": 1})
            assert result["state"] == "INVALID_INPUT", (bad, result)


def test_review_promotion_comment_after_approval_does_not_revoke_it():
    approvals = [approval("alice", at="2026-03-10T09:00:00+00:00"), approval("alice", "commented", at="2026-03-10T10:00:00+00:00")]
    result = gate(approvals=approvals)
    assert (result["verdict"], result["valid_approval_count"]) == ("ready", 1)
    assert gate(approvals=[*approvals, approval("alice", "dismissed", at="2026-03-10T11:00:00+00:00")])["valid_approval_count"] == 0
    assert gate(approvals=[*approvals, approval("alice", "changes_requested", at="2026-03-10T11:00:00+00:00")])["verdict"] == "blocked"
    assert gate(approvals=[approval("alice", "commented")])["valid_approval_count"] == 0


def test_review_promotion_abbreviated_and_upper_case_shas_identify_the_head_commit():
    full = "abcdef1234567890abcdef1234567890abcdef12"
    for sha in (full[:7], full[:12], full.upper(), full):
        assert gate(head_sha=full, approvals=[approval("alice", sha=sha)])["valid_approval_count"] == 1, sha
    assert gate(head_sha=full[:7], approvals=[approval("alice", sha=full)])["valid_approval_count"] == 1
    stale = gate(head_sha=full, approvals=[approval("alice", sha="abcdef0")])
    assert stale["valid_approval_count"] == 0 and severities(stale)["stale-approval:alice"] == "info"
    assert gate(head_sha=full, approvals=[approval("alice", sha="abcdef1xyz")])["valid_approval_count"] == 0


def test_review_secret_scan_placeholders_must_be_the_whole_value():
    def flagged(value):
        result = scan(deployment_with_env({"name": "API_TOKEN", "value": value}))
        return any(f["id"].startswith("plaintext-env") for f in result["findings"])

    for value in ("hunter2${x}", "pa$(ss)word", "{{ .Values.a }}secret", "Bearer ${T}"):
        assert flagged(value), value
    for value in ("${TOKEN_FROM_CI}", "$(TOKEN)", "{{ .Values.token }}", "<token>", "${A}:${B}", "", "CHANGEME", "  "):
        assert not flagged(value), value


@pytest.mark.parametrize("name", ["DB_PWD", "SSH_PASSPHRASE", "db-pwd"])
def test_review_secret_scan_more_secret_words(name):
    result = scan(deployment_with_env({"name": name, "value": "hunter2hunter2"}))
    assert [f["id"].split(":")[0] for f in result["findings"]] == ["plaintext-env"]


def test_review_secret_scan_looks_inside_list_kinds():
    configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}}
    listing = {"apiVersion": "v1", "kind": "List", "metadata": {}, "items": [secret("s", stringData={"password": CANARIES["password"]}), configmap]}
    result = scan(listing)
    assert severities(result) == {"plaintext-secret:Secret/app/s": "blocker"} and result["manifest_count"] == 2
    assert_no_canary(result)


def test_review_layout_every_production_path_needs_an_owner():
    paths = [".github/CODEOWNERS", "apps/web/base/kustomization.yaml", "apps/web/overlays/staging/kustomization.yaml", "apps/web/overlays/prod/kustomization.yaml",
             "apps/api/base/kustomization.yaml", "apps/api/overlays/staging/kustomization.yaml", "apps/api/overlays/prod/kustomization.yaml"]
    partial = layout(paths, codeowners="/apps/api/ @acme/api\n")
    assert severities(partial) == {"prod-env-unowned:prod": "major"}
    assert "1 of 2" in partial["findings"][0]["summary"] and "apps/web/overlays/prod/kustomization.yaml" in partial["findings"][0]["summary"]
    assert layout(paths, codeowners="/apps/ @acme/platform\n")["findings"] == []
    last_wins = layout(paths, codeowners="/apps/ @acme/platform\n/apps/web/overlays/prod/\n")
    assert severities(last_wins) == {"prod-env-unowned:prod": "major"}  # a later pattern without owners un-owns the path


def test_review_layout_every_cluster_path_needs_an_owner():
    paths = [*GOOD, "clusters/a/flux-system/gotk-sync.yaml", "clusters/b/apps.yaml"]
    result = layout(paths, clusters=["a", "b"], codeowners="apps/ @acme/platform\n/clusters/a/ @acme/platform\n")
    assert severities(result) == {"clusters-unowned": "minor"} and "clusters/b/apps.yaml" in result["findings"][0]["summary"]
    assert layout(paths, clusters=["a", "b"], codeowners="apps/ @acme/platform\n/clusters/ @acme/platform\n")["findings"] == []


def _canary_payloads():
    pw, aws, gh, hx, mn = (CANARIES[k] for k in ("password", "aws", "github", "hex64", "mnemonic"))
    web = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "web", "namespace": "shop", "annotations": {
        "argocd.argoproj.io/sync-wave": hx, "argocd.argoproj.io/hook": aws, "argocd.argoproj.io/hook-delete-policy": gh}},
        "spec": {"template": {"spec": {"containers": [{"name": "c", "image": f"r.io/app:{gh}", "env": [
            {"name": "API_TOKEN", "value": gh}, {"name": "DB_URL", "value": CANARY_URL}, {"name": "AWS_KEY", "value": aws}, {"name": "DB_PASSWORD", "value": pw}]}]}}}}
    keyed = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s", "namespace": "shop", "annotations": {"note": CANARY_URL, "argocd.argoproj.io/sync-options": "Prune=false," + gh}},
             "stringData": {"password": pw, "key": aws, "pem": "-----BEGIN RSA PRIVATE KEY-----\n" + hx, "words": mn}, "data": {"token": gh}}
    return {
        "gitops_kustomize_check": [{"kustomization": {"namePrefix": aws, "patches": [{"target": {"kind": "Deployment", "name": "web"}, "patch": [
            {"op": "bogus", "path": "/x", "value": gh}, {"op": "replace", "path": "/spec/x", "value": aws}]}], "secretGenerator": [{"name": "s", "literals": [f"password={pw}"]}]},
            "resources": [web, res("ConfigMap", "c", "shop", "v1")]}],
        "gitops_helm_values_drift": [{"environments": [
            {"name": "staging", "values": {"db": {"password": pw, "token": gh, "dsn": CANARY_URL}, "words": mn, "key": aws, "hex": hx}},
            {"name": "prod", "values": {"db": {"password": aws, "token": hx, "dsn": "postgres://u:p@h/db"}, "words": pw, "key": gh, "hex": mn}}],
            "required_keys": ["db.password", "missing"], "invariant_keys": ["db", "words", "key", "hex"]}],
        "gitops_app_sync_policy": [
            {"resource": argo_app(syncPolicy={"syncOptions": [aws, f"{aws}=true", "Prune=" + gh, "Foo=" + CANARY_URL]},
                                  source_extra={"repoURL": CANARY_URL, "targetRevision": gh}), "environment": "prod"},
            {"resource": flux_kustomization(interval=hx, decryption={"provider": aws}, sourceRef={"kind": gh, "name": "x"}), "environment": "prod"},
            {"resource": flux_helm_release(interval=mn, chart={"spec": {"chart": "c", "version": gh}}), "environment": "prod"}],
        "gitops_appset_check": [{"applicationset": appset([{"list": {"elements": [{"name": "a", "server": CANARY_URL, "token": gh, "words": mn}]}}],
                                                           appset_template(name="web-{{name}}-{{" + aws + "}}"), strategy={"type": gh}, goTemplateOptions=[aws],
                                                           syncPolicy={"applicationsSync": hx}), "clusters": CLUSTERS, "environment": "prod"}],
        "gitops_sync_wave_check": [{"manifests": [web, keyed]}],
        "gitops_prune_risk": [{"pruned": [web, keyed], "environment": "prod", "automated": True, "desired_count": 0, "allow_empty": True}],
        "gitops_rollout_check": [{"rollout": rollout({"steps": [{"setWeight": gh}, {"pause": {"duration": aws}}, {"setWeight": 10}], "maxSurge": hx, "maxUnavailable": mn,
                                                       "unknownKey": aws}, replicas=gh), "analysis_templates": [aws]},
                                 {"rollout": rollout(blue_green={"activeService": aws, "previewService": aws, "scaleDownDelaySeconds": gh}), "analysis_templates": []}],
        "gitops_canary_verdict": [{"metrics": [metric(threshold=1, samples=[1, 2, 3])], "min_samples": 3}],
        "gitops_promotion_gate": [{"now": "2026-03-10T12:00:00+00:00", "env_order": ["dev", "prod"], "source_env": "dev", "target_env": "prod",
                                   "deployed_at": "2026-03-10T11:00:00+00:00", "min_soak_minutes": 120, "checks": [{"name": "e2e", "status": "failure"}],
                                   "required_checks": ["e2e"], "approvals": [{"user": "alice", "state": "approved", "commit_sha": hx[::-1], "at": "2026-03-10T09:00:00+00:00"}],
                                   "required_approvals": 1, "pr_author": "bob", "head_sha": hx, "target_is_production": True}],
        "gitops_drift_classify": [{"diffs": [{"kind": "Secret", "name": "s", "path": "data.password", "change": "changed", "field_manager": "kubectl-edit"}], "ignore_differences": []}],
        "gitops_image_policy": [{"image": CANARY_URL, "candidate_tags": [], "require_digest": False},
                                {"image": f"r.io/app:{gh}", "candidate_tags": [f"{aws}:x", "1.0.0"], "require_digest": True},
                                {"image": f"r.io/{aws}", "candidate_tags": [gh], "policy": {"type": "alphabetical"}, "require_digest": False}],
        "gitops_secret_scan": [{"manifests": [web, keyed, {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"api_token": gh, "dsn": CANARY_URL, "password": pw}}]}],
        "gitops_repo_layout": [{"paths": [f"secrets/{aws}.key", f"apps/{gh}/overlays/prod/kustomization.yaml", ".github/CODEOWNERS"], "expected_envs": ["prod"], "production_envs": ["prod"],
                                "clusters": [], "engine": "none", "codeowners": f"/apps/ {gh}\n{aws} @team\n/secrets/ {CANARY_URL}\n"}],
    }


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_review_no_tool_echoes_an_input_value_that_could_be_a_secret(name):
    payloads = _canary_payloads()[name]
    for payload in payloads:
        result = call(name, copy.deepcopy(payload))
        assert result["state"] in ("SUCCESS", "INVALID_INPUT"), result
        assert_no_canary(result)
    assert set(_canary_payloads()) == set(TOOL_NAMES)


def test_review_free_form_inputs_are_scanned_in_linear_time():
    import time
    started = time.perf_counter()
    cm = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"a": "a." * 32000, "secret_token": "${" * 32000, "b": "{{" * 32000, "c": "http://" * 9000}}
    assert scan(cm)["state"] == "SUCCESS"
    rules = "*a*a*a*a*a*a*a*a*b @acme/x\n" + "/**/a/**/b/**/ @acme/y\n"
    result = layout([".github/CODEOWNERS", "overlays/prod/" + "a/" * 70 + "k.yaml"], codeowners=rules)
    assert result["state"] == "SUCCESS"
    template = appset_template(name="x-{{name}}" + "{{" * 30000)
    assert check_appset(appset([{"list": {"elements": [{"name": "a", "server": "s"}]}}], template, goTemplate=True))["state"] == "SUCCESS"
    assert time.perf_counter() - started < 3.0


def test_review_results_stay_far_below_the_output_limit():
    paths = [f"overlays/{'-'.join(f'seg{i}x{k}' for k in range(25))[:200]}/k.yaml" for i in range(2000)]
    result = layout(paths, expected=("prod",), production=())
    assert len(result["envs_found"]) == 200 and len(json.dumps(result)) < 200_000
    steps = [{"setWeight": 10}] * 1001
    invalid("gitops_rollout_check", {"rollout": rollout({"steps": steps}), "analysis_templates": []}, "rollout")
    assert check_rollout(rollout({"steps": [{"setWeight": 10}] * 999 + [ANALYSIS_STEP]}), max_first_step_weight=100)["step_count"] == 1000
