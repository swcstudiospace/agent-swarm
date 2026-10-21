"""Behaviour tests for the A23 Service Mesh & Networking Engineer tools (mesh_* analyses over parsed manifests).

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
AGENT, SLUG, PREFIX = "A23", "a23-mesh-network", "mesh_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "mesh_authz_lint",
    "mesh_mtls_coverage",
    "mesh_traffic_split",
    "mesh_destination_policy",
    "mesh_gateway_api_check",
    "mesh_netpol_coverage",
    "mesh_netpol_reachability",
    "mesh_retry_amplification",
    "mesh_timeout_hierarchy",
    "mesh_dns_ndots",
    "mesh_egress_coverage",
    "mesh_cidr_overlap",
    "mesh_ingress_tls",
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


# --- helpers for manifests and findings -----------------------------------------------------------------------


def res(name: str, namespace: str | None = None, spec: dict | None = None, **extra) -> dict:
    metadata = {"name": name, **({"namespace": namespace} if namespace else {})}
    return {"metadata": metadata, "spec": spec if spec is not None else {}, **extra}


def ids(result: dict) -> list[str]:
    return sorted(f["id"] for f in result["findings"])


def found(result: dict, fid: str) -> list[dict]:
    return [f for f in result["findings"] if f["id"] == fid]


def severity(result: dict, fid: str) -> str:
    matches = found(result, fid)
    assert matches, (fid, ids(result))
    return matches[0]["severity"]


# --- mesh_authz_lint ------------------------------------------------------------------------------------------


def allow_from(principals=None, **source) -> dict:
    return {"from": [{"source": {**({"principals": principals} if principals else {}), **source}}]}


def test_authz_namespace_wide_empty_allow_is_the_default_deny():
    result = ok("mesh_authz_lint", {"policies": [res("deny-all", "payments")], "namespaces": ["payments"]})
    assert result["namespaces_without_default_deny"] == [] and result["mesh_wide_default_deny"] is False
    assert (result["policy_count"], result["namespace_count"], result["verdict"], result["finding_count"]) == (1, 1, "pass", 0)


def test_authz_mesh_wide_deny_in_root_namespace_covers_every_namespace():
    policies = [res("deny-all", "istio-system")]
    result = ok("mesh_authz_lint", {"policies": policies, "namespaces": ["a", "b"]})
    assert result["mesh_wide_default_deny"] is True and result["namespaces_without_default_deny"] == []
    custom = ok("mesh_authz_lint", {"policies": policies, "namespaces": ["a"], "root_namespace": "mesh-root"})
    assert custom["mesh_wide_default_deny"] is False and custom["namespaces_without_default_deny"] == ["a"]
    moved = ok("mesh_authz_lint", {"policies": [res("deny-all", "mesh-root")], "namespaces": ["a"], "root_namespace": "mesh-root"})
    assert moved["mesh_wide_default_deny"] is True and moved["namespaces_without_default_deny"] == []


def test_authz_namespaces_default_to_those_with_policies_and_missing_default_deny_is_major():
    policies = [res("deny-all", "a"), res("p", "b", {"selector": {"matchLabels": {"app": "x"}}, "rules": [allow_from(["sa"])]})]
    result = ok("mesh_authz_lint", {"policies": policies})
    assert result["namespace_count"] == 2 and result["namespaces_without_default_deny"] == ["b"]
    assert severity(result, "AUTHZ-NO-DEFAULT-DENY") == "major" and result["verdict"] == "fail"
    explicit = ok("mesh_authz_lint", {"policies": policies, "namespaces": ["a", "b", "c", "a"]})
    assert explicit["namespace_count"] == 3 and explicit["namespaces_without_default_deny"] == ["b", "c"]


def test_authz_workload_scoped_empty_policy_is_not_a_namespace_default_deny():
    scoped = res("deny-api", "a", {"selector": {"matchLabels": {"app": "api"}}})
    by_ref = res("deny-gw", "a", {"targetRefs": [{"kind": "Gateway", "name": "gw"}]})
    result = ok("mesh_authz_lint", {"policies": [scoped, by_ref], "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == ["a"]
    empty_selector = ok("mesh_authz_lint", {"policies": [res("deny", "a", {"selector": {"matchLabels": {}}})], "namespaces": ["a"]})
    assert empty_selector["namespaces_without_default_deny"] == []


def test_authz_allow_all_rule_is_a_blocker_namespace_wide_and_cancels_the_default_deny():
    policies = [res("deny-all", "a"), res("open", "a", {"rules": [{}]})]
    result = ok("mesh_authz_lint", {"policies": policies, "namespaces": ["a"]})
    assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker" and "to the namespace" in found(result, "AUTHZ-ALLOW-ALL")[0]["summary"]
    assert result["namespaces_without_default_deny"] == ["a"]
    mesh = ok("mesh_authz_lint", {"policies": [res("deny", "istio-system"), res("open", "istio-system", {"rules": [{}]})], "namespaces": ["a"]})
    assert mesh["mesh_wide_default_deny"] is False and "to the mesh" in found(mesh, "AUTHZ-ALLOW-ALL")[0]["summary"]
    assert mesh["namespaces_without_default_deny"] == ["a"]


def test_authz_workload_scoped_allow_all_is_major_and_keeps_the_namespace_default_deny():
    policies = [res("deny-all", "a"), res("open-api", "a", {"selector": {"matchLabels": {"app": "api"}}, "rules": [{}]})]
    result = ok("mesh_authz_lint", {"policies": policies, "namespaces": ["a"]})
    assert severity(result, "AUTHZ-ALLOW-ALL") == "major" and "selected workloads" in found(result, "AUTHZ-ALLOW-ALL")[0]["summary"]
    assert result["namespaces_without_default_deny"] == []


def test_authz_negative_matches_in_an_allow_rule_are_not_a_default_deny():
    # istio.io authorization policy: not* source and operation fields admit every value except the listed ones
    rules = [{"from": [{"source": {"notPrincipals": ["cluster.local/ns/x/sa/y"]}}]}, {"from": [{"source": {"notRequestPrincipals": ["*"]}}]},
             {"from": [{"source": {"notNamespaces": ["web"]}}]}, {"from": [{"source": {"notIpBlocks": ["10.0.0.0/8"]}}]},
             {"from": [{"source": {"notRemoteIpBlocks": ["10.0.0.0/8"]}}]}, {"to": [{"operation": {"notPorts": ["22"]}}]},
             {"to": [{"operation": {"notHosts": ["admin.example.com"]}}]}, {"to": [{"operation": {"notPaths": ["/admin"]}}]},
             {"to": [{"operation": {"notMethods": ["DELETE"]}}]}, {"when": [{"key": "request.headers[x-team]", "notValues": ["ops"]}]}]
    for rule in rules:
        result = ok("mesh_authz_lint", {"policies": [res("allow-but-one", "a", {"rules": [rule]})], "namespaces": ["a"]})
        assert result["namespaces_without_default_deny"] == ["a"] and result["verdict"] == "fail", rule
        assert severity(result, "AUTHZ-NEGATIVE-MATCH") == "major", rule
    cancelled = ok("mesh_authz_lint", {"policies": [res("deny-all", "a"), res("open", "a", {"rules": [rules[2]]})], "namespaces": ["a"]})
    assert cancelled["namespaces_without_default_deny"] == ["a"]
    scoped = res("api-not-web", "a", {"selector": {"matchLabels": {"app": "api"}}, "rules": [rules[2]]})
    scoped_result = ok("mesh_authz_lint", {"policies": [res("deny-all", "a"), scoped], "namespaces": ["a"]})
    assert scoped_result["namespaces_without_default_deny"] == [] and severity(scoped_result, "AUTHZ-NEGATIVE-MATCH") == "major"
    deny = res("deny-not-web", "a", {"action": "DENY", "rules": [rules[2]]})
    assert "AUTHZ-NEGATIVE-MATCH" not in ids(ok("mesh_authz_lint", {"policies": [deny], "namespaces": ["a"]}))


def test_authz_selector_keys_other_than_match_labels_scope_the_policy_and_are_reported():
    expressions = res("api-only", "a", {"selector": {"matchExpressions": [{"key": "app", "operator": "In", "values": ["api"]}]}, "rules": []})
    alone = ok("mesh_authz_lint", {"policies": [expressions], "namespaces": ["a"]})
    assert alone["namespaces_without_default_deny"] == ["a"] and severity(alone, "AUTHZ-SELECTOR-UNSUPPORTED") == "minor"
    with_deny = ok("mesh_authz_lint", {"policies": [res("deny-all", "a"), expressions], "namespaces": ["a"]})
    assert with_deny["namespaces_without_default_deny"] == [] and severity(with_deny, "AUTHZ-SELECTOR-UNSUPPORTED") == "minor"
    empty = res("deny", "a", {"selector": {"matchLabels": {}, "matchExpressions": []}})
    assert "AUTHZ-SELECTOR-UNSUPPORTED" not in ids(ok("mesh_authz_lint", {"policies": [empty], "namespaces": ["a"]}))


def test_authz_wildcards_in_allow_rules_are_major_and_prefix_patterns_minor():
    rules = [allow_from(["*"]), allow_from(requestPrincipals=["*"]), allow_from(namespaces=["*"]),
             allow_from(["cluster.local/ns/*"]), allow_from(namespaces=["*-prod"]),
             allow_from(ipBlocks=["0.0.0.0/0"]), allow_from(remoteIpBlocks=["::/0"]), allow_from(ipBlocks=["10.0.0.0/8"])]
    result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"selector": {"matchLabels": {"a": "b"}}, "rules": rules})], "namespaces": []})
    assert [f["summary"] for f in found(result, "AUTHZ-WILDCARD-PRINCIPAL")] == ['a/p: rules[0] allows any principal (principals: "*")']
    assert len(found(result, "AUTHZ-WILDCARD-REQUEST-PRINCIPAL")) == 1 and len(found(result, "AUTHZ-WILDCARD-NAMESPACE")) == 1
    assert len(found(result, "AUTHZ-OPEN-IPBLOCK")) == 2
    assert [severity(result, i) for i in ("AUTHZ-WILDCARD-PRINCIPAL", "AUTHZ-OPEN-IPBLOCK", "AUTHZ-BROAD-PATTERN")] == ["major", "major", "minor"]
    assert len(found(result, "AUTHZ-BROAD-PATTERN")) == 2


def test_authz_wildcards_are_ignored_outside_allow_rules():
    deny = res("deny", "a", {"action": "DENY", "rules": [allow_from(["*"], ipBlocks=["0.0.0.0/0"])]})
    audit = res("audit", "a", {"action": "AUDIT", "rules": [allow_from(["*"])]})
    result = ok("mesh_authz_lint", {"policies": [deny, audit], "namespaces": []})
    assert result["findings"] == []


def test_authz_rule_without_source_is_minor_unless_conditions_exist():
    to_only = {"to": [{"operation": {"methods": ["GET"]}}]}
    when_only = {"when": [{"key": "request.headers[x]", "values": ["1"]}]}
    result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"selector": {"matchLabels": {"a": "b"}}, "rules": [to_only, when_only]})],
                                    "namespaces": []})
    assert [f["summary"].startswith("a/p: rules[0]") for f in found(result, "AUTHZ-NO-SOURCE")] == [True]
    assert severity(result, "AUTHZ-NO-SOURCE") == "minor" and result["verdict"] == "warn"


def test_authz_action_checks():
    policies = [res("deny-empty", "a", {"action": "DENY"}), res("custom", "a", {"action": "CUSTOM", "rules": [{}]}),
                res("custom-ok", "a", {"action": "CUSTOM", "provider": {"name": "ext-authz"}}), res("bogus", "a", {"action": "BLOCK"}),
                res("both", "a", {"selector": {"matchLabels": {"a": "b"}}, "targetRefs": [{"name": "gw"}]})]
    result = ok("mesh_authz_lint", {"policies": policies, "namespaces": []})
    assert ids(result) == ["AUTHZ-ACTION-UNKNOWN", "AUTHZ-CUSTOM-NO-PROVIDER", "AUTHZ-DENY-NO-RULES", "AUTHZ-SCOPE-CONFLICT"]
    assert [severity(result, i) for i in ids(result)] == ["major", "major", "minor", "major"]


def test_authz_malformed_policies_name_the_field():
    invalid("mesh_authz_lint", {"policies": [{"spec": {}}]}, "policies")
    invalid("mesh_authz_lint", {"policies": [{"metadata": {"name": "p"}, "spec": {"rules": "all"}}]}, "policies")
    invalid("mesh_authz_lint", {"policies": [{"metadata": {"name": "p"}, "spec": {"rules": ["x"]}}]}, "policies")
    invalid("mesh_authz_lint", {"policies": [{"metadata": {"name": "p", "namespace": ""}}]}, "policies")


# --- mesh_mtls_coverage ---------------------------------------------------------------------------------------


def pa(name, namespace, mode=None, labels=None, ports=None, created=None) -> dict:
    spec: dict = {}
    if mode:
        spec["mtls"] = {"mode": mode}
    if labels:
        spec["selector"] = {"matchLabels": labels}
    if ports:
        spec["portLevelMtls"] = {str(p): {"mode": m} for p, m in ports.items()}
    doc = res(name, namespace, spec)
    if created:
        doc["metadata"]["creationTimestamp"] = created
    return doc


def wl(namespace, name, **labels) -> dict:
    return {"namespace": namespace, "name": name, "labels": labels}


def test_mtls_without_any_policy_is_permissive_and_fails_the_default_target():
    result = ok("mesh_mtls_coverage", {"peer_authentications": [], "workloads": [wl("a", "x"), wl("a", "y")]})
    assert (result["strict_count"], result["permissive_count"], result["disabled_count"], result["strict_pct"]) == (0, 2, 0, 0.0)
    assert result["non_strict_workloads"] == ["a/x:PERMISSIVE", "a/y:PERMISSIVE"]
    assert ids(result) == ["MTLS-COVERAGE-BELOW-TARGET", "MTLS-PERMISSIVE"]
    assert found(result, "MTLS-PERMISSIVE")[0]["summary"] == "2 workload(s) accept plaintext (PERMISSIVE) via default (no PeerAuthentication): a/x, a/y"


def test_mtls_precedence_workload_then_namespace_then_mesh():
    policies = [pa("mesh", "istio-system", "STRICT"), pa("ns", "legacy", "PERMISSIVE"), pa("one", "legacy", "STRICT", {"app": "api"}),
                pa("off", "other", "DISABLE", {"app": "db"})]
    workloads = [wl("legacy", "api", app="api"), wl("legacy", "web", app="web"), wl("other", "db", app="db"), wl("other", "app", app="x"),
                 wl("istio-system", "ingress")]
    result = ok("mesh_mtls_coverage", {"peer_authentications": policies, "workloads": workloads})
    assert result["non_strict_workloads"] == ["legacy/web:PERMISSIVE", "other/db:DISABLE"]
    assert (result["strict_count"], result["permissive_count"], result["disabled_count"]) == (3, 1, 1)
    assert result["namespaces"] == [{"namespace": "istio-system", "workload_count": 1, "strict_count": 1, "strict_pct": 100.0},
                                    {"namespace": "legacy", "workload_count": 2, "strict_count": 1, "strict_pct": 50.0},
                                    {"namespace": "other", "workload_count": 2, "strict_count": 1, "strict_pct": 50.0}]
    assert severity(result, "MTLS-DISABLED") == "major" and severity(result, "MTLS-PERMISSIVE") == "minor"


def test_mtls_unset_mode_defers_to_the_parent_policy():
    policies = [pa("mesh", "istio-system", "STRICT"), pa("ns", "a", "UNSET"), pa("w", "a", None, {"app": "x"})]
    result = ok("mesh_mtls_coverage", {"peer_authentications": policies, "workloads": [wl("a", "x", app="x"), wl("a", "y")]})
    assert result["strict_count"] == 2 and result["verdict"] == "pass"


def test_mtls_port_level_exceptions_weaken_a_strict_workload():
    policies = [pa("mesh", "istio-system", "STRICT"), pa("api", "a", "STRICT", {"app": "api"}, {9090: "DISABLE", 8080: "PERMISSIVE", 443: "UNSET"}),
                pa("soft", "b", "STRICT", {"app": "soft"}, {8080: "PERMISSIVE"}), pa("inherit", "c", None, {"app": "i"}, {8080: "UNSET"})]
    workloads = [wl("a", "api", app="api"), wl("b", "soft", app="soft"), wl("c", "i", app="i")]
    result = ok("mesh_mtls_coverage", {"peer_authentications": policies, "workloads": workloads})
    assert (result["strict_count"], result["port_exception_count"], result["permissive_count"]) == (1, 2, 0)
    assert result["non_strict_workloads"] == ["a/api:STRICT-with-port-exceptions", "b/soft:STRICT-with-port-exceptions"]
    summaries = {f["summary"]: f["severity"] for f in found(result, "MTLS-PORT-EXCEPTION")}
    assert summaries == {"a/api: STRICT workload accepts plaintext on port(s) 8080=PERMISSIVE, 9090=DISABLE": "major",
                         "b/soft: STRICT workload accepts plaintext on port(s) 8080=PERMISSIVE": "minor"}


def test_mtls_port_level_policy_without_selector_is_flagged():
    bad = res("bad", "a", {"mtls": {"mode": "STRICT"}, "portLevelMtls": {"80": {"mode": "DISABLE"}}})
    result = ok("mesh_mtls_coverage", {"peer_authentications": [bad], "workloads": [wl("a", "x")]})
    assert severity(result, "MTLS-PORT-NO-SELECTOR") == "major"


def test_mtls_min_strict_pct_boundary_is_inclusive():
    policies = [pa("ns", "a", "STRICT")]
    workloads = [wl("a", "x"), wl("a", "y"), wl("b", "z"), wl("b", "w")]
    base = {"peer_authentications": policies, "workloads": workloads}
    assert ok("mesh_mtls_coverage", {**base, "min_strict_pct": 50})["strict_pct"] == 50.0
    assert "MTLS-COVERAGE-BELOW-TARGET" not in ids(ok("mesh_mtls_coverage", {**base, "min_strict_pct": 50}))
    below = ok("mesh_mtls_coverage", {**base, "min_strict_pct": 50.01})
    assert found(below, "MTLS-COVERAGE-BELOW-TARGET")[0]["summary"] == "strict mTLS covers 2 of 4 workloads (50.0000%), target 50.01%"


def test_mtls_oldest_policy_wins_and_undated_conflicts_are_flagged():
    dated = [pa("new", "a", "STRICT", {"app": "x"}, created="2026-02-01T00:00:00Z"), pa("old", "a", "DISABLE", {"app": "x"}, created="2026-01-01T00:00:00+00:00")]
    result = ok("mesh_mtls_coverage", {"peer_authentications": dated, "workloads": [wl("a", "x", app="x")]})
    assert result["disabled_count"] == 1 and "MTLS-CONFLICT" not in ids(result)
    undated = [pa("b", "a", "STRICT", {"app": "x"}), pa("a", "a", "DISABLE", {"app": "x"})]
    flagged = ok("mesh_mtls_coverage", {"peer_authentications": undated, "workloads": [wl("a", "x", app="x")]})
    assert (flagged["disabled_count"], flagged["strict_count"]) == (1, 0)
    assert severity(flagged, "MTLS-CONFLICT") == "major" and "(a/a, a/b)" in found(flagged, "MTLS-CONFLICT")[0]["summary"]
    same_mode = [pa("b", "a", "STRICT", {"app": "x"}), pa("a", "a", "STRICT", {"app": "x"})]
    assert "MTLS-CONFLICT" not in ids(ok("mesh_mtls_coverage", {"peer_authentications": same_mode, "workloads": [wl("a", "x", app="x")]}))


def test_mtls_conflicts_never_count_strict_whatever_the_names_or_timestamps():
    for first, second in (("a-strict", "z-permissive"), ("a-permissive", "z-strict")):
        policies = [pa(first, "a", "STRICT", {"app": "x"}), pa(second, "a", "PERMISSIVE", {"app": "x"})]
        result = ok("mesh_mtls_coverage", {"peer_authentications": policies, "workloads": [wl("a", "x", app="x")]})
        assert (result["strict_count"], result["permissive_count"], result["verdict"]) == (0, 1, "fail")
        assert severity(result, "MTLS-CONFLICT") == "major"
    tied = [pa("x", "a", "STRICT", {"app": "x"}, created="2026-01-01T00:00:00Z"), pa("y", "a", "PERMISSIVE", {"app": "x"}, created="2026-01-01T00:00:00Z")]
    undated_rival = [pa("old", "a", "STRICT", {"app": "x"}, created="2026-01-01T00:00:00Z"), pa("new", "a", "PERMISSIVE", {"app": "x"})]
    for policies in (tied, undated_rival):
        result = ok("mesh_mtls_coverage", {"peer_authentications": policies, "workloads": [wl("a", "x", app="x")]})
        assert result["strict_count"] == 0 and severity(result, "MTLS-CONFLICT") == "major"
    decided = [pa("old", "a", "STRICT", {"app": "x"}, created="2026-01-01T00:00:00Z"),
               pa("new", "a", "PERMISSIVE", {"app": "x"}, created="2026-02-01T00:00:00Z")]
    result = ok("mesh_mtls_coverage", {"peer_authentications": decided, "workloads": [wl("a", "x", app="x")]})
    assert result["strict_count"] == 1 and "MTLS-CONFLICT" not in ids(result)


def test_mtls_selector_keys_other_than_match_labels_and_empty_workloads_are_refused():
    expressions = res("p", "a", {"mtls": {"mode": "STRICT"}, "selector": {"matchExpressions": [{"key": "app", "operator": "Exists"}]}})
    invalid("mesh_mtls_coverage", {"peer_authentications": [expressions], "workloads": [wl("a", "x")]}, "peer_authentications")
    direct = importlib.import_module("swarm.tools.a23_tools").mtls_coverage({"peer_authentications": [], "workloads": []})
    assert (direct["state"], direct["field"]) == ("INVALID_INPUT", "workloads")


def test_mtls_custom_root_namespace_and_invalid_inputs():
    result = ok("mesh_mtls_coverage", {"peer_authentications": [pa("mesh", "mesh-root", "STRICT")], "workloads": [wl("a", "x")],
                                       "root_namespace": "mesh-root"})
    assert result["strict_count"] == 1
    invalid("mesh_mtls_coverage", {"peer_authentications": [pa("p", "a", "ENFORCE")], "workloads": [wl("a", "x")]}, "peer_authentications")
    invalid("mesh_mtls_coverage", {"peer_authentications": [pa("p", "a", "STRICT", {"a": "b"}, {"http": "STRICT"})], "workloads": [wl("a", "x")]}, "peer_authentications")
    invalid("mesh_mtls_coverage", {"peer_authentications": [pa("p", "a", "STRICT", {"a": "b"}, {70000: "STRICT"})], "workloads": [wl("a", "x")]}, "peer_authentications")
    invalid("mesh_mtls_coverage", {"peer_authentications": [pa("p", "a", "STRICT", {"a": "b"}, {80: "ENFORCE"})], "workloads": [wl("a", "x")]}, "peer_authentications")
    invalid("mesh_mtls_coverage", {"peer_authentications": [pa("p", "a", "STRICT", created="yesterday")], "workloads": [wl("a", "x")]}, "peer_authentications")
    invalid("mesh_mtls_coverage", {"peer_authentications": [pa("p", "a", "STRICT", created="2026-01-01T00:00:00")], "workloads": [wl("a", "x")]}, "peer_authentications")
    invalid("mesh_mtls_coverage", {"peer_authentications": [], "workloads": [{"namespace": "a", "name": "x", "labels": {"k": 1}}]}, "workloads")
    invalid("mesh_mtls_coverage", {"peer_authentications": [], "workloads": [{"name": "x"}]}, "workloads")


# --- mesh_traffic_split ---------------------------------------------------------------------------------------


def dest(host, weight=None, subset=None) -> dict:
    out = {"destination": {"host": host, **({"subset": subset} if subset else {})}}
    if weight is not None:
        out["weight"] = weight
    return out


def vs(name, namespace, http=None, **extra) -> dict:
    return res(name, namespace, {"hosts": [name], **({"http": http} if http is not None else {}), **extra})


def dr(name, namespace, host, subsets=()) -> dict:
    return res(name, namespace, {"host": host, "subsets": [{"name": s, "labels": {"version": s}} for s in subsets]})


def split(virtual_services, destination_rules=()) -> dict:
    return ok("mesh_traffic_split", {"virtual_services": virtual_services, "destination_rules": list(destination_rules)})


def test_traffic_split_clean_canary_passes_with_counts():
    service = vs("reviews", "d", [{"route": [dest("reviews", 90, "v1"), dest("reviews", 10, "v2")], "timeout": "10s",
                                   "retries": {"attempts": 2, "perTryTimeout": "3s"}}])
    result = split([service], [dr("reviews", "d", "reviews", ["v1", "v2"])])
    assert (result["virtual_service_count"], result["route_count"], result["destination_count"], result["verdict"]) == (1, 1, 2, "pass")
    assert result["findings"] == []


def test_traffic_split_weight_sum_zero_and_single_destination_rules():
    routes = [{"route": [dest("a", 50), dest("b", 40)]}, {"route": [dest("a", 60), dest("b", 40)]}, {"route": [dest("a"), dest("b")]},
              {"route": [dest("a", 0), dest("b", 0)]}, {"route": [dest("a")]}, {"route": [dest("a", 100)]}, {"route": [dest("a", 30)]},
              {"route": [dest("a", 101), dest("b")]}]
    result = split([vs("r", "d", routes)])
    sums = {f["summary"] for f in found(result, "TRAFFIC-WEIGHT-SUM")}
    # Istio VirtualService reference: "A destination will receive weight/(sum of all weights) requests", so a sum other than 100 is valid
    assert sums == {"d/r: http[0]: weights sum to 90, not 100; Istio treats weights as relative shares, so the split is a 55.56% / b 44.44%",
                    "d/r: http[7]: weights sum to 101, not 100; Istio treats weights as relative shares, so the split is a 100% / b 0%"}
    assert [f["summary"] for f in found(result, "TRAFFIC-WEIGHT-ZERO")] == [
        "d/r: http[2]: destination weights are all 0 or unset, no traffic is routed",
        "d/r: http[3]: destination weights are all 0 or unset, no traffic is routed"]
    assert [f["summary"] for f in found(result, "TRAFFIC-SINGLE-WEIGHT")] == [
        "d/r: http[6]: single destination has weight 30; it carries all traffic regardless"]
    assert severity(result, "TRAFFIC-WEIGHT-ZERO") == "blocker" and severity(result, "TRAFFIC-WEIGHT-SUM") == "minor"


def test_traffic_split_tcp_and_tls_routes_use_the_same_weight_rule_and_redirects_need_no_route():
    spec = {"hosts": ["x"], "tcp": [{"route": [dest("a", 70), dest("b", 20)]}], "tls": [{"route": [dest("a", 100)]}],
            "http": [{"redirect": {"uri": "/new"}}, {"directResponse": {"status": 200}}, {"name": "empty"}]}
    result = split([res("x", "d", spec)])
    assert (result["route_count"], result["destination_count"]) == (5, 3)
    assert ids(result) == ["TRAFFIC-ROUTE-EMPTY", "TRAFFIC-WEIGHT-SUM"]
    assert found(result, "TRAFFIC-ROUTE-EMPTY")[0]["summary"] == "d/x: http[2] (empty) has no route, redirect or directResponse"
    assert found(result, "TRAFFIC-WEIGHT-SUM")[0]["summary"].startswith("d/x: tcp[0]")


def test_traffic_split_subsets_must_exist_for_the_resolved_host():
    routes = [{"route": [dest("reviews", 50, "v1"), dest("reviews", 50, "v9")]}, {"route": [dest("ratings", None, "v1")]},
              {"route": [dest("reviews.d.svc.cluster.local", None, "v2")]}, {"route": [dest("reviews.other.svc.cluster.local", None, "v1")]}]
    rules = [dr("reviews", "d", "reviews", ["v1"]), dr("reviews-v2", "x", "reviews.d.svc.cluster.local", ["v2"])]
    result = split([vs("r", "d", routes)], rules)
    assert [f["summary"] for f in found(result, "TRAFFIC-SUBSET-MISSING")] == [
        "d/r: http[0]: subset 'v9' is not defined by any DestinationRule for reviews.d.svc.cluster.local (requests fail 503)"]
    assert sorted(f["summary"] for f in found(result, "TRAFFIC-SUBSET-NO-DESTINATIONRULE")) == [
        "d/r: http[1]: subset 'v1' of ratings.d.svc.cluster.local has no DestinationRule defining subsets",
        "d/r: http[3]: subset 'v1' of reviews.other.svc.cluster.local has no DestinationRule defining subsets"]
    other_namespace = split([vs("r", "other", [{"route": [dest("reviews", None, "v1")]}])], rules)
    assert ids(other_namespace) == ["TRAFFIC-SUBSET-NO-DESTINATIONRULE"]


def test_traffic_split_retry_attempt_ceiling_boundary():
    def with_attempts(n):
        return split([vs("r", "d", [{"route": [dest("a")], "timeout": "60s", "retries": {"attempts": n, "perTryTimeout": "1s"}}])])
    assert with_attempts(5)["findings"] == []
    high = with_attempts(6)
    assert [f["summary"] for f in found(high, "TRAFFIC-RETRY-ATTEMPTS-HIGH")] == ["d/r: http[0]: retries.attempts 6 exceeds 5"]
    assert severity(high, "TRAFFIC-RETRY-ATTEMPTS-HIGH") == "minor"
    # Istio's ValidateHTTPRetry rejects perTryTimeout next to attempts 0 ("http retry policy configured when attempts are set to 0")
    assert ids(with_attempts(0)) == ["TRAFFIC-RETRY-DISABLED-CONFIG"]
    assert split([vs("r", "d", [{"route": [dest("a")], "timeout": "60s", "retries": {"attempts": 0}}])])["findings"] == []


def test_traffic_split_retry_budget_versus_timeout_boundaries():
    def route(timeout, per_try, attempts=2):
        return split([vs("r", "d", [{"route": [dest("a")], "timeout": timeout, "retries": {"attempts": attempts, "perTryTimeout": per_try}}])])
    assert route("3s", "1s")["findings"] == []  # (2+1) x 1s == 3s: exactly fits
    over = route("2999ms", "1s")
    assert ids(over) == ["TRAFFIC-RETRY-BUDGET"] and "3 tries of 1s cannot complete within the route timeout 2999ms" in over["findings"][0]["summary"]
    assert ids(route("1s", "1s", 0)) == ["TRAFFIC-RETRY-DISABLED-CONFIG"]
    dead = route("1s", "1001ms")
    assert ids(dead) == ["TRAFFIC-PERTRY-EXCEEDS-TIMEOUT"] and severity(dead, "TRAFFIC-PERTRY-EXCEEDS-TIMEOUT") == "major"
    equal = route("1s", "1s")
    assert ids(equal) == ["TRAFFIC-RETRY-BUDGET"]
    assert route("1m", "30s", 1)["findings"] == [] and route("1h30m", "30m", 2)["findings"] == []
    assert route("1.5s", "500ms")["findings"] == []


def test_traffic_split_retries_without_timeout_are_minor_and_zero_timeout_is_rejected_by_istio():
    no_timeout = split([vs("r", "d", [{"route": [dest("a")], "retries": {"attempts": 2}}])])
    assert ids(no_timeout) == ["TRAFFIC-RETRY-NO-TIMEOUT"] and severity(no_timeout, "TRAFFIC-RETRY-NO-TIMEOUT") == "minor"
    # istio pkg/config/validation: http.Timeout goes through ValidateDuration (>= 1ms, whole ms), so 0s is a rejected manifest
    for zero in ("0s", "0"):
        rejected = split([vs("r", "d", [{"route": [dest("a")], "timeout": zero, "retries": {"attempts": 1, "perTryTimeout": "5s"}}])])
        assert ids(rejected) == ["TRAFFIC-DURATION-INVALID", "TRAFFIC-RETRY-NO-TIMEOUT"], zero
        assert found(rejected, "TRAFFIC-DURATION-INVALID")[0]["summary"] == (
            f"d/r: http[0]: timeout {zero} is below the 1ms minimum; Istio rejects it (omit the field for no timeout)")
        assert severity(rejected, "TRAFFIC-DURATION-INVALID") == "major"


def test_traffic_split_invalid_inputs_name_the_field():
    for bad in ({"timeout": "soon"}, {"timeout": "-1s"}, {"timeout": "5"}, {"timeout": 5}, {"timeout": "1s2"}, {"retries": {"attempts": -1}},
                {"retries": {"attempts": 1, "perTryTimeout": "x"}}, {"retries": {"attempts": True}}):
        invalid("mesh_traffic_split", {"virtual_services": [vs("r", "d", [{"route": [dest("a")], **bad}])], "destination_rules": []}, "virtual_services")
    for weight in (-1, 1.5, True, "50"):
        invalid("mesh_traffic_split", {"virtual_services": [vs("r", "d", [{"route": [{"destination": {"host": "a"}, "weight": weight}]}])],
                                       "destination_rules": []}, "virtual_services")
    invalid("mesh_traffic_split", {"virtual_services": [vs("r", "d", [{"route": [{"destination": {}}]}])], "destination_rules": []}, "virtual_services")
    invalid("mesh_traffic_split", {"virtual_services": [], "destination_rules": [res("d", "n", {})]}, "destination_rules")
    invalid("mesh_traffic_split", {"virtual_services": [], "destination_rules": [res("d", "n", {"host": "a", "subsets": [{}]})]}, "destination_rules")


def test_traffic_split_subset_must_be_a_string():
    # a list or object subset was an unhashable key in the DestinationRule subset set (a crash); an integer is not a valid subset either
    rules = [dr("reviews", "d", "reviews", ["v1"])]
    for subset in (["v1"], {"name": "v1"}, 1):
        invalid("mesh_traffic_split", {"virtual_services": [vs("r", "d", [{"route": [{"destination": {"host": "reviews", "subset": subset}, "weight": 100}]}])],
                                       "destination_rules": rules}, "virtual_services")


# --- mesh_destination_policy ----------------------------------------------------------------------------------


def policy_rule(name, traffic_policy=None, subsets=None, host="reviews", namespace="d") -> dict:
    spec: dict = {"host": host}
    if traffic_policy is not None:
        spec["trafficPolicy"] = traffic_policy
    if subsets is not None:
        spec["subsets"] = subsets
    return res(name, namespace, spec)


def dpolicy(rules, workloads=()) -> dict:
    return ok("mesh_destination_policy", {"destination_rules": rules, "workloads": list(workloads)})


GOOD_OUTLIER = {"consecutive5xxErrors": 5, "interval": "10s", "baseEjectionTime": "30s", "maxEjectionPercent": 50}


def test_destination_policy_healthy_rule_passes():
    rule = policy_rule("r", {"outlierDetection": GOOD_OUTLIER, "connectionPool": {"tcp": {"maxConnections": 100, "connectTimeout": "5s"},
                                                                                      "http": {"http2MaxRequests": 1000, "maxRetries": 3}}},
                       [{"name": "v1", "labels": {"version": "v1"}}])
    result = dpolicy([rule], [{"namespace": "d", "service": "reviews", "labels": {"app": "reviews", "version": "v1"}}])
    assert (result["rule_count"], result["subset_count"], result["verdict"], result["findings"]) == (1, 1, "pass", [])


def test_destination_policy_missing_outlier_detection_is_minor_anywhere_it_can_live():
    assert ids(dpolicy([policy_rule("r")])) == ["DR-NO-OUTLIER-DETECTION"]
    in_subset = policy_rule("r", subsets=[{"name": "v1", "labels": {"a": "b"}, "trafficPolicy": {"outlierDetection": GOOD_OUTLIER}}])
    assert dpolicy([in_subset])["findings"] == []
    in_port = policy_rule("r", {"portLevelSettings": [{"port": {"number": 80}, "outlierDetection": GOOD_OUTLIER}]})
    assert dpolicy([in_port])["findings"] == []


def test_destination_policy_outlier_thresholds():
    def outlier(**fields):
        return dpolicy([policy_rule("r", {"outlierDetection": {**GOOD_OUTLIER, **fields}})])
    assert outlier()["findings"] == []
    assert ids(outlier(consecutive5xxErrors=0)) == ["DR-OUTLIER-INERT"]
    assert outlier(consecutive5xxErrors=0, consecutiveGatewayErrors=3)["findings"] == []
    assert ids(outlier(consecutive5xxErrors=0, consecutiveGatewayErrors=0)) == ["DR-OUTLIER-INERT"]
    assert outlier(maxEjectionPercent=99)["findings"] == []
    assert ids(outlier(maxEjectionPercent=100)) == ["DR-OUTLIER-EJECT-ALL"] and severity(outlier(maxEjectionPercent=100), "DR-OUTLIER-EJECT-ALL") == "major"
    assert ids(outlier(maxEjectionPercent=0)) == ["DR-OUTLIER-EJECT-NONE"]
    assert outlier(interval="1ms", baseEjectionTime="1ms")["findings"] == []
    assert ids(outlier(interval="999us")) == ["DR-OUTLIER-DURATION"] and ids(outlier(baseEjectionTime="0s")) == ["DR-OUTLIER-DURATION"]


def test_destination_policy_connection_pool_checks():
    def pool(**sections):
        return dpolicy([policy_rule("r", {"outlierDetection": GOOD_OUTLIER, "connectionPool": sections})])
    assert ids(pool(tcp={"connectTimeout": "0s"})) == ["DR-POOL-CONNECT-TIMEOUT"]
    assert pool(tcp={"connectTimeout": "1ms"}, http={"maxRetries": 1})["findings"] == []
    assert ids(pool(http={"maxRequestsPerConnection": 1, "maxRetries": 1})) == ["DR-POOL-NO-KEEPALIVE"]
    assert pool(http={"maxRequestsPerConnection": 2, "maxRetries": 1})["findings"] == []
    negative = pool(tcp={"maxConnections": -1}, http={"http2MaxRequests": -5, "maxRetries": 0})
    assert [f["id"] for f in negative["findings"]] == ["DR-POOL-NEGATIVE", "DR-POOL-NEGATIVE"] and negative["verdict"] == "fail"
    unbounded = pool(http={"http2MaxRequests": 10})
    assert ids(unbounded) == ["DR-POOL-RETRIES-UNBOUNDED"] and unbounded["verdict"] == "pass"
    assert pool(tcp={"maxConnections": 5})["findings"] == []


def test_destination_policy_tls_mode():
    base = {"outlierDetection": GOOD_OUTLIER}
    assert ids(dpolicy([policy_rule("r", {**base, "tls": {"mode": "DISABLE"}})])) == ["DR-TLS-DISABLE"]
    for mode in ("SIMPLE", "MUTUAL", "ISTIO_MUTUAL"):
        assert dpolicy([policy_rule("r", {**base, "tls": {"mode": mode}})])["findings"] == []
    invalid("mesh_destination_policy", {"destination_rules": [policy_rule("r", {"tls": {"mode": "STRICT"}})], "workloads": []}, "destination_rules")


def test_destination_policy_subset_checks():
    subsets = [{"name": "v1", "labels": {"version": "v1"}}, {"name": "v1", "labels": {"version": "v1"}}, {"name": "all"},
               {"name": "v9", "labels": {"version": "v9"}}, {"name": "multi", "labels": {"version": "v1", "tier": "web"}}]
    rule = policy_rule("r", {"outlierDetection": GOOD_OUTLIER}, subsets)
    result = dpolicy([rule], [{"namespace": "d", "service": "reviews", "labels": {"version": "v1"}}])
    assert ids(result) == ["DR-SUBSET-DUPLICATE", "DR-SUBSET-NO-ENDPOINTS", "DR-SUBSET-NO-ENDPOINTS", "DR-SUBSET-NO-LABELS"]
    assert sorted(f["summary"] for f in found(result, "DR-SUBSET-NO-ENDPOINTS")) == [
        "d/r: no endpoint of reviews.d.svc.cluster.local carries the labels of subset 'multi'",
        "d/r: no endpoint of reviews.d.svc.cluster.local carries the labels of subset 'v9'"]
    assert severity(result, "DR-SUBSET-DUPLICATE") == "blocker"
    unverified = dpolicy([rule], [{"namespace": "d", "service": "other", "labels": {}}])
    assert "DR-SUBSET-NO-ENDPOINTS" not in ids(unverified)


def test_destination_policy_duplicate_hosts_in_one_namespace():
    rules = [policy_rule("a", {"outlierDetection": GOOD_OUTLIER}), policy_rule("b", {"outlierDetection": GOOD_OUTLIER}, host="reviews.d.svc.cluster.local"),
             policy_rule("c", {"outlierDetection": GOOD_OUTLIER}, namespace="x")]
    result = dpolicy(rules)
    assert [f["summary"] for f in found(result, "DR-HOST-DUPLICATE")] == [
        "d: 2 DestinationRules for reviews.d.svc.cluster.local (a, b); Istio merges them only when subsets do not conflict"]


def test_destination_policy_invalid_inputs_name_the_field():
    for bad in ({"outlierDetection": {"maxEjectionPercent": 101}}, {"outlierDetection": {"minHealthPercent": 101}},
                {"outlierDetection": {"consecutive5xxErrors": -1}}, {"outlierDetection": {"interval": "ten"}},
                {"connectionPool": {"tcp": {"maxConnections": "many"}}}, {"connectionPool": {"tcp": {"connectTimeout": 5}}}):
        invalid("mesh_destination_policy", {"destination_rules": [policy_rule("r", bad)], "workloads": []}, "destination_rules")
    invalid("mesh_destination_policy", {"destination_rules": [res("r", "d", {})], "workloads": []}, "destination_rules")
    invalid("mesh_destination_policy", {"destination_rules": [], "workloads": [{"namespace": "d", "labels": {}}]}, "workloads")


# --- mesh_gateway_api_check -----------------------------------------------------------------------------------


def listener(name, port, protocol, **extra) -> dict:
    return {"name": name, "port": port, "protocol": protocol, **extra}


def gateway(name, namespace, listeners, cls="istio") -> dict:
    spec = {"listeners": listeners}
    if cls:
        spec["gatewayClassName"] = cls
    return res(name, namespace, spec)


def route(name, namespace, parents, rules=None, hostnames=None) -> dict:
    spec: dict = {"parentRefs": parents, "rules": rules if rules is not None else [{"backendRefs": [{"name": "web", "port": 80}]}]}
    if hostnames is not None:
        spec["hostnames"] = hostnames
    return res(name, namespace, spec)


def grant(namespace, senders, receivers, name="g") -> dict:
    return res(name, namespace, {"from": senders, "to": receivers})


GW_GROUP = "gateway.networking.k8s.io"


def gwcheck(gateways=(), routes=(), grants=(), namespaces=None) -> dict:
    payload = {"gateways": list(gateways), "routes": list(routes), "reference_grants": list(grants)}
    if namespaces is not None:
        payload["namespaces"] = namespaces
    return ok("mesh_gateway_api_check", payload)


def test_gateway_valid_setup_has_no_findings():
    gw = gateway("edge", "infra", [listener("https", 443, "HTTPS", hostname="*.example.com",
                                            tls={"certificateRefs": [{"name": "cert"}]}, allowedRoutes={"namespaces": {"from": "Same"}})])
    web = route("web", "infra", [{"name": "edge", "sectionName": "https"}], hostnames=["shop.example.com"])
    result = gwcheck([gw], [web])
    assert (result["gateway_count"], result["route_count"], result["grant_count"], result["unattached_routes"]) == (1, 1, 0, [])
    assert result["findings"] == [] and result["verdict"] == "pass"


def test_gateway_listener_validity():
    gw = gateway("g", "n", [listener("a", 0, "HTTP")], cls=None)
    invalid("mesh_gateway_api_check", {"gateways": [gw], "routes": [], "reference_grants": []}, "gateways")
    gw = gateway("g", "n", [listener("a", 80, "HTTP"), listener("a", 81, "FTP"), listener("b", 82, "HTTP", hostname="Bad_Host"),
                            listener("c", 83, "TCP", hostname="x.example.com"), listener("d", 84, "example.com/custom")], cls=None)
    result = gwcheck([gw])
    assert ids(result) == ["GW-LISTENER-DUPLICATE", "GW-LISTENER-HOSTNAME", "GW-LISTENER-HOSTNAME", "GW-LISTENER-PROTOCOL", "GW-NO-CLASS"]
    assert [f["summary"] for f in found(result, "GW-LISTENER-PROTOCOL")] == [
        "n/g: listener a: protocol 'FTP' is not one of HTTP, HTTPS, TLS, TCP, UDP or a domain-prefixed custom protocol"]
    empty = gwcheck([gateway("g", "n", [])])
    assert ids(empty) == ["GW-NO-LISTENERS"]


def test_gateway_hostname_syntax_pinned():
    def hostname_findings(host):
        return found(gwcheck([gateway("g", "n", [listener("a", 80, "HTTP", hostname=host)])]), "GW-LISTENER-HOSTNAME")
    for good in ("example.com", "*.example.com", "a-b.c1.example.com", "localhost"):
        assert hostname_findings(good) == [], good
    for bad in ("Example.com", "-a.com", "a-.com", "a..com", "*.", "a.*.com", "ex_ample.com", "*", "a" * 64 + ".com", ("a" * 60 + ".") * 5 + "com"):
        assert len(hostname_findings(bad)) == 1, bad


def test_gateway_tls_requirements_per_protocol():
    listeners = [listener("https-none", 443, "HTTPS"), listener("https-pass", 444, "HTTPS", tls={"mode": "Passthrough", "certificateRefs": [{"name": "c"}]}),
                 listener("tls-terminate", 445, "TLS", tls={"certificateRefs": []}), listener("tls-pass", 446, "TLS", tls={"mode": "Passthrough"}),
                 listener("tls-ok", 447, "TLS", tls={"certificateRefs": [{"name": "c"}]}), listener("http-tls", 80, "HTTP", tls={"certificateRefs": [{"name": "c"}]})]
    result = gwcheck([gateway("g", "n", listeners)])
    assert sorted(f["summary"].split(": ")[1] for f in found(result, "GW-LISTENER-TLS-MISSING")) == [
        "listener https-none", "listener https-pass", "listener tls-terminate"]
    assert [f["summary"] for f in found(result, "GW-LISTENER-TLS-UNUSED")] == [
        "n/g: listener http-tls: tls must not be set on a HTTP listener; the API rejects it"]
    assert severity(result, "GW-LISTENER-TLS-UNUSED") == "major"


def test_gateway_port_sharing_rules():
    def conflicts(*listeners):
        return [f["summary"] for f in found(gwcheck([gateway("g", "n", list(listeners))]), "GW-LISTENER-PORT-CONFLICT")]
    http = lambda name, host=None: listener(name, 80, "HTTP", **({"hostname": host} if host else {}))  # noqa: E731
    assert conflicts(http("a", "a.example.com"), http("b", "b.example.com")) == []
    assert conflicts(http("a"), http("b")) == ["n/g: listeners a and b conflict on port 80 (same protocol class and hostname '<any>')"]
    assert conflicts(http("a", "x.example.com"), http("b", "x.example.com")) == [
        "n/g: listeners a and b conflict on port 80 (same protocol class and hostname 'x.example.com')"]
    assert conflicts(http("a"), listener("b", 80, "HTTPS", hostname="b.example.com")) == [
        "n/g: listeners a, b share port 80 with incompatible protocols HTTP, HTTPS"]
    tls = {"certificateRefs": [{"name": "c"}]}
    assert conflicts(listener("a", 443, "HTTPS", hostname="a.example.com", tls=tls), listener("b", 443, "TLS", hostname="b.example.com", tls=tls)) == []
    assert len(conflicts(listener("a", 443, "HTTPS", hostname="a.example.com", tls=tls), listener("b", 443, "TLS", hostname="a.example.com", tls=tls))) == 1
    assert len(conflicts(listener("a", 9, "TCP"), listener("b", 9, "TCP"))) == 1
    assert conflicts(listener("a", 9, "TCP"), listener("b", 9, "UDP")) == []
    assert conflicts(http("a"), listener("b", 80, "TCP")) != []


def test_gateway_allowed_routes_scope():
    listeners = [listener("bad", 80, "HTTP", allowedRoutes={"namespaces": {"from": "Everyone"}}),
                 listener("sel", 81, "HTTP", allowedRoutes={"namespaces": {"from": "Selector"}}),
                 listener("all", 82, "HTTP", allowedRoutes={"namespaces": {"from": "All"}})]
    result = gwcheck([gateway("g", "n", listeners)])
    assert sorted(f["id"] + ":" + f["severity"] for f in result["findings"]) == [
        "GW-ROUTES-ALL-NAMESPACES:info", "GW-ROUTES-SCOPE:major", "GW-ROUTES-SCOPE:major"]


def test_gateway_certificate_in_another_namespace_needs_a_matching_reference_grant():
    gw = gateway("edge", "infra", [listener("https", 443, "HTTPS", tls={"certificateRefs": [{"name": "cert", "namespace": "certs"}]})])
    missing = gwcheck([gw])
    assert [f["summary"] for f in found(missing, "GW-CERT-NO-GRANT")] == [
        "infra/edge: listener https: certificate certs/cert needs a ReferenceGrant in certs allowing Gateways from infra"]
    sender = {"group": GW_GROUP, "kind": "Gateway", "namespace": "infra"}
    secret = {"group": "", "kind": "Secret"}
    assert gwcheck([gw], grants=[grant("certs", [sender], [secret])])["findings"] == []
    assert gwcheck([gw], grants=[grant("certs", [sender], [{**secret, "name": "cert"}])])["findings"] == []
    for wrong in (grant("certs", [sender], [{**secret, "name": "other"}]), grant("certs", [{**sender, "namespace": "x"}], [secret]),
                  grant("certs", [{**sender, "kind": "HTTPRoute"}], [secret]), grant("elsewhere", [sender], [secret]),
                  grant("certs", [sender], [{"group": "", "kind": "ConfigMap"}])):
        assert ids(gwcheck([gw], grants=[wrong])) == ["GRANT-UNUSED", "GW-CERT-NO-GRANT"]


def test_gateway_route_parent_resolution_and_acceptance():
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP"), listener("tcp", 9000, "TCP")])
    cases = {
        "missing-gateway": ([{"name": "nope", "namespace": "infra"}], "ROUTE-PARENT-MISSING"),
        "missing-section": ([{"name": "edge", "namespace": "infra", "sectionName": "web"}], "ROUTE-SECTION-MISSING"),
        "wrong-protocol": ([{"name": "edge", "namespace": "infra", "sectionName": "tcp"}], "ROUTE-NOT-ACCEPTED"),
        "other-namespace": ([{"name": "edge", "namespace": "infra"}], "ROUTE-NOT-ACCEPTED"),
        "port-mismatch": ([{"name": "edge", "namespace": "infra", "port": 8080}], "ROUTE-NOT-ACCEPTED"),
    }
    for label, (parents, expected) in cases.items():
        result = gwcheck([gw], [route("r", "shop", parents)])
        assert ids(result) == [expected], label
        assert result["unattached_routes"] == ["shop/r"], label
    accepted = gwcheck([gw], [route("r", "infra", [{"name": "edge", "sectionName": "http"}])])
    assert accepted["findings"] == [] and accepted["unattached_routes"] == []
    summary = found(gwcheck([gw], [route("r", "shop", [{"name": "edge", "namespace": "infra"}])]), "ROUTE-NOT-ACCEPTED")[0]["summary"]
    assert summary == ("shop/r: Gateway infra/edge accepts the route on no listener (listener http: allowedRoutes allows only namespace infra; "
                       "listener tcp: protocol TCP cannot carry an HTTPRoute)")
    assert ids(gwcheck([gw], [res("r", "infra", {"rules": []})])) == ["ROUTE-NO-PARENT"]
    mesh_parent = [{"group": "", "kind": "Service", "name": "svc"}]
    assert gwcheck([gw], [route("r", "infra", mesh_parent)])["findings"] == []


def test_gateway_route_namespace_scope_and_listener_kinds():
    selector = {"namespaces": {"from": "Selector", "selector": {"matchLabels": {"team": "a"}}}}
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP", allowedRoutes=selector)])
    parents = [{"name": "edge", "namespace": "infra"}]
    namespaces = [{"name": "shop", "labels": {"team": "a"}}, {"name": "other", "labels": {"team": "b"}}]
    assert gwcheck([gw], [route("r", "shop", parents)], namespaces=namespaces)["findings"] == []
    assert ids(gwcheck([gw], [route("r", "other", parents)], namespaces=namespaces)) == ["ROUTE-NOT-ACCEPTED"]
    by_name = {"namespaces": {"from": "Selector", "selector": {"matchLabels": {"kubernetes.io/metadata.name": "shop"}}}}
    gw_by_name = gateway("edge", "infra", [listener("http", 80, "HTTP", allowedRoutes=by_name)])
    assert gwcheck([gw_by_name], [route("r", "shop", parents)])["findings"] == []
    assert ids(gwcheck([gw_by_name], [route("r", "other", parents)])) == ["ROUTE-NOT-ACCEPTED"]
    kinds = gateway("edge", "infra", [listener("http", 80, "HTTP", allowedRoutes={"kinds": [{"kind": "TCPRoute"}]})])
    assert ids(gwcheck([kinds], [route("r", "infra", parents)])) == ["ROUTE-NOT-ACCEPTED"]
    allowed = gateway("edge", "infra", [listener("http", 80, "HTTP", allowedRoutes={"kinds": [{"kind": "HTTPRoute"}], "namespaces": {"from": "All"}})])
    assert ids(gwcheck([allowed], [route("r", "shop", parents)])) == ["GW-ROUTES-ALL-NAMESPACES"]


def test_gateway_hostname_intersection_pinned():
    def accepted(listener_host, route_hosts):
        gw = gateway("edge", "infra", [listener("h", 80, "HTTP", hostname=listener_host)] if listener_host else [listener("h", 80, "HTTP")])
        return gwcheck([gw], [route("r", "infra", [{"name": "edge"}], hostnames=route_hosts)])["unattached_routes"] == []
    assert accepted("shop.example.com", ["shop.example.com"])
    assert accepted("*.example.com", ["shop.example.com"]) and accepted("*.example.com", ["a.b.example.com"])
    assert accepted("shop.example.com", ["*.example.com"]) and accepted("*.example.com", ["*.b.example.com"])
    assert accepted("*.b.example.com", ["*.example.com"])
    assert not accepted("*.example.com", ["example.com"]) and not accepted("shop.example.com", ["other.example.com"])
    assert not accepted("*.example.com", ["*.example.org"])
    assert accepted(None, ["anything.test"]) and accepted("shop.example.com", None) and accepted("shop.example.com", [])
    assert accepted("a.example.com", ["b.example.com", "a.example.com"])


def test_gateway_backend_refs_ports_weights_and_cross_namespace_grants():
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP")])
    parents = [{"name": "edge"}]

    def backends(*refs):
        return gwcheck([gw], [route("r", "infra", parents, [{"backendRefs": list(refs)}])])
    assert ids(backends({"name": "web"})) == ["ROUTE-BACKEND-NO-PORT"]
    assert backends({"name": "web", "port": 80, "weight": 0}, {"name": "b", "port": 80})["findings"] == []
    assert ids(backends({"name": "web", "port": 80, "weight": 0})) == ["ROUTE-WEIGHT-ZERO"]
    assert backends({"name": "x", "kind": "ServiceImport", "group": "multicluster.x-k8s.io"})["findings"] == []
    cross = {"name": "db", "namespace": "data", "port": 5432}
    assert [f["summary"] for f in found(backends(cross), "ROUTE-BACKEND-NO-GRANT")] == [
        "infra/r: backend data/db needs a ReferenceGrant in data allowing HTTPRoutes from infra"]
    sender = {"group": GW_GROUP, "kind": "HTTPRoute", "namespace": "infra"}
    allow = grant("data", [sender], [{"group": "", "kind": "Service"}])
    assert gwcheck([gw], [route("r", "infra", parents, [{"backendRefs": [cross]}])], [allow])["findings"] == []
    named = grant("data", [sender], [{"group": "", "kind": "Service", "name": "other"}])
    assert ids(gwcheck([gw], [route("r", "infra", parents, [{"backendRefs": [cross]}])], [named])) == ["GRANT-UNUSED", "ROUTE-BACKEND-NO-GRANT"]
    other_kind = {"name": "x", "namespace": "data", "kind": "ServiceImport", "group": "multicluster.x-k8s.io"}
    assert ids(gwcheck([gw], [route("r", "infra", parents, [{"backendRefs": [other_kind]}])], [allow])) == ["GRANT-UNUSED", "ROUTE-BACKEND-NO-GRANT"]


def test_gateway_rules_without_backends_and_empty_grants():
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP")])
    rules = [{}, {"filters": [{"type": "RequestRedirect"}]}, {"filters": [{"type": "RequestHeaderModifier"}]}]
    result = gwcheck([gw], [route("r", "infra", [{"name": "edge"}], rules)])
    assert [f["summary"] for f in found(result, "ROUTE-RULE-NO-BACKEND")] == [
        "infra/r: rules[0] has no backendRefs and no RequestRedirect (requests get a 500)",
        "infra/r: rules[2] has no backendRefs and no RequestRedirect (requests get a 500)"]
    empty = gwcheck([gw], grants=[grant("n", [], [{"group": "", "kind": "Service"}])])
    assert ids(empty) == ["GRANT-EMPTY", "GRANT-UNUSED"] and severity(empty, "GRANT-EMPTY") == "major"


def test_gateway_invalid_inputs_name_the_field():
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP")])
    invalid("mesh_gateway_api_check", {"gateways": [gw], "routes": [res("r", "n", {"parentRefs": [{}]})], "reference_grants": []}, "routes")
    invalid("mesh_gateway_api_check", {"gateways": [gw], "routes": [route("r", "infra", [{"name": "edge"}], [{"backendRefs": [{"name": "w", "port": 1, "weight": -1}]}])],
                                       "reference_grants": []}, "routes")
    invalid("mesh_gateway_api_check", {"gateways": [gw], "routes": [], "reference_grants": [res("g", "n", {"from": [{"kind": "X"}], "to": []})]}, "reference_grants")
    invalid("mesh_gateway_api_check", {"gateways": [gw], "routes": [], "reference_grants": [], "namespaces": [{"labels": {}}]}, "namespaces")
    invalid("mesh_gateway_api_check", {"gateways": [gateway("g", "n", [listener("a", True, "HTTP")])], "routes": [], "reference_grants": []}, "gateways")
    invalid("mesh_gateway_api_check", {"gateways": [gateway("g", "n", [listener("a", 70000, "HTTP")])], "routes": [], "reference_grants": []}, "gateways")


def test_gateway_route_parent_namespace_must_be_a_string():
    # a list parentRefs.namespace was an unhashable Gateway lookup key (a crash the registry reported as a dependency outage)
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP")])
    invalid("mesh_gateway_api_check", {"gateways": [gw], "routes": [route("r", "app", [{"name": "edge", "namespace": ["infra"]}])],
                                       "reference_grants": []}, "routes")


def test_gateway_listener_hostname_must_be_a_string():
    # a non-string hostname reached the RFC 1123 check, which called string methods on it (a crash)
    invalid("mesh_gateway_api_check", {"gateways": [gateway("edge", "infra", [listener("http", 80, "HTTP", hostname=5)])],
                                       "routes": [], "reference_grants": []}, "gateways")


def test_gateway_listener_protocol_must_be_a_string():
    # a list protocol was an unhashable member of a port group's protocol set (a crash); a valid string protocol stays valid
    invalid("mesh_gateway_api_check", {"gateways": [gateway("edge", "infra", [listener("a", 80, ["HTTP"]), listener("b", 80, "HTTP")])],
                                       "routes": [], "reference_grants": []}, "gateways")
    invalid("mesh_gateway_api_check", {"gateways": [gateway("edge", "infra", [listener("a", 80, ["HTTP"])])], "routes": [],
                                       "reference_grants": []}, "gateways")
    assert not found(gwcheck(gateways=[gateway("edge", "infra", [listener("http", 80, "HTTP")])]), "GW-LISTENER-PROTOCOL")


# --- mesh_netpol_coverage -------------------------------------------------------------------------------------


def netpol(name, namespace, selector=None, types=None, ingress=None, egress=None) -> dict:
    spec: dict = {"podSelector": selector if selector is not None else {}}
    if types is not None:
        spec["policyTypes"] = types
    if ingress is not None:
        spec["ingress"] = ingress
    if egress is not None:
        spec["egress"] = egress
    return res(name, namespace, spec)


def pod(namespace, name, ip=None, ports=None, **labels) -> dict:
    out = {"namespace": namespace, "name": name, "labels": labels}
    if ip:
        out["ip"] = ip
    if ports:
        out["ports"] = ports
    return out


def coverage(policies, pods, **extra) -> dict:
    return ok("mesh_netpol_coverage", {"network_policies": policies, "pods": pods, **extra})


def test_netpol_full_default_deny_covers_both_directions():
    result = coverage([netpol("deny", "a", types=["Ingress", "Egress"])], [pod("a", "p1", app="x"), pod("a", "p2")])
    assert result["namespaces"] == [{"namespace": "a", "pod_count": 2, "default_deny_ingress": True, "ingress_open_pods": 0,
                                     "default_deny_egress": True, "egress_open_pods": 0}]
    assert result["uncovered_namespaces"] == [] and result["findings"] == [] and result["verdict"] == "pass"
    assert (result["namespace_count"], result["pod_count"]) == (1, 2)


def test_netpol_default_policy_types_follow_kubernetes_semantics():
    ingress_only = coverage([netpol("deny", "a")], [pod("a", "p")])
    assert ingress_only["namespaces"][0]["default_deny_ingress"] is True and ingress_only["namespaces"][0]["default_deny_egress"] is False
    assert ids(ingress_only) == ["NETPOL-NO-DEFAULT-DENY-EGRESS"] and severity(ingress_only, "NETPOL-NO-DEFAULT-DENY-EGRESS") == "minor"
    with_egress_rules = coverage([netpol("allow-dns", "a", egress=[{"ports": [{"port": 53, "protocol": "UDP"}]}])], [pod("a", "p")])
    row = with_egress_rules["namespaces"][0]
    # Ingress is always a policy type, so this policy also denies all ingress; the egress rules allow only DNS: no egress default-deny
    assert row["default_deny_ingress"] is True and row["default_deny_egress"] is False and row["egress_open_pods"] == 0
    egress_only = coverage([netpol("deny-out", "a", types=["Egress"])], [pod("a", "p")])
    assert egress_only["namespaces"][0]["default_deny_egress"] is True and egress_only["namespaces"][0]["default_deny_ingress"] is False


def test_netpol_default_deny_needs_empty_selector_and_no_rules():
    selective = coverage([netpol("deny-x", "a", {"matchLabels": {"app": "x"}})], [pod("a", "p", app="x"), pod("a", "q")])
    assert selective["namespaces"][0]["default_deny_ingress"] is False and selective["namespaces"][0]["ingress_open_pods"] == 1
    assert severity(selective, "NETPOL-NO-DEFAULT-DENY-INGRESS") == "major" and selective["uncovered_namespaces"] == ["a"]
    with_rule = coverage([netpol("allow", "a", ingress=[{"from": [{"podSelector": {}}]}])], [pod("a", "p")])
    assert with_rule["namespaces"][0]["default_deny_ingress"] is False
    expression = {"matchExpressions": [{"key": "app", "operator": "Exists"}]}
    assert coverage([netpol("deny-e", "a", expression)], [pod("a", "p")])["namespaces"][0]["default_deny_ingress"] is False
    assert coverage([netpol("deny-e", "a", {"matchLabels": {}})], [pod("a", "p")])["namespaces"][0]["default_deny_ingress"] is True


def test_netpol_isolated_pods_without_default_deny_are_info_only():
    result = coverage([netpol("x", "a", {"matchLabels": {"app": "x"}}, ingress=[{"from": [{"podSelector": {}}]}], types=["Ingress", "Egress"])],
                      [pod("a", "p", app="x")])
    assert result["namespaces"][0]["ingress_open_pods"] == 0 and result["namespaces"][0]["egress_open_pods"] == 0
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("NETPOL-NO-DEFAULT-DENY-EGRESS", "info"), ("NETPOL-NO-DEFAULT-DENY-INGRESS", "info")]
    assert result["verdict"] == "pass" and result["uncovered_namespaces"] == ["a"]


def test_netpol_allow_all_rule_reopens_pods_despite_default_deny():
    policies = [netpol("deny", "a", types=["Ingress", "Egress"]), netpol("open-in", "a", {"matchLabels": {"app": "x"}}, ingress=[{}]),
                netpol("open-out", "a", {}, types=["Egress"], egress=[{"to": []}])]
    result = coverage(policies, [pod("a", "p", app="x"), pod("a", "q")])
    row = result["namespaces"][0]
    assert (row["ingress_open_pods"], row["egress_open_pods"]) == (1, 2)
    assert ids(result) == ["NETPOL-ALLOW-ALL-EGRESS", "NETPOL-ALLOW-ALL-INGRESS", "NETPOL-DEFAULT-DENY-OVERRIDDEN-EGRESS",
                           "NETPOL-DEFAULT-DENY-OVERRIDDEN-INGRESS"]
    assert severity(result, "NETPOL-DEFAULT-DENY-OVERRIDDEN-INGRESS") == "major" and severity(result, "NETPOL-ALLOW-ALL-INGRESS") == "minor"
    ports_only = coverage([netpol("deny", "a", types=["Ingress"]), netpol("web", "a", ingress=[{"ports": [{"port": 80}]}])], [pod("a", "p")])
    assert ports_only["namespaces"][0]["ingress_open_pods"] == 0 and "NETPOL-ALLOW-ALL-INGRESS" not in ids(ports_only)


def test_netpol_port_less_entries_admit_every_port_and_coverage_agrees_with_reachability():
    # Kubernetes NetworkPolicyPort: an entry without `port` allows every port of its protocol, so `[{}]` re-opens the pod
    allow_all = netpol("allow-all-ports", "shop", {"matchLabels": {"app": "api"}}, ["Ingress"], ingress=[{"ports": [{}]}])
    result = coverage([allow_all], [pod("shop", "api-0", app="api")])
    assert result["namespaces"][0]["ingress_open_pods"] == 1 and severity(result, "NETPOL-ALLOW-ALL-INGRESS") == "minor"
    assert severity(result, "NETPOL-NO-DEFAULT-DENY-INGRESS") == "major" and result["verdict"] == "fail"
    assert reach([allow_all], source=WEB, destination=API)["reachable"] is True
    tcp_all = netpol("tcp-all", "shop", {"matchLabels": {"app": "api"}}, ["Ingress"], ingress=[{"ports": [{"protocol": "TCP"}]}])
    assert coverage([tcp_all], [pod("shop", "api-0", app="api")])["namespaces"][0]["ingress_open_pods"] == 1
    ported = netpol("web", "shop", {"matchLabels": {"app": "api"}}, ["Ingress"], ingress=[{"ports": [{"port": 8080}]}])
    assert coverage([ported], [pod("shop", "api-0", app="api")])["namespaces"][0]["ingress_open_pods"] == 0
    cidr = netpol("world", "shop", {"matchLabels": {"app": "api"}}, ["Egress"], egress=[{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}], "ports": [{}]}])
    assert found(coverage([cidr], [pod("shop", "api-0", app="api")]), "NETPOL-ALLOW-ANY-CIDR-EGRESS")


def test_netpol_missing_policies_and_extra_namespaces():
    result = coverage([], [pod("a", "p")], namespaces=["empty"])
    assert [(r["namespace"], r["pod_count"], r["ingress_open_pods"]) for r in result["namespaces"]] == [("a", 1, 1), ("empty", 0, 0)]
    assert result["uncovered_namespaces"] == ["a", "empty"]
    assert [f["summary"] for f in found(result, "NETPOL-NO-DEFAULT-DENY-INGRESS")] == [
        "a: no default-deny ingress policy (empty podSelector, Ingress type, no rules); 1 of 1 known pod(s) are open",
        "empty: no default-deny ingress policy (empty podSelector, Ingress type, no rules); 0 of 0 known pod(s) are open"]
    assert all(f["severity"] == "major" for f in found(result, "NETPOL-NO-DEFAULT-DENY-INGRESS"))
    other_namespace = coverage([netpol("deny", "a")], [pod("b", "p")])
    assert other_namespace["uncovered_namespaces"] == ["b"] and other_namespace["namespace_count"] == 2


def test_netpol_require_egress_raises_missing_egress_default_deny_to_major():
    policies = [netpol("deny", "a")]
    assert coverage(policies, [pod("a", "p")])["verdict"] == "warn"
    strict = coverage(policies, [pod("a", "p")], require_egress=True)
    assert severity(strict, "NETPOL-NO-DEFAULT-DENY-EGRESS") == "major" and strict["verdict"] == "fail"


def test_netpol_invalid_inputs_name_the_field():
    invalid("mesh_netpol_coverage", {"network_policies": [res("p", "a", {})], "pods": []}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [netpol("p", "a", types=["Sideways"])], "pods": []}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [netpol("p", "a", types=[])], "pods": []}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [netpol("p", "a", ingress="all")], "pods": []}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [netpol("p", "a", {"matchExpressions": [{"key": "k", "operator": "Like", "values": ["v"]}]})],
                                     "pods": [pod("a", "p")]}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [netpol("p", "a", {"matchExpressions": [{"key": "k", "operator": "In"}]})],
                                     "pods": [pod("a", "p")]}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [netpol("p", "a", {"matchExpressions": [{"key": "k", "operator": "Exists", "values": ["v"]}]})],
                                     "pods": [pod("a", "p")]}, "network_policies")
    invalid("mesh_netpol_coverage", {"network_policies": [], "pods": [{"namespace": "a"}]}, "pods")
    many = [pod(f"ns-{i}", "p") for i in range(201)]
    invalid("mesh_netpol_coverage", {"network_policies": [], "pods": many}, "network_policies")
    assert ok("mesh_netpol_coverage", {"network_policies": [], "pods": many[:200]})["namespace_count"] == 200


def test_netpol_selector_operators_pinned():
    def selected(selector, **labels):
        policies = [netpol("deny", "a", selector, ingress=[{"from": [{"podSelector": {}}]}])]
        return coverage(policies, [pod("a", "p", **labels)])["namespaces"][0]["ingress_open_pods"] == 0
    assert selected({"matchExpressions": [{"key": "tier", "operator": "In", "values": ["web", "api"]}]}, tier="api")
    assert not selected({"matchExpressions": [{"key": "tier", "operator": "In", "values": ["web"]}]}, tier="db")
    assert not selected({"matchExpressions": [{"key": "tier", "operator": "In", "values": ["web"]}]})
    assert selected({"matchExpressions": [{"key": "tier", "operator": "NotIn", "values": ["web"]}]}, tier="db")
    assert selected({"matchExpressions": [{"key": "tier", "operator": "NotIn", "values": ["web"]}]})
    assert not selected({"matchExpressions": [{"key": "tier", "operator": "NotIn", "values": ["web"]}]}, tier="web")
    assert selected({"matchExpressions": [{"key": "tier", "operator": "Exists"}]}, tier="x")
    assert not selected({"matchExpressions": [{"key": "tier", "operator": "Exists"}]})
    assert selected({"matchExpressions": [{"key": "tier", "operator": "DoesNotExist"}]})
    assert not selected({"matchExpressions": [{"key": "tier", "operator": "DoesNotExist"}]}, tier="x")
    assert not selected({"matchLabels": {"app": "x"}, "matchExpressions": [{"key": "tier", "operator": "Exists"}]}, tier="x")
    assert selected({"matchLabels": {"app": "x"}, "matchExpressions": [{"key": "tier", "operator": "Exists"}]}, tier="x", app="x")


# --- mesh_netpol_reachability ---------------------------------------------------------------------------------


WEB = pod("shop", "web-0", "10.0.0.5", app="web")
API = pod("shop", "api-0", "10.0.0.6", [{"name": "http", "containerPort": 8080}], app="api")
DB = pod("shop", "db-0", "10.0.0.7", app="db")
PROM = pod("mon", "prom-0", "10.1.0.1", app="prom")
PODS = [WEB, API, DB, PROM]


def ref(p: dict) -> dict:
    return {"namespace": p["namespace"], "name": p["name"]}


def reach(policies, source=WEB, destination=API, port=8080, protocol=None, pods=PODS, namespaces=None) -> dict:
    payload = {"network_policies": policies, "pods": pods, "source": ref(source), "destination": ref(destination), "port": port}
    if protocol:
        payload["protocol"] = protocol
    if namespaces is not None:
        payload["namespaces"] = namespaces
    return ok("mesh_netpol_reachability", payload)


def ingress_to_api(*rules, name="api-in") -> dict:
    return netpol(name, "shop", {"matchLabels": {"app": "api"}}, ["Ingress"], ingress=list(rules))


def egress_from_web(*rules) -> dict:
    return netpol("web-out", "shop", {"matchLabels": {"app": "web"}}, ["Egress"], egress=list(rules))


def from_pod(app, **extra) -> dict:
    return {"from": [{"podSelector": {"matchLabels": {"app": app}}}], **extra}


def test_reachability_without_policies_is_open_in_both_directions():
    result = reach([])
    assert result == {"state": "SUCCESS", "reachable": True, "verdict": "reachable", "egress_isolated": False, "egress_allowed": True,
                      "ingress_isolated": False, "ingress_allowed": True, "egress_policies": [], "ingress_policies": [],
                      "reason": "allowed: no policy isolates either pod", "notes": []}


def test_reachability_ingress_policy_allows_only_the_named_peer_and_port():
    policy = ingress_to_api(from_pod("web", ports=[{"port": 8080}]))
    allowed = reach([policy])
    assert allowed["reachable"] is True and allowed["ingress_policies"] == ["shop/api-in"] and allowed["ingress_isolated"] is True
    assert allowed["reason"] == "allowed: every isolating policy has a matching rule" and allowed["egress_isolated"] is False
    wrong_port = reach([policy], port=9090)
    assert wrong_port["reachable"] is False and wrong_port["verdict"] == "blocked" and wrong_port["egress_allowed"] is True
    assert wrong_port["reason"] == "blocked: ingress of shop/api-0 has no rule for shop/web-0 on TCP/9090"
    wrong_peer = reach([policy], source=DB)
    assert wrong_peer["reachable"] is False and wrong_peer["ingress_policies"] == []
    assert reach([ingress_to_api()])["reachable"] is False  # isolated, no rules: deny all
    assert reach([ingress_to_api({})])["reachable"] is True  # an empty rule allows everything


def test_reachability_a_policy_selecting_other_pods_or_directions_does_not_isolate():
    other_pod = netpol("db-in", "shop", {"matchLabels": {"app": "db"}}, ["Ingress"], ingress=[])
    egress_only = netpol("api-out", "shop", {"matchLabels": {"app": "api"}}, ["Egress"], egress=[])
    other_namespace = netpol("deny", "mon", {}, ["Ingress"], ingress=[])
    result = reach([other_pod, egress_only, other_namespace])
    assert result["reachable"] is True and result["ingress_isolated"] is False and result["egress_isolated"] is False


def test_reachability_policies_are_additive():
    first, second = ingress_to_api(from_pod("db"), name="first"), ingress_to_api(from_pod("web"), name="second")
    result = reach([first, second])
    assert result["reachable"] is True and result["ingress_policies"] == ["shop/second"]
    both = reach([ingress_to_api(from_pod("web"), name="b"), ingress_to_api({"from": [{"podSelector": {}}]}, name="a")])
    assert both["ingress_policies"] == ["shop/a", "shop/b"]


def test_reachability_egress_side_must_allow_too():
    policy = egress_from_web({"to": [{"podSelector": {"matchLabels": {"app": "api"}}}], "ports": [{"port": 8080}]})
    assert reach([policy])["reachable"] is True
    blocked = reach([policy], destination=DB, port=5432)
    assert blocked["reachable"] is False and blocked["egress_isolated"] is True and blocked["egress_allowed"] is False
    assert blocked["reason"] == "blocked: egress of shop/web-0 has no rule for shop/db-0 on TCP/5432"
    both = reach([policy, ingress_to_api()])
    assert both["reason"] == ("blocked: ingress of shop/api-0 has no rule for shop/web-0 on TCP/8080")
    neither = reach([egress_from_web(), ingress_to_api()])
    assert neither["reason"].startswith("blocked: egress of shop/web-0") and "; ingress of shop/api-0" in neither["reason"]


def test_reachability_namespace_selectors_and_bare_pod_selectors():
    by_name = {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "mon"}}}]}
    assert reach([ingress_to_api(by_name)], source=PROM)["reachable"] is True
    assert reach([ingress_to_api(by_name)], source=WEB)["reachable"] is False
    by_label = {"from": [{"namespaceSelector": {"matchLabels": {"team": "ops"}}}]}
    assert reach([ingress_to_api(by_label)], source=PROM)["reachable"] is False
    labelled = [{"name": "mon", "labels": {"team": "ops"}}]
    assert reach([ingress_to_api(by_label)], source=PROM, namespaces=labelled)["reachable"] is True
    combined = {"from": [{"namespaceSelector": {"matchLabels": {"team": "ops"}}, "podSelector": {"matchLabels": {"app": "prom"}}}]}
    assert reach([ingress_to_api(combined)], source=PROM, namespaces=labelled)["reachable"] is True
    assert reach([ingress_to_api(from_pod("prom"))], source=PROM)["reachable"] is False  # bare podSelector: policy namespace only
    separate = {"from": [{"namespaceSelector": {"matchLabels": {"team": "ops"}}}, {"podSelector": {"matchLabels": {"app": "web"}}}]}
    assert reach([ingress_to_api(separate)], source=WEB, namespaces=labelled)["reachable"] is True


def test_reachability_port_matching_pinned():
    def allowed(ports, port, protocol=None, destination=API):
        return reach([ingress_to_api({"ports": ports})], port=port, protocol=protocol, destination=destination)["reachable"]
    assert allowed([{"port": 80}], 80) and not allowed([{"port": 80}], 81)
    assert allowed([{"port": 8000, "endPort": 8100}], 8000) and allowed([{"port": 8000, "endPort": 8100}], 8100)
    assert not allowed([{"port": 8000, "endPort": 8100}], 7999) and not allowed([{"port": 8000, "endPort": 8100}], 8101)
    assert allowed([{"port": 80}], 80, "TCP") and not allowed([{"port": 80}], 80, "UDP") and not allowed([{"port": 80, "protocol": "UDP"}], 80)
    assert allowed([{"port": 53, "protocol": "UDP"}], 53, "UDP") and not allowed([{"port": 53, "protocol": "UDP"}], 53, "SCTP")
    assert allowed([{"protocol": "TCP"}], 1234) and not allowed([{"protocol": "UDP"}], 1234)
    assert allowed([{"port": 1}, {"port": 2}], 2) and allowed([], 9)
    assert allowed([{"port": "http"}], 8080) and not allowed([{"port": "http"}], 9090) and not allowed([{"port": "metrics"}], 8080)
    unnamed = pod("shop", "api-0", "10.0.0.6", app="api")
    no_ports = reach([ingress_to_api({"ports": [{"port": "http"}]})], pods=[WEB, unnamed, DB, PROM])
    assert no_ports["reachable"] is False  # a named port needs a matching container port on the destination pod
    udp_only = pod("shop", "api-0", "10.0.0.6", [{"name": "http", "containerPort": 8080, "protocol": "UDP"}], app="api")
    assert reach([ingress_to_api({"ports": [{"port": "http"}]})], pods=[WEB, udp_only, DB, PROM])["reachable"] is False
    assert reach([ingress_to_api({"ports": [{"port": "http", "protocol": "UDP"}]})], protocol="UDP", pods=[WEB, udp_only, DB, PROM])["reachable"] is True


def test_reachability_ip_blocks_match_pod_ips_minus_except():
    block = lambda cidr, *exclude: {"from": [{"ipBlock": {"cidr": cidr, **({"except": list(exclude)} if exclude else {})}}]}  # noqa: E731
    first = reach([ingress_to_api(block("10.0.0.0/24"))])
    assert first["reachable"] is True and first["notes"] == [
        "ipBlock evaluated against a pod ip: Kubernetes documents ipBlock for cluster-external addresses and CNI plugins differ"]
    assert reach([ingress_to_api(block("10.0.0.0/24", "10.0.0.5/32"))])["reachable"] is False
    assert reach([ingress_to_api(block("10.0.0.0/24", "10.0.0.5/32"))], source=DB)["reachable"] is True
    assert reach([ingress_to_api(block("10.1.0.0/16"))])["reachable"] is False
    assert reach([ingress_to_api(block("fd00::/8"))])["reachable"] is False
    assert reach([ingress_to_api(block("0.0.0.0/0"))], source=PROM)["reachable"] is True
    anonymous = pod("shop", "web-0", app="web")
    result = reach([ingress_to_api(block("10.0.0.0/24"))], pods=[anonymous, API, DB, PROM])
    assert result["reachable"] is False and result["notes"] == ["ipBlock peer not evaluated: pod shop/web-0 has no ip"]
    egress = egress_from_web({"to": [{"ipBlock": {"cidr": "10.0.0.0/24"}}]})
    assert reach([egress])["reachable"] is True and reach([egress], destination=PROM, port=80)["reachable"] is False


def test_reachability_invalid_inputs_name_the_field():
    base = {"network_policies": [], "pods": PODS, "source": ref(WEB), "destination": ref(API), "port": 80}
    invalid("mesh_netpol_reachability", {**base, "source": {"namespace": "shop", "name": "ghost"}}, "source")
    invalid("mesh_netpol_reachability", {**base, "destination": {"namespace": "x", "name": "api-0"}}, "destination")
    invalid("mesh_netpol_reachability", {**base, "destination": ref(WEB)}, "destination")
    invalid("mesh_netpol_reachability", {**base, "protocol": "ICMP"}, "protocol")
    invalid("mesh_netpol_reachability", {**base, "network_policies": [ingress_to_api({"from": [{}]})]}, "network_policies")
    invalid("mesh_netpol_reachability", {**base, "network_policies": [ingress_to_api({"from": [{"ipBlock": {"cidr": "10.0.0.0/8"}, "podSelector": {}}]})]},
            "network_policies")
    invalid("mesh_netpol_reachability", {**base, "network_policies": [ingress_to_api({"from": [{"ipBlock": {"cidr": "not-a-cidr"}}]})]}, "network_policies")
    invalid("mesh_netpol_reachability", {**base, "network_policies": [ingress_to_api({"ports": [{"port": 80, "endPort": 70}]})]}, "network_policies")
    invalid("mesh_netpol_reachability", {**base, "network_policies": [ingress_to_api({"ports": [{"port": 1.5}]})]}, "network_policies")
    invalid("mesh_netpol_reachability", {**base, "network_policies": [ingress_to_api({"ports": [{"port": 80, "protocol": "ICMP"}]})]}, "network_policies")
    bad_ip = {**base, "network_policies": [ingress_to_api({"from": [{"ipBlock": {"cidr": "10.0.0.0/8"}}]})], "pods": [pod("shop", "web-0", "nope", app="web"), API]}
    invalid("mesh_netpol_reachability", bad_ip, "pods")


# --- mesh_retry_amplification ---------------------------------------------------------------------------------


def hop(caller, callee, retries, budget=None) -> dict:
    return {"caller": caller, "callee": callee, "retries": retries, **({"budget_percent": budget} if budget is not None else {})}


def amplification(calls, limit) -> dict:
    return ok("mesh_retry_amplification", {"calls": calls, "max_multiplier": limit})


def test_retry_amplification_multiplies_attempts_along_the_chain():
    result = amplification([hop("gw", "a", 2), hop("a", "b", 1)], 6)
    assert (result["hop_count"], result["worst_case_multiplier"], result["over_budget"]) == (2, 6.0, False)
    assert result["hops"] == [{"caller": "gw", "callee": "a", "retries": 2, "hop_multiplier": 3.0, "cumulative_load": 3.0},
                              {"caller": "a", "callee": "b", "retries": 1, "hop_multiplier": 2.0, "cumulative_load": 6.0}]
    assert ids(result) == ["RETRY-NESTED"] and result["verdict"] == "warn"


def test_retry_amplification_budget_boundary_is_strictly_greater():
    chain = [hop("gw", "a", 2), hop("a", "b", 1)]
    assert amplification(chain, 6)["over_budget"] is False
    over = amplification(chain, 5.99)
    assert over["over_budget"] is True and over["verdict"] == "fail"
    assert found(over, "RETRY-AMPLIFICATION")[0]["summary"] == ("load on b reaches the 5.99x budget at hop 2; the chain end sees 6.0x one client "
                                                              "request when every attempt fails")
    first = amplification(chain, 2.5)
    assert found(first, "RETRY-AMPLIFICATION")[0]["summary"].startswith("load on a reaches the 2.5x budget at hop 1")
    assert severity(first, "RETRY-AMPLIFICATION") == "major"


def test_retry_amplification_retry_budget_caps_the_extra_load_of_a_hop():
    result = amplification([hop("gw", "a", 2), hop("a", "b", 5, 20)], 100)
    assert [h["hop_multiplier"] for h in result["hops"]] == [3.0, 1.2] and result["worst_case_multiplier"] == 3.6
    assert "RETRY-NESTED" not in ids(result)
    assert amplification([hop("a", "b", 3, 100)], 100)["hops"][0]["hop_multiplier"] == 2.0
    assert amplification([hop("a", "b", 3, 0)], 100)["hops"][0]["hop_multiplier"] == 1.0
    assert amplification([hop("a", "b", 1, 250 / 10)], 100)["hops"][0]["hop_multiplier"] == 1.25
    assert amplification([hop("a", "b", 1, 80)], 100)["hops"][0]["hop_multiplier"] == 1.8
    assert amplification([hop("a", "b", 1, 100)], 100)["hops"][0]["hop_multiplier"] == 2.0


def test_retry_amplification_without_retries_is_neutral_and_nested_needs_two_unbudgeted_hops():
    quiet = amplification([hop("a", "b", 0), hop("b", "c", 0)], 1)
    assert (quiet["worst_case_multiplier"], quiet["over_budget"], quiet["verdict"]) == (1.0, False, "pass")
    assert "RETRY-NESTED" not in ids(amplification([hop("a", "b", 1), hop("b", "c", 0)], 10))
    assert found(amplification([hop("a", "b", 1), hop("b", "c", 1), hop("c", "d", 0)], 10), "RETRY-NESTED")[0]["summary"] == (
        "2 hops retry without a retry budget; layered retries multiply")


def test_retry_amplification_detects_cycles_and_stays_finite_on_long_chains():
    cyclic = amplification([hop("gw", "a", 0), hop("a", "gw", 0)], 10)
    assert [(f["id"], f["severity"], f["summary"]) for f in cyclic["findings"]] == [
        ("RETRY-CYCLE", "blocker", "gw appears twice on the call path; a retry loop multiplies without bound")]
    chain = [hop(f"s{i}", f"s{i + 1}", 10) for i in range(32)]
    long = amplification(chain, 1_000_000)
    assert long["over_budget"] is True and long["worst_case_multiplier"] == round(11.0 ** 32, 4)


def test_retry_amplification_rejects_broken_chains():
    invalid("mesh_retry_amplification", {"calls": [hop("a", "b", 1), hop("x", "c", 1)], "max_multiplier": 2}, "calls")
    invalid("mesh_retry_amplification", {"calls": [{"caller": "a", "callee": "b"}], "max_multiplier": 2}, "calls")
    invalid("mesh_retry_amplification", {"calls": [], "max_multiplier": 2}, "calls")
    invalid("mesh_retry_amplification", {"calls": [hop("a", "b", 1)], "max_multiplier": 0}, "max_multiplier")


# --- mesh_timeout_hierarchy -----------------------------------------------------------------------------------


def tcall(caller, callee, timeout, per_try=None, retries=None) -> dict:
    out = {"caller": caller, "callee": callee, "timeout_ms": timeout}
    if per_try is not None:
        out["per_try_timeout_ms"] = per_try
    if retries is not None:
        out["retries"] = retries
    return out


def hierarchy(calls, **extra) -> dict:
    return ok("mesh_timeout_hierarchy", {"calls": calls, **extra})


def test_timeout_hierarchy_decreasing_chain_passes_with_budget_numbers():
    result = hierarchy([tcall("gw", "a", 5000), tcall("a", "b", 2000), tcall("b", "c", 500)])
    assert (result["hop_count"], result["edge_budget_ms"], result["pair_count"], result["tightest_gap_ms"]) == (3, 5000, 2, 1500)
    assert result["findings"] == [] and result["verdict"] == "pass"
    single = hierarchy([tcall("gw", "a", 5000)])
    assert (single["pair_count"], single["tightest_gap_ms"], single["edge_budget_ms"]) == (0, 0, 5000)


def test_timeout_hierarchy_gap_boundaries_with_the_default_minimum():
    thin = lambda upstream: hierarchy([tcall("gw", "a", upstream), tcall("a", "b", 2000)])  # noqa: E731
    assert thin(2001)["findings"] == [] and thin(2001)["tightest_gap_ms"] == 1
    equal = thin(2000)
    assert ids(equal) == ["TIMEOUT-THIN-MARGIN"] and severity(equal, "TIMEOUT-THIN-MARGIN") == "minor"
    assert found(equal, "TIMEOUT-THIN-MARGIN")[0]["summary"] == "gw->a (2000ms) over b (2000ms): gap 0ms is below the required 1ms"
    inverted = thin(1999)
    assert ids(inverted) == ["TIMEOUT-INVERTED"] and severity(inverted, "TIMEOUT-INVERTED") == "major" and inverted["tightest_gap_ms"] == -1
    assert found(inverted, "TIMEOUT-INVERTED")[0]["summary"].startswith("gw->a (1999ms) over b (2000ms): the caller gives up 1ms before")


def test_timeout_hierarchy_min_gap_is_inclusive():
    chain = lambda upstream: [tcall("gw", "a", upstream), tcall("a", "b", 2000)]  # noqa: E731
    assert hierarchy(chain(2100), min_gap_ms=100)["findings"] == []
    assert ids(hierarchy(chain(2099), min_gap_ms=100)) == ["TIMEOUT-THIN-MARGIN"]
    assert ids(hierarchy(chain(1999), min_gap_ms=100)) == ["TIMEOUT-INVERTED"]


def test_timeout_hierarchy_uses_the_per_try_budget_of_the_caller():
    result = hierarchy([tcall("gw", "a", 10000, 1000, 2), tcall("a", "b", 1500)])
    assert ids(result) == ["TIMEOUT-INVERTED"] and result["tightest_gap_ms"] == -500
    ok_chain = hierarchy([tcall("gw", "a", 10000, 1000, 2), tcall("a", "b", 900)])
    assert ok_chain["findings"] == [] and ok_chain["tightest_gap_ms"] == 100


def test_timeout_hierarchy_per_try_above_the_total_is_capped_at_the_total():
    # the caller cannot wait longer than its total timeout: a 5000ms per-try on a 1000ms route is a 1000ms attempt
    result = hierarchy([tcall("gw", "a", 1000, 5000, 0), tcall("a", "b", 2000)])
    assert result["tightest_gap_ms"] == -1000 and result["verdict"] == "fail"
    assert ids(result) == ["TIMEOUT-INVERTED", "TIMEOUT-PERTRY-EXCEEDS-TOTAL"] and severity(result, "TIMEOUT-INVERTED") == "major"


def test_timeout_hierarchy_per_try_and_retry_rules():
    assert ids(hierarchy([tcall("a", "b", 1000, 1500)])) == ["TIMEOUT-PERTRY-EXCEEDS-TOTAL"]
    assert hierarchy([tcall("a", "b", 1000, 1000)])["findings"] == []
    assert ids(hierarchy([tcall("a", "b", 1000, None, 2)])) == ["TIMEOUT-RETRIES-NO-PERTRY"]
    assert hierarchy([tcall("a", "b", 1000, None, 0)])["findings"] == []
    assert hierarchy([tcall("a", "b", 1000, 500, 1)])["findings"] == []  # 2 x 500 == 1000
    over = hierarchy([tcall("a", "b", 1000, 501, 1)])
    assert ids(over) == ["TIMEOUT-RETRIES-EXCEED-TOTAL"] and found(over, "TIMEOUT-RETRIES-EXCEED-TOTAL")[0]["summary"] == (
        "a->b: 2 tries of 501ms cannot finish within 1000ms")


def test_timeout_hierarchy_tightest_gap_is_the_minimum_over_pairs():
    result = hierarchy([tcall("a", "b", 3000), tcall("b", "c", 2500), tcall("c", "d", 1000), tcall("d", "e", 990)])
    assert (result["pair_count"], result["tightest_gap_ms"]) == (3, 10)


def test_timeout_hierarchy_rejects_broken_chains():
    invalid("mesh_timeout_hierarchy", {"calls": [tcall("a", "b", 100), tcall("x", "c", 50)]}, "calls")
    invalid("mesh_timeout_hierarchy", {"calls": [{"caller": "a", "callee": "b"}]}, "calls")
    invalid("mesh_timeout_hierarchy", {"calls": [tcall("a", "b", 0)]}, "calls")
    invalid("mesh_timeout_hierarchy", {"calls": [tcall("a", "b", 10)], "min_gap_ms": 0}, "min_gap_ms")


# --- mesh_dns_ndots -------------------------------------------------------------------------------------------


SEARCH = ["shop.svc.cluster.local", "svc.cluster.local", "cluster.local"]


def dns(names, ndots=5, search=None, **extra) -> dict:
    return ok("mesh_dns_ndots", {"ndots": ndots, "namespace": "shop", "search_domains": SEARCH if search is None else search,
                                 "names": [{"name": n, "target": t} for n, t in names], **extra})


def lookups(name, target, ndots, search=None) -> int:
    return dns([(name, target)], ndots, search)["names"][0]["lookups"]


def test_dns_lookup_costs_at_the_kubernetes_default_ndots_5():
    result = dns([("api", "cluster"), ("api.shop", "cluster"), ("api.shop.svc", "cluster"), ("api.shop.svc.cluster.local", "cluster"),
                  ("api.example.com", "external"), ("a.b.c.d.e", "external"), ("a.b.c.d.e.f", "external"), ("example.com.", "external"),
                  ("10.0.0.1", "external"), ("localhost", "external")])
    assert [(r["name"], r["dots"], r["lookups"], r["wasted_lookups"]) for r in result["names"]] == [
        ("api", 0, 1, 0), ("api.shop", 1, 2, 1), ("api.shop.svc", 2, 3, 2), ("api.shop.svc.cluster.local", 4, 4, 3),
        ("api.example.com", 2, 4, 3), ("a.b.c.d.e", 4, 4, 3), ("a.b.c.d.e.f", 5, 1, 0), ("example.com.", 1, 1, 0),
        ("10.0.0.1", 0, 0, 0), ("localhost", 0, 4, 3)]
    assert (result["total_lookups"], result["wasted_lookups"], result["name_count"], result["search_domain_count"]) == (24, 15, 10, 3)


def test_dns_ndots_boundary_names_with_exactly_ndots_dots_resolve_as_written_first():
    assert lookups("a.b.c.d.e", "external", 4) == 1 and lookups("a.b.c.d.e", "external", 5) == 4
    assert lookups("a.b.c.d.e", "external", 6) == 4
    assert lookups("api.example.com", "external", 2) == 1 and lookups("api.example.com", "external", 3) == 4
    assert lookups("example.com", "external", 1) == 1 and lookups("example.com", "external", 2) == 4
    assert lookups("example.com", "external", 0) == 1


def test_dns_search_resolved_names_cost_one_extra_lookup_when_ndots_is_at_or_below_their_dots():
    assert lookups("api", "cluster", 1) == 1 and lookups("api", "cluster", 0) == 2
    assert lookups("api.shop", "cluster", 2) == 2 and lookups("api.shop", "cluster", 1) == 3 and lookups("api.shop", "cluster", 0) == 3
    assert lookups("api.shop.svc", "cluster", 3) == 3 and lookups("api.shop.svc", "cluster", 2) == 4
    assert lookups("api.shop.svc.cluster.local", "cluster", 4) == 1 and lookups("api.shop.svc.cluster.local", "cluster", 5) == 4
    assert lookups("api.example.com", "cluster", 5) == 4  # not a short service form: no search hit exists for it


def test_dns_non_default_search_list_disables_the_short_form_model():
    corp = ["corp.example.com", "svc.cluster.local", "cluster.local"]
    assert lookups("api", "cluster", 5, corp) == 4
    assert lookups("api.shop", "cluster", 5, corp) == 2
    assert lookups("api", "cluster", 5, []) == 1
    assert lookups("api.example.com", "external", 5, []) == 1
    custom = dns([("api", "cluster"), ("api.x", "cluster")], 5, ["x.svc.corp.local", "svc.corp.local", "corp.local"], namespace="x", cluster_domain="corp.local")
    assert [r["lookups"] for r in custom["names"]] == [1, 2]


def test_dns_best_ndots_is_the_smallest_with_the_fewest_lookups():
    result = dns([("api", "cluster"), ("api.shop", "cluster"), ("api.example.com", "external")])
    assert (result["total_lookups"], result["best_ndots"], result["best_total_lookups"]) == (7, 2, 4)
    assert found(result, "DNS-NDOTS-TUNE")[0]["summary"] == "ndots 2 would cut lookups for these names from 7 to 4"
    already = dns([("api", "cluster"), ("api.example.com", "external")], ndots=2)
    assert (already["total_lookups"], already["best_ndots"], already["best_total_lookups"]) == (2, 1, 2)
    assert "DNS-NDOTS-TUNE" not in ids(already)
    only_short = dns([("api", "cluster")], ndots=3)
    assert (only_short["best_ndots"], only_short["best_total_lookups"]) == (1, 1)


def test_dns_waste_findings():
    result = dns([("api.example.com", "external"), ("api.shop", "cluster"), ("api", "cluster")])
    assert [f["summary"] for f in found(result, "DNS-NDOTS-WASTE")] == [
        "api.example.com: 3 failed search-domain lookup(s) before it resolves (2 dots < ndots 5); write it with a trailing dot or lower ndots"]
    assert "DNS-SHORT-NAME-WASTE" not in ids(result)
    low = dns([("api.shop", "cluster")], ndots=1)
    assert [f["summary"] for f in found(low, "DNS-SHORT-NAME-WASTE")] == [
        "api.shop: ndots 1 makes the resolver try it as written first (2 wasted lookup(s)) before the search list"]
    assert severity(low, "DNS-SHORT-NAME-WASTE") == "minor" and low["verdict"] == "warn"
    clean = dns([("api", "cluster"), ("example.com.", "external")])
    assert clean["verdict"] == "pass" and clean["findings"] == []


def test_dns_search_list_limits_are_inclusive():
    many = [f"d{i}.example" for i in range(32)]
    assert "DNS-SEARCH-LIMIT" not in ids(dns([("a", "external")], search=many))
    flagged = dns([("a", "external")], search=many + ["extra.example"])
    assert severity(flagged, "DNS-SEARCH-LIMIT") == "major" and flagged["verdict"] == "fail"
    assert found(flagged, "DNS-SEARCH-LIMIT")[0]["summary"].startswith("search list has 33 domains / ")
    long_domains = ["a" * 253] * 8
    exact = long_domains + ["b" * 16]
    assert len(" ".join(exact)) == 2048 and "DNS-SEARCH-LIMIT" not in ids(dns([("a", "external")], search=exact))
    over = long_domains + ["b" * 17]
    assert len(" ".join(over)) == 2049 and "DNS-SEARCH-LIMIT" in ids(dns([("a", "external")], search=over))


def test_dns_invalid_inputs_name_the_field():
    for name in ("has space", "a..b", ".leading", "."):
        invalid("mesh_dns_ndots", {"ndots": 5, "namespace": "shop", "search_domains": SEARCH, "names": [{"name": name, "target": "external"}]}, "names")
    invalid("mesh_dns_ndots", {"ndots": 5, "namespace": "shop", "search_domains": SEARCH, "names": [{"name": "a", "target": "both"}]}, "names")
    invalid("mesh_dns_ndots", {"ndots": 5, "namespace": "shop", "search_domains": ["bad domain"], "names": [{"name": "a", "target": "external"}]}, "search_domains")
    invalid("mesh_dns_ndots", {"ndots": 5, "namespace": "shop", "search_domains": ["a..b"], "names": [{"name": "a", "target": "external"}]}, "search_domains")
    invalid("mesh_dns_ndots", {"ndots": 5, "namespace": "  ", "search_domains": SEARCH, "names": [{"name": "a", "target": "external"}]}, "namespace")
    invalid("mesh_dns_ndots", {"ndots": 16, "namespace": "shop", "search_domains": SEARCH, "names": [{"name": "a", "target": "external"}]}, "ndots")


# --- mesh_egress_coverage -------------------------------------------------------------------------------------


def service_entry(name, hosts, ports=((443, "TLS"),), namespace="payments", **spec) -> dict:
    return res(name, namespace, {"hosts": list(hosts), "ports": [{"number": n, "name": f"p{n}", "protocol": p} for n, p in ports], **spec})


def egress(destinations, entries, mode="REGISTRY_ONLY") -> dict:
    return ok("mesh_egress_coverage", {"destinations": destinations, "service_entries": entries, "outbound_policy": mode})


def test_egress_registered_destination_is_covered():
    result = egress([{"host": "api.stripe.com", "port": 443, "protocol": "TLS"}], [service_entry("stripe", ["api.stripe.com"], resolution="DNS")])
    assert (result["destination_count"], result["covered_count"], result["uncovered"], result["unused_entries"]) == (1, 1, [], [])
    assert result["findings"] == [] and result["verdict"] == "pass"


def test_egress_uncovered_destinations_depend_on_the_outbound_policy_mode():
    destinations = [{"host": "x.example.org", "port": 443}, {"host": "api.stripe.com", "port": 8443}]
    entries = [service_entry("stripe", ["api.stripe.com"])]
    blocked = egress(destinations, entries, "REGISTRY_ONLY")
    assert blocked["uncovered"] == ["api.stripe.com:8443", "x.example.org:443"] and blocked["covered_count"] == 0
    assert [(f["id"], f["severity"]) for f in blocked["findings"][:2]] == [("EGRESS-BLOCKED", "major")] * 2
    assert found(blocked, "EGRESS-BLOCKED")[0]["summary"] == "api.stripe.com:8443 has no ServiceEntry; REGISTRY_ONLY drops the traffic"
    allowed = egress(destinations, entries, "ALLOW_ANY")
    assert allowed["uncovered"] == blocked["uncovered"] and ids(allowed) == ["EGRESS-UNREGISTERED", "EGRESS-UNREGISTERED", "EGRESS-UNUSED-ENTRY"]
    assert [f["severity"] for f in found(allowed, "EGRESS-UNREGISTERED")] == ["minor", "minor"] and allowed["verdict"] == "warn"


def test_egress_host_pattern_matching_pinned():
    def covered(pattern, host):
        return egress([{"host": host, "port": 443}], [service_entry("e", [pattern])])["covered_count"] == 1
    assert covered("api.stripe.com", "api.stripe.com") and covered("API.Stripe.com", "api.STRIPE.com")
    assert covered("*.stripe.com", "api.stripe.com") and covered("*.stripe.com", "a.b.stripe.com")
    assert not covered("*.stripe.com", "stripe.com") and not covered("*.stripe.com", "api.notstripe.com") and not covered("*.stripe.com", "api.stripe.com.evil.io")
    assert not covered("api.stripe.com", "stripe.com") and covered("*", "anything.test")


def test_egress_port_must_be_declared():
    entries = [service_entry("e", ["a.example.com"], ((443, "TLS"), (8443, "TLS")))]
    assert egress([{"host": "a.example.com", "port": 8443}], entries)["covered_count"] == 1
    assert egress([{"host": "a.example.com", "port": 80}], entries)["covered_count"] == 0


def test_egress_overbroad_and_unresolvable_entries():
    result = egress([], [service_entry("all", ["*"], resolution="NONE"), service_entry("tld", ["*.com"], resolution="NONE"),
                         service_entry("ok", ["*.example.com"], resolution="NONE")])
    assert [(f["id"], f["severity"]) for f in result["findings"] if f["severity"] == "major"] == [("EGRESS-WILDCARD-ALL", "major"), ("EGRESS-WILDCARD-TLD", "major")]
    assert found(result, "EGRESS-WILDCARD-ALL")[0]["summary"] == 'payments/all: host "*" allows every external destination'
    assert found(result, "EGRESS-WILDCARD-TLD")[0]["summary"] == "payments/tld: host *.com covers a whole top-level domain"
    dns_wildcard = egress([], [service_entry("w", ["*.example.com"], resolution="DNS")])
    assert ids(dns_wildcard) == ["EGRESS-UNUSED-ENTRY", "SE-WILDCARD-DNS"] and severity(dns_wildcard, "SE-WILDCARD-DNS") == "major"
    for fine in ({"resolution": "DNS", "endpoints": [{"address": "1.2.3.4"}]}, {"resolution": "STATIC"}, {"resolution": "NONE"}, {}):
        assert "SE-WILDCARD-DNS" not in ids(egress([], [service_entry("w", ["*.example.com"], **fine)]))
    assert "SE-WILDCARD-DNS" in ids(egress([], [service_entry("w", ["*.example.com"], resolution="DNS_ROUND_ROBIN")]))
    assert ids(egress([], [service_entry("bad", ["a.example.com"], resolution="MAGIC")])) == ["EGRESS-UNUSED-ENTRY", "SE-RESOLUTION-INVALID"]


def test_egress_protocol_class_mismatch():
    def mismatch(destination_protocol, declared):
        destinations = [{"host": "a.example.com", "port": 443, **({"protocol": destination_protocol} if destination_protocol else {})}]
        return "EGRESS-PROTOCOL-MISMATCH" in ids(egress(destinations, [service_entry("e", ["a.example.com"], ((443, declared),))]))
    assert mismatch("HTTPS", "HTTP") and mismatch("TLS", "GRPC") and mismatch("HTTP", "TLS") and mismatch("HTTP2", "HTTPS")
    assert not mismatch("HTTPS", "TLS") and not mismatch("TLS", "HTTPS") and not mismatch("HTTP", "HTTP2") and not mismatch("GRPC", "HTTP")
    assert not mismatch("TCP", "HTTP") and not mismatch("HTTPS", "TCP") and not mismatch(None, "TLS")
    both = [service_entry("http", ["a.example.com"], ((443, "HTTP"),)), service_entry("tls", ["a.example.com"], ((443, "TLS"),))]
    assert "EGRESS-PROTOCOL-MISMATCH" not in ids(egress([{"host": "a.example.com", "port": 443, "protocol": "HTTPS"}], both))
    lower = service_entry("e", ["a.example.com"], ((443, "http"),))
    assert "EGRESS-PROTOCOL-MISMATCH" in ids(egress([{"host": "a.example.com", "port": 443, "protocol": "HTTPS"}], [lower]))
    assert found(egress([{"host": "a.example.com", "port": 443, "protocol": "HTTPS"}], [lower]), "EGRESS-PROTOCOL-MISMATCH")[0]["summary"] == (
        "a.example.com:443 is HTTPS but ServiceEntry payments/e declares HTTP; Envoy would parse it with the wrong filter chain")


def test_egress_unused_entries_and_entries_without_ports():
    result = egress([{"host": "a.example.com", "port": 443}], [service_entry("used", ["a.example.com"]), service_entry("idle", ["b.example.com"]),
                                                              service_entry("portless", ["c.example.com"], ())])
    assert result["unused_entries"] == ["payments/idle"]
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("SE-NO-PORTS", "minor"), ("EGRESS-UNUSED-ENTRY", "info")]


def test_egress_invalid_inputs_name_the_field():
    base = {"destinations": [], "outbound_policy": "ALLOW_ANY"}
    invalid("mesh_egress_coverage", {**base, "service_entries": [res("e", "n", {"hosts": []})]}, "service_entries")
    invalid("mesh_egress_coverage", {**base, "service_entries": [res("e", "n", {})]}, "service_entries")
    invalid("mesh_egress_coverage", {**base, "service_entries": [res("e", "n", {"hosts": ["a"], "ports": [{"number": 0}]})]}, "service_entries")
    invalid("mesh_egress_coverage", {**base, "service_entries": [res("e", "n", {"hosts": ["a"], "ports": [{"number": True}]})]}, "service_entries")
    invalid("mesh_egress_coverage", {**base, "service_entries": [res("e", "n", {"hosts": [3], "ports": []})]}, "service_entries")
    invalid("mesh_egress_coverage", {**base, "service_entries": [], "outbound_policy": "DENY_ALL"}, "outbound_policy")
    invalid("mesh_egress_coverage", {**base, "service_entries": [], "destinations": [{"host": "a", "port": 0}]}, "destinations")


# --- mesh_cidr_overlap ----------------------------------------------------------------------------------------


def cluster(name, pod=(), service=(), node=None) -> dict:
    out = {"name": name, "pod_cidrs": list(pod), "service_cidrs": list(service)}
    if node is not None:
        out["node_cidrs"] = list(node)
    return out


def cidrs(clusters, connectivity="routed") -> dict:
    return ok("mesh_cidr_overlap", {"clusters": clusters, "connectivity": connectivity})


def pair_severity(kind_a, kind_b, connectivity, same_cluster) -> tuple[str, str]:
    fields = lambda kind: {"pod_cidrs": ["10.0.0.0/16"] if kind == "pod" else [], "service_cidrs": ["10.0.0.0/16"] if kind == "service" else [],  # noqa: E731
                           "node_cidrs": ["10.0.0.0/16"] if kind == "node" else []}
    if same_cluster:
        first = {"name": "east", "pod_cidrs": [], "service_cidrs": [], "node_cidrs": []}
        for kind in (kind_a, kind_b):
            key = f"{kind}_cidrs"
            first[key] = first[key] + ["10.0.0.0/16"]
        clusters = [first]
    else:
        clusters = [{"name": "east", **fields(kind_a)}, {"name": "west", **fields(kind_b)}]
    result = cidrs(clusters, connectivity)
    assert result["overlap_count"] == 1, (kind_a, kind_b, connectivity)
    return result["findings"][0]["severity"], result["findings"][0]["id"]


def test_cidr_overlap_clean_clusters_pass():
    result = cidrs([cluster("east", ["10.0.0.0/16"], ["10.96.0.0/12"], ["192.168.0.0/24"]),
                    cluster("west", ["10.1.0.0/16"], ["10.112.0.0/12"], ["192.168.1.0/24"])])
    assert (result["cluster_count"], result["cidr_count"], result["overlap_count"], result["verdict"], result["findings"]) == (2, 6, 0, "pass", [])


def test_cidr_overlap_routed_severity_table_between_clusters():
    expected = {("pod", "pod"): "blocker", ("node", "node"): "blocker", ("node", "pod"): "blocker", ("pod", "service"): "major",
                ("node", "service"): "major", ("service", "service"): "minor"}
    for (a, b), severity_ in expected.items():
        assert pair_severity(a, b, "routed", False) == (severity_, f"CIDR-CROSS-{a.upper()}-{b.upper()}")
        assert pair_severity(b, a, "routed", False)[0] == severity_


def test_cidr_overlap_gateway_connectivity_only_cares_about_nodes():
    assert pair_severity("node", "node", "gateway", False) == ("major", "CIDR-CROSS-NODE-NODE")
    for a, b in (("pod", "pod"), ("node", "pod"), ("pod", "service"), ("node", "service"), ("service", "service")):
        assert pair_severity(a, b, "gateway", False)[0] == "info", (a, b)


def test_cidr_overlap_inside_one_cluster():
    expected = {("pod", "service"): "blocker", ("node", "pod"): "major", ("node", "service"): "major", ("pod", "pod"): "major",
                ("service", "service"): "major", ("node", "node"): "major"}
    for (a, b), severity_ in expected.items():
        for connectivity in ("routed", "gateway"):
            assert pair_severity(a, b, connectivity, True) == (severity_, f"CIDR-SAME-{a.upper()}-{b.upper()}")


def test_cidr_overlap_relations_and_ip_versions():
    contains = cidrs([cluster("east", ["10.0.0.0/16"]), cluster("west", ["10.0.1.0/24"])])
    assert contains["findings"][0]["summary"] == "east pod 10.0.0.0/16 contains west pod 10.0.1.0/24"
    inside = cidrs([cluster("east", ["10.0.1.0/24"]), cluster("west", ["10.0.0.0/16"])])
    assert inside["findings"][0]["summary"] == "east pod 10.0.1.0/24 is inside west pod 10.0.0.0/16"
    same = cidrs([cluster("east", ["10.0.0.0/16"]), cluster("west", ["10.0.0.0/16"])])
    assert same["findings"][0]["summary"] == "east pod 10.0.0.0/16 is identical to west pod 10.0.0.0/16"
    adjacent = cidrs([cluster("east", ["10.0.0.0/17"]), cluster("west", ["10.0.128.0/17"])])
    assert adjacent["overlap_count"] == 0
    dual = cidrs([cluster("east", ["10.0.0.0/16", "fd00:1::/64"]), cluster("west", ["10.1.0.0/16", "fd00:1::/80"])])
    assert dual["overlap_count"] == 1 and dual["findings"][0]["summary"] == "east pod fd00:1::/64 contains west pod fd00:1::/80"
    assert cidrs([cluster("east", ["0.0.0.0/0"]), cluster("west", ["::/0"])])["overlap_count"] == 0


def test_cidr_overlap_findings_are_sorted_worst_first_and_counted():
    result = cidrs([cluster("a", ["10.0.0.0/16"], ["10.0.0.0/20"]), cluster("b", ["10.0.5.0/24"], ["10.96.0.0/12"], ["10.96.0.0/16"])])
    assert result["overlap_count"] == 4
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("CIDR-CROSS-POD-POD", "blocker"), ("CIDR-SAME-POD-SERVICE", "blocker"),
                                                                       ("CIDR-CROSS-POD-SERVICE", "major"), ("CIDR-SAME-NODE-SERVICE", "major")]


def test_cidr_overlap_invalid_inputs_name_the_field():
    for bad in ("10.0.0.1/16", "10.0.0.0/33", "not-a-cidr", "10.0.0/16", "fd00::1/64"):
        invalid("mesh_cidr_overlap", {"clusters": [cluster("a", [bad])], "connectivity": "routed"}, "clusters")
    assert cidrs([cluster("a", ["10.0.0.0"])])["cidr_count"] == 1  # a bare address is a /32 host network
    invalid("mesh_cidr_overlap", {"clusters": [cluster("a"), cluster("a")], "connectivity": "routed"}, "clusters")
    invalid("mesh_cidr_overlap", {"clusters": [cluster("a", [5])], "connectivity": "routed"}, "clusters")
    invalid("mesh_cidr_overlap", {"clusters": [cluster("a")], "connectivity": "mesh"}, "connectivity")


# --- mesh_ingress_tls -----------------------------------------------------------------------------------------


def tls_listener(name="public", **fields) -> dict:
    base = {"name": name, "terminates_tls": True, "certificate_name": "cert", "min_tls_version": "TLSv1.2", "hsts_max_age_s": 31_536_000}
    return {**base, **fields}


def tls(*listeners) -> dict:
    return ok("mesh_ingress_tls", {"listeners": list(listeners)})


def test_ingress_tls_hygienic_listener_passes():
    result = tls(tls_listener(hostnames=["shop.example.com"], certificate_sans=["*.example.com"], cipher_suites=["ECDHE-RSA-AES128-GCM-SHA256"],
                              hsts_include_subdomains=True, redirect_http_to_https=True))
    assert (result["listener_count"], result["tls_listener_count"], result["verdict"], result["findings"]) == (1, 1, "pass", [])


def test_ingress_tls_version_floor_pinned():
    def floor(value, **extra):
        return tls(tls_listener(min_tls_version=value, **extra))
    assert ids(floor("SSLv3")) == ["TLS-VERSION-SSL3"] and severity(floor("SSLv3"), "TLS-VERSION-SSL3") == "blocker"
    for legacy in ("TLSv1.0", "TLSv1.1", "TLSV1_0", "TLSv1_1", "tls1.0", "TLS1_1"):
        assert ids(floor(legacy)) == ["TLS-VERSION-LEGACY"], legacy
        assert severity(floor(legacy), "TLS-VERSION-LEGACY") == "major"
    for modern in ("TLSv1.2", "TLSV1_2", "TLSv1_2", "TLS1.2", "tlsv1.3", "TLSV1_3"):
        assert floor(modern)["findings"] == [], modern
    assert ids(floor("TLS_AUTO")) == ["TLS-MIN-VERSION-UNSET"] and severity(floor("TLS_AUTO"), "TLS-MIN-VERSION-UNSET") == "info"
    unset = tls({"name": "x", "terminates_tls": True, "certificate_name": "c", "hsts_max_age_s": 31_536_000})
    assert ids(unset) == ["TLS-MIN-VERSION-UNSET"] and unset["verdict"] == "pass"


def test_ingress_tls_max_version_checks():
    assert ids(tls(tls_listener(min_tls_version="TLSv1.0", max_tls_version="TLSv1.1"))) == ["TLS-MAX-LEGACY", "TLS-VERSION-LEGACY"]
    assert tls(tls_listener(max_tls_version="TLSv1.2"))["findings"] == []
    assert tls(tls_listener(min_tls_version="TLSv1.2", max_tls_version="TLSv1.2"))["findings"] == []
    invalid("mesh_ingress_tls", {"listeners": [tls_listener(min_tls_version="TLSv1.3", max_tls_version="TLSv1.2")]}, "listeners")
    invalid("mesh_ingress_tls", {"listeners": [tls_listener(min_tls_version="TLSv2")]}, "listeners")
    invalid("mesh_ingress_tls", {"listeners": [tls_listener(max_tls_version="latest")]}, "listeners")


def test_ingress_tls_cipher_classification_pinned():
    def flags(cipher):
        return ids(tls(tls_listener(cipher_suites=[cipher])))
    clean = ("ECDHE-RSA-AES128-GCM-SHA256", "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256",
             "ECDHE-ECDSA-AES128-CCM", "DHE-RSA-AES256-GCM-SHA384", "EDH-RSA-AES256-GCM-SHA384")
    for cipher in clean:
        assert flags(cipher) == [], cipher
    assert flags("ECDHE-RSA-AES256-SHA") == ["TLS-CIPHER-CBC"] and flags("TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA") == ["TLS-CIPHER-CBC"]
    assert flags("DHE-RSA-AES128-SHA256") == ["TLS-CIPHER-CBC"] and flags("ECDHE-RSA-CAMELLIA128-SHA") == ["TLS-CIPHER-CBC"]
    assert flags("TLS_RSA_WITH_AES_128_GCM_SHA256") == ["TLS-CIPHER-NO-PFS"] and flags("AES256-GCM-SHA384") == ["TLS-CIPHER-NO-PFS"]
    assert flags("AES128-SHA") == ["TLS-CIPHER-CBC", "TLS-CIPHER-NO-PFS"]
    for weak in ("ECDHE-RSA-RC4-SHA", "DES-CBC3-SHA", "TLS_RSA_WITH_NULL_SHA", "EXP-RC4-MD5", "ADH-AES128-SHA", "TLS_RSA_WITH_3DES_EDE_CBC_SHA",
                 "AECDH-AES256-SHA", "TLS_DH_anon_WITH_AES_128_CBC_SHA", "ECDHE-RSA-AES128-GCM-MD5", "EXPORT-DES40-CBC-SHA"):
        assert flags(weak) == ["TLS-CIPHER-WEAK"], weak
    assert flags("FOO-BAR") == ["TLS-CIPHER-UNKNOWN"]
    result = tls(tls_listener(cipher_suites=["ECDHE-RSA-RC4-SHA", "ECDHE-RSA-AES256-SHA", "AES128-GCM-SHA256", "FOO"]))
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("TLS-CIPHER-WEAK", "major"), ("TLS-CIPHER-CBC", "minor"),
                                                                       ("TLS-CIPHER-NO-PFS", "minor"), ("TLS-CIPHER-UNKNOWN", "info")]


def test_ingress_tls_ciphers_are_irrelevant_when_the_floor_is_tls13():
    assert tls(tls_listener(min_tls_version="TLSv1.3", cipher_suites=["ECDHE-RSA-RC4-SHA"]))["findings"] == []
    assert ids(tls(tls_listener(min_tls_version="TLSv1.2", cipher_suites=["ECDHE-RSA-RC4-SHA"]))) == ["TLS-CIPHER-WEAK"]
    invalid("mesh_ingress_tls", {"listeners": [tls_listener(cipher_suites=[" "])]}, "listeners")


def test_ingress_tls_certificate_reference_and_sans():
    assert ids(tls(tls_listener(certificate_name=" "))) == ["TLS-NO-CERT"]
    missing = {"name": "x", "terminates_tls": True, "min_tls_version": "TLSv1.2", "hsts_max_age_s": 31_536_000}
    assert ids(tls(missing)) == ["TLS-NO-CERT"] and severity(tls(missing), "TLS-NO-CERT") == "major"

    def san_findings(hosts, sans):
        return found(tls(tls_listener(hostnames=hosts, certificate_sans=sans)), "TLS-SAN-MISMATCH")
    assert san_findings(["shop.example.com"], ["shop.example.com"]) == [] and san_findings(["Shop.Example.com"], ["shop.EXAMPLE.com"]) == []
    assert san_findings(["shop.example.com"], ["*.example.com"]) == []
    assert [f["summary"] for f in san_findings(["example.com", "a.b.example.com", "shop.example.com"], ["*.example.com"])] == [
        "public: certificate SANs do not cover a.b.example.com, example.com"]
    assert san_findings(["*.example.com"], ["*.example.com"]) == [] and len(san_findings(["*.example.com"], ["shop.example.com"])) == 1
    assert len(san_findings(["*.shop.example.com"], ["*.example.com"])) == 1
    assert san_findings(["shop.example.com"], []) == [] and san_findings([], ["a.example.com"]) == []


def test_ingress_tls_hsts_boundaries():
    def hsts(**fields):
        listener_ = tls_listener(**fields)
        if fields.get("hsts_max_age_s", 0) is None:
            listener_.pop("hsts_max_age_s")
        return tls(listener_)
    assert hsts(hsts_max_age_s=31_536_000)["findings"] == []
    short = hsts(hsts_max_age_s=31_535_999)
    assert ids(short) == ["TLS-HSTS-SHORT"] and short["findings"][0]["summary"] == "public: HSTS max-age 31535999s is below one year (31536000s)"
    assert ids(hsts(hsts_max_age_s=0)) == ["TLS-HSTS-DISABLED"]
    assert ids(hsts(hsts_max_age_s=None)) == ["TLS-HSTS-MISSING"] and severity(hsts(hsts_max_age_s=None), "TLS-HSTS-MISSING") == "minor"
    assert hsts(hsts_preload=True, hsts_include_subdomains=True)["findings"] == []
    assert ids(hsts(hsts_preload=True)) == ["TLS-HSTS-PRELOAD-INVALID"] and severity(hsts(hsts_preload=True), "TLS-HSTS-PRELOAD-INVALID") == "major"
    assert ids(hsts(hsts_preload=True, hsts_include_subdomains=True, hsts_max_age_s=31_535_999)) == ["TLS-HSTS-PRELOAD-INVALID", "TLS-HSTS-SHORT"]
    assert hsts(hsts_preload=False)["findings"] == []
    # the preload rule holds even with no max-age: the header is missing, and preload needs a one-year max-age
    no_age = hsts(hsts_preload=True, hsts_include_subdomains=True, hsts_max_age_s=None)
    assert ids(no_age) == ["TLS-HSTS-MISSING", "TLS-HSTS-PRELOAD-INVALID"] and severity(no_age, "TLS-HSTS-PRELOAD-INVALID") == "major"
    assert no_age["verdict"] == "fail"


def test_ingress_tls_plaintext_listeners_and_redirects():
    plain = {"name": "http", "terminates_tls": False}
    assert [(f["id"], f["severity"]) for f in tls(plain)["findings"]] == [("TLS-PLAINTEXT-LISTENER", "minor")]
    assert tls({**plain, "redirect_http_to_https": True})["findings"] == []
    assert ids(tls({**plain, "redirect_http_to_https": False})) == ["TLS-PLAINTEXT-LISTENER"]
    assert ids(tls(tls_listener(redirect_http_to_https=False))) == ["TLS-NO-HTTP-REDIRECT"]
    assert tls(tls_listener(redirect_http_to_https=True))["findings"] == []
    both = tls(tls_listener(), plain)
    assert (both["listener_count"], both["tls_listener_count"]) == (2, 1)
    ignored = tls({"name": "http", "terminates_tls": False, "min_tls_version": "SSLv3", "hsts_max_age_s": 1, "certificate_name": ""})
    assert ids(ignored) == ["TLS-PLAINTEXT-LISTENER"]


# --- shared report semantics ----------------------------------------------------------------------------------


def test_verdict_is_derived_from_the_worst_severity():
    assert tls(tls_listener(min_tls_version="TLS_AUTO"))["verdict"] == "pass"  # info only
    assert tls(tls_listener(hsts_max_age_s=1))["verdict"] == "warn"  # minor
    assert tls(tls_listener(min_tls_version="TLSv1.1"))["verdict"] == "fail"  # major
    assert tls(tls_listener(min_tls_version="SSLv3"))["verdict"] == "fail"  # blocker


def test_findings_are_capped_but_counted_in_full_and_ordered_worst_first():
    entries = [service_entry(f"e{i:03d}", [f"h{i}.example.com"], ((443, "TLS"),)) for i in range(100)]
    destinations = [{"host": f"d{i}.example.org", "port": 443} for i in range(200)]
    result = egress(destinations, entries)
    assert result["finding_count"] == 300 and len(result["findings"]) == 250
    kept = [f["id"] for f in result["findings"]]
    assert kept == ["EGRESS-BLOCKED"] * 200 + ["EGRESS-UNUSED-ENTRY"] * 50  # worst first; the cap drops the tail of the info findings


def test_results_are_deterministic_and_do_not_mutate_the_payload():
    payload = {"network_policies": [netpol("deny", "b"), netpol("deny", "a")], "pods": [pod("b", "p"), pod("a", "q")]}
    frozen = copy.deepcopy(payload)
    first = call("mesh_netpol_coverage", payload)
    assert call("mesh_netpol_coverage", payload) == first and payload == frozen
    assert [row["namespace"] for row in first["namespaces"]] == ["a", "b"]


def test_list_caps_keep_results_small_while_counts_stay_exact():
    workloads = [wl("a", f"w{i:03d}") for i in range(110)]
    result = ok("mesh_mtls_coverage", {"peer_authentications": [], "workloads": workloads})
    assert result["permissive_count"] == 110 and len(result["non_strict_workloads"]) == 100
    assert result["non_strict_workloads"][0] == "a/w000:PERMISSIVE" and result["non_strict_workloads"][-1] == "a/w099:PERMISSIVE"
    invalid("mesh_mtls_coverage", {"peer_authentications": [], "workloads": [wl(f"ns{i}", "w") for i in range(201)]}, "workloads")
    assert ok("mesh_mtls_coverage", {"peer_authentications": [], "workloads": [wl(f"ns{i}", "w") for i in range(200)]})["workload_count"] == 200
    policies = [ingress_to_api(from_pod("web"), name=f"allow-{i:03d}") for i in range(60)]
    listed = reach(policies)
    assert listed["ingress_policies"] == [f"shop/allow-{i:03d}" for i in range(50)]
    gw = gateway("edge", "infra", [listener("http", 80, "HTTP")])
    orphans = [route(f"r{i:03d}", "infra", [{"name": "missing"}]) for i in range(120)]
    capped = gwcheck([gw], orphans)
    assert capped["route_count"] == 120 and capped["unattached_routes"] == [f"infra/r{i:03d}" for i in range(100)]


# --- independent review regressions ---------------------------------------------------------------------------
# Sources: istio.io VirtualService / DestinationRule / ServiceEntry / AuthorizationPolicy references and istio/istio
# pkg/config/validation (agent.ValidateDuration, ValidateHTTPRetry, validateOutlierDetection); kubernetes
# pkg/apis/networking/v1/defaults.go (SetDefaults_NetworkPolicy); gateway-api apis/v1/gateway_types.go and the TLS guide.


def test_authz_allow_rule_with_an_empty_source_matches_everything_like_the_empty_rule():
    # istio.io authorization policy: a from item whose source has no value constrains nothing, so the rule admits every request
    result = ok("mesh_authz_lint", {"policies": [res("open-source", "a", {"rules": [{"from": [{"source": {}}]}]})], "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == ["a"] and result["verdict"] == "fail"
    assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker"


def test_authz_allow_rule_with_only_empty_principal_lists_matches_everything():
    # empty lists are no constraint: source {principals: []} admits every principal, as the literal {} rule does
    result = ok("mesh_authz_lint", {"policies": [res("open-principals", "a", {"rules": [{"from": [{"source": {"principals": []}}]}]})],
                                    "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == ["a"] and result["verdict"] == "fail"
    assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker"


def test_authz_allow_rule_with_an_empty_operation_matches_everything_and_a_narrowing_to_keeps_the_credit():
    # to operation {} constrains nothing; a to item that names a method narrows the rule, so the namespace keeps its default-deny credit
    result = ok("mesh_authz_lint", {"policies": [res("open-operation", "a", {"rules": [{"to": [{"operation": {}}]}]})], "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == ["a"] and result["verdict"] == "fail"
    assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker"
    narrowed = ok("mesh_authz_lint", {"policies": [res("get-only", "a", {"rules": [{"to": [{"operation": {"methods": ["GET"]}}]}]})],
                                      "namespaces": ["a"]})
    assert narrowed["namespaces_without_default_deny"] == [] and "AUTHZ-ALLOW-ALL" not in ids(narrowed)


def test_authz_unrecognised_fields_are_no_constraint():
    # Istio ignores unknown fields: a source key that is a typo for principals, an operation key that is a typo for methods and a when
    # condition with a misspelled value key all constrain nothing, so the rule admits every request
    typo = ok("mesh_authz_lint", {"policies": [res("typo", "a", {"rules": [{"from": [{"source": {"principal": ["cluster.local/ns/x/sa/y"]}}]}]})],
                                  "namespaces": ["a"]})
    assert typo["namespaces_without_default_deny"] == ["a"] and typo["verdict"] == "fail"
    assert severity(typo, "AUTHZ-ALLOW-ALL") == "blocker"
    for rule in ({"to": [{"operation": {"method": ["GET"]}}]}, {"when": [{"key": "request.headers[x]", "value": ["1"]}]}):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [rule]})], "namespaces": ["a"]})
        assert result["namespaces_without_default_deny"] == ["a"] and severity(result, "AUTHZ-ALLOW-ALL") == "blocker", rule
    mixed = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"principal": ["x"], "namespaces": ["web"]}}]}]})],
                                   "namespaces": ["a"]})
    assert mixed["namespaces_without_default_deny"] == [] and "AUTHZ-ALLOW-ALL" not in ids(mixed)


def test_authz_wildcard_in_operation_fields_is_no_constraint_and_a_restricted_source_is_not_a_blocker():
    # a positive to.operation field whose values include "*" matches every value, so the rule admits every request: a blocker
    for rule in ({"to": [{"operation": {"methods": ["*"]}}]}, {"to": [{"operation": {"paths": ["*"]}}]}):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [rule]})], "namespaces": ["a"]})
        assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker" and result["namespaces_without_default_deny"] == ["a"], rule
    restricted = {"from": [{"source": {"principals": ["cluster.local/ns/x/sa/y"]}}], "to": [{"operation": {"methods": ["*"]}}]}
    control = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [restricted]})], "namespaces": ["a"]})
    assert "AUTHZ-ALLOW-ALL" not in ids(control) and control["namespaces_without_default_deny"] == []


def test_authz_wildcard_source_narrows_the_source_and_keeps_its_wildcard_finding():
    # from principals, requestPrincipals or namespaces "*" means any authenticated request: it narrows the source, so no blocker and
    # the namespace is credited; the wildcard finding stays
    for key, fid in (("principals", "AUTHZ-WILDCARD-PRINCIPAL"), ("requestPrincipals", "AUTHZ-WILDCARD-REQUEST-PRINCIPAL"),
                     ("namespaces", "AUTHZ-WILDCARD-NAMESPACE")):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {key: ["*"]}}]}]})], "namespaces": ["a"]})
        assert severity(result, fid) == "major" and "AUTHZ-ALLOW-ALL" not in ids(result), key
        assert result["namespaces_without_default_deny"] == [], key


def test_authz_when_wildcard_values_narrow_and_negated_wildcards_are_not_a_blocker():
    # a when value "*" means the attribute must be present, so the rule is restrictive and the namespace is credited
    present = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"when": [{"key": "request.headers[x]", "values": ["*"]}]}]})],
                                     "namespaces": ["a"]})
    assert "AUTHZ-ALLOW-ALL" not in ids(present) and present["namespaces_without_default_deny"] == []
    # notPrincipals "*" and when notValues "*" admit only requests without that value: the negative-match finding, never a blocker
    for rule in ({"from": [{"source": {"notPrincipals": ["*"]}}]}, {"when": [{"key": "request.headers[x]", "notValues": ["*"]}]}):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [rule]})], "namespaces": ["a"]})
        assert "AUTHZ-ALLOW-ALL" not in ids(result) and severity(result, "AUTHZ-NEGATIVE-MATCH") == "major", rule


def test_authz_universal_ip_blocks_match_every_address_so_they_do_not_narrow():
    # 0.0.0.0/0 and ::/0 admit every address: the rule is open, so the namespace gets no default-deny credit
    for source in ({"ipBlocks": ["0.0.0.0/0"]}, {"remoteIpBlocks": ["::/0"]}):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": source}]}]})], "namespaces": ["a"]})
        assert severity(result, "AUTHZ-OPEN-IPBLOCK") == "major" and severity(result, "AUTHZ-ALLOW-ALL") == "blocker", source
        assert result["namespaces_without_default_deny"] == ["a"], source
    # a principals constraint in the same rule still narrows it: no blocker, and the namespace is credited
    narrowed = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"ipBlocks": ["0.0.0.0/0"],
                                                              "principals": ["cluster.local/ns/x/sa/y"]}}]}]})], "namespaces": ["a"]})
    assert severity(narrowed, "AUTHZ-OPEN-IPBLOCK") == "major" and "AUTHZ-ALLOW-ALL" not in ids(narrowed)
    assert narrowed["namespaces_without_default_deny"] == []


def test_authz_narrower_ip_block_still_narrows_and_is_credited():
    result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"ipBlocks": ["10.0.0.0/8"]}}]}]})],
                                    "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == [] and "AUTHZ-ALLOW-ALL" not in ids(result)
    assert "AUTHZ-OPEN-IPBLOCK" not in ids(result)


def test_authz_ipv4_mapped_universal_range_matches_every_ipv4_peer_so_it_does_not_narrow():
    # ::ffff:0.0.0.0/96 covers every IPv4 peer written in IPv6 notation (the inference marked in _universal_block): the rule is open
    result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"ipBlocks": ["::ffff:0.0.0.0/96"]}}]}]})],
                                    "namespaces": ["a"]})
    # old: AUTHZ-ALLOW-ALL blocker only; new: AUTHZ-OPEN-IPBLOCK major too, as 0.0.0.0/0 and ::/0 get (one predicate for every universal range)
    assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker" and severity(result, "AUTHZ-OPEN-IPBLOCK") == "major"
    assert result["namespaces_without_default_deny"] == ["a"]
    narrower = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"ipBlocks": ["::ffff:10.0.0.0/104"]}}]}]})],
                                      "namespaces": ["a"]})
    # old: no AUTHZ-ALLOW-ALL asserted; new: neither finding, and the namespace is credited
    assert ids(narrower) == [] and narrower["namespaces_without_default_deny"] == []


def test_authz_open_ip_block_is_one_predicate_for_every_universal_range():
    # 0.0.0.0/0 and ::/0 keep the findings they had; ::ffff:0.0.0.0/96 gets the same set, through the same predicate, for both fields
    for block in ("0.0.0.0/0", "::/0", "::ffff:0.0.0.0/96"):
        for field in ("ipBlocks", "remoteIpBlocks"):
            result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {field: [block]}}]}]})],
                                            "namespaces": ["a"]})
            assert ids(result) == ["AUTHZ-ALLOW-ALL", "AUTHZ-NO-DEFAULT-DENY", "AUTHZ-OPEN-IPBLOCK"], (block, field)
            assert result["namespaces_without_default_deny"] == ["a"], (block, field)


def test_authz_ipv6_network_containing_the_mapped_range_is_universal_and_narrower_ones_still_narrow():
    # a network that contains all of ::ffff:0.0.0.0/96 admits every IPv4 peer under the same inference: ::/64 and ::/80 are universal,
    # so they get the open-block findings and no credit (old: no finding and credit, since only equality was checked)
    for block in ("::/64", "::/80"):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"ipBlocks": [block]}}]}]})], "namespaces": ["a"]})
        assert severity(result, "AUTHZ-ALLOW-ALL") == "blocker" and severity(result, "AUTHZ-OPEN-IPBLOCK") == "major", block
        assert result["namespaces_without_default_deny"] == ["a"], block
    # a network that does not contain the whole mapped range still narrows: ::/81 has bit 80 fixed, ::/96 excludes ::ffff:0:0/96
    for block in ("::/81", "::/96"):
        result = ok("mesh_authz_lint", {"policies": [res("p", "a", {"rules": [{"from": [{"source": {"ipBlocks": [block]}}]}]})], "namespaces": ["a"]})
        assert "AUTHZ-ALLOW-ALL" not in ids(result) and "AUTHZ-OPEN-IPBLOCK" not in ids(result), block
        assert result["namespaces_without_default_deny"] == [], block


def test_authz_deny_all_rule_is_a_default_deny_that_an_allow_all_cannot_cancel():
    # istio.io security concepts: DENY with rules [{}] "will always deny the request even if there is another ALLOW policy"
    deny_all = res("deny-all", "a", {"action": "DENY", "rules": [{}]})
    allow_all = res("allow-all", "a", {"rules": [{}]})
    result = ok("mesh_authz_lint", {"policies": [deny_all, allow_all], "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == [] and ids(result) == ["AUTHZ-ALLOW-ALL", "AUTHZ-DENY-ALL"]
    assert severity(result, "AUTHZ-DENY-ALL") == "info"
    assert found(result, "AUTHZ-DENY-ALL")[0]["summary"] == "a/deny-all: a DENY rule {} blocks every request to the namespace, whatever any ALLOW policy says"
    mesh = ok("mesh_authz_lint", {"policies": [res("deny-all", "istio-system", {"action": "DENY", "rules": [{}]})], "namespaces": ["a", "b"]})
    assert mesh["mesh_wide_default_deny"] is True and mesh["namespaces_without_default_deny"] == []
    scoped = res("deny-one", "a", {"action": "DENY", "selector": {"matchLabels": {"app": "x"}}, "rules": [{}]})
    narrow = res("deny-evil", "a", {"action": "DENY", "rules": [{"from": [{"source": {"namespaces": ["evil"]}}]}]})
    for policy in (scoped, narrow):
        assert ok("mesh_authz_lint", {"policies": [policy], "namespaces": ["a"]})["namespaces_without_default_deny"] == ["a"]


def test_authz_namespace_wide_allow_with_specific_rules_is_a_default_deny():
    # With any ALLOW policy applying to a workload a request needs a matching ALLOW rule (istio.io authorization-policy reference)
    frontend = res("frontend-only", "a", {"rules": [allow_from(["cluster.local/ns/web/sa/frontend"])]})
    result = ok("mesh_authz_lint", {"policies": [frontend], "namespaces": ["a"]})
    assert result["namespaces_without_default_deny"] == [] and result["finding_count"] == 0 and result["mesh_wide_default_deny"] is False
    cancelled = ok("mesh_authz_lint", {"policies": [frontend, res("open", "a", {"rules": [{}]})], "namespaces": ["a"]})
    assert cancelled["namespaces_without_default_deny"] == ["a"]
    workload_only = res("scoped", "a", {"selector": {"matchLabels": {"app": "x"}}, "rules": [allow_from(["cluster.local/ns/web/sa/frontend"])]})
    assert ok("mesh_authz_lint", {"policies": [workload_only], "namespaces": ["a"]})["namespaces_without_default_deny"] == ["a"]
    mesh = ok("mesh_authz_lint", {"policies": [res("mesh-allow", "istio-system", {"rules": [allow_from(["cluster.local/ns/web/sa/frontend"])]})],
                                  "namespaces": ["a", "b"]})
    assert mesh["mesh_wide_default_deny"] is True and mesh["namespaces_without_default_deny"] == []


def test_traffic_split_istio_duration_rules_for_timeouts_and_per_try_timeouts():
    def one(**route_fields):
        return split([vs("r", "d", [{"route": [dest("a")], **route_fields}])])
    sub_ms = one(timeout="1500us")
    assert ids(sub_ms) == ["TRAFFIC-DURATION-INVALID"]
    assert sub_ms["findings"][0]["summary"] == "d/r: http[0]: timeout 1500us is not a whole number of milliseconds; Istio rejects it (omit the field for no timeout)"
    assert one(timeout="1ms")["findings"] == [] and one(timeout="1500ms")["findings"] == [] and one(timeout="0.0015s")["findings"][0]["id"] == "TRAFFIC-DURATION-INVALID"
    bad_try = one(timeout="5s", retries={"attempts": 2, "perTryTimeout": "0s"})
    assert ids(bad_try) == ["TRAFFIC-DURATION-INVALID"]
    assert bad_try["findings"][0]["summary"] == "d/r: http[0]: perTryTimeout 0s is below the 1ms minimum; Istio rejects it"
    assert ids(one(timeout="5s", retries={"attempts": 0, "retryOn": "5xx"})) == ["TRAFFIC-RETRY-DISABLED-CONFIG"]
    assert severity(one(timeout="5s", retries={"attempts": 0, "retryOn": "5xx"}), "TRAFFIC-RETRY-DISABLED-CONFIG") == "major"


def test_traffic_split_host_resolution_leaves_wildcards_and_ip_literals_alone():
    # pilot/pkg/model ResolveShortnameToFQDN: "*" is fully qualified and an IP address gets no namespace or domain appended
    for host in ("*", "10.1.2.3", "::1"):
        result = split([vs("r", "d", [{"route": [dest(host, None, "v1")]}])])
        assert [f["summary"] for f in found(result, "TRAFFIC-SUBSET-NO-DESTINATIONRULE")] == [
            f"d/r: http[0]: subset 'v1' of {host} has no DestinationRule defining subsets"]
    assert split([vs("r", "d", [{"route": [dest("*", None, "v1")]}])], [dr("any", "d", "*", ["v1"])])["findings"] == []


def test_destination_policy_local_origin_and_gateway_error_semantics():
    def outlier(**fields):
        return dpolicy([policy_rule("r", {"outlierDetection": fields})])
    assert ids(outlier(consecutive5xxErrors=0)) == ["DR-OUTLIER-INERT"]
    assert outlier(consecutive5xxErrors=0, consecutiveGatewayErrors=3)["findings"] == []
    assert outlier(consecutive5xxErrors=0, splitExternalLocalOriginErrors=True)["findings"] == []  # local-origin failures default to 5
    assert ids(outlier(consecutive5xxErrors=0, splitExternalLocalOriginErrors=True, consecutiveLocalOriginFailures=0)) == ["DR-OUTLIER-INERT"]
    orphan = outlier(consecutiveLocalOriginFailures=3)
    assert ids(orphan) == ["DR-OUTLIER-LOCAL-ORIGIN"] and severity(orphan, "DR-OUTLIER-LOCAL-ORIGIN") == "major"
    shadowed = outlier(consecutive5xxErrors=5, consecutiveGatewayErrors=5)  # istio.io: gateway errors >= 5xx errors "will have no effect"
    assert ids(shadowed) == ["DR-OUTLIER-GATEWAY-SHADOWED"] and severity(shadowed, "DR-OUTLIER-GATEWAY-SHADOWED") == "info"
    assert ids(outlier(consecutiveGatewayErrors=5)) == ["DR-OUTLIER-GATEWAY-SHADOWED"]  # consecutive5xxErrors defaults to 5
    assert outlier(consecutive5xxErrors=5, consecutiveGatewayErrors=4)["findings"] == []
    invalid("mesh_destination_policy", {"destination_rules": [policy_rule("r", {"outlierDetection": {"splitExternalLocalOriginErrors": "yes"}})],
                                         "workloads": []}, "destination_rules")


def test_destination_policy_durations_need_whole_milliseconds():
    # istio agent.ValidateDuration: "duration must be greater than 1ms" and "only durations to ms precision are supported"
    result = dpolicy([policy_rule("r", {"outlierDetection": {"interval": "1500us", "baseEjectionTime": "30s"},
                                        "connectionPool": {"tcp": {"connectTimeout": "2500us"}}})])
    assert sorted(f["summary"] for f in result["findings"]) == [
        "d/r: trafficPolicy: connectTimeout 2500us is not a whole number of milliseconds",
        "d/r: trafficPolicy: outlierDetection.interval 1500us is not a whole number of milliseconds"]
    zero = dpolicy([policy_rule("r", {"connectionPool": {"tcp": {"connectTimeout": "0s"}}, "outlierDetection": {"baseEjectionTime": "0s"}})])
    assert sorted(f["summary"] for f in zero["findings"]) == [
        "d/r: trafficPolicy: connectTimeout 0s is below the 1ms minimum", "d/r: trafficPolicy: outlierDetection.baseEjectionTime 0s is below the 1ms minimum"]


def test_gateway_httproute_attaches_to_http_and_https_listeners_only():
    # gateway-api TLS guide: a TLS listener takes TLSRoute, HTTPS takes HTTPRoute
    tls_gateway = gateway("g", "n", [listener("pass", 443, "TLS", tls={"mode": "Passthrough"})])
    result = gwcheck([tls_gateway], [route("r", "n", [{"name": "g"}])])
    assert ids(result) == ["ROUTE-NOT-ACCEPTED"] and result["unattached_routes"] == ["n/r"]
    assert found(result, "ROUTE-NOT-ACCEPTED")[0]["summary"] == (
        "n/r: Gateway n/g accepts the route on no listener (listener pass: protocol TLS cannot carry an HTTPRoute)")
    https = gateway("g", "n", [listener("https", 443, "HTTPS", tls={"certificateRefs": [{"name": "c"}]})])
    assert gwcheck([https], [route("r", "n", [{"name": "g"}])])["findings"] == []


def test_gateway_allowed_routes_none_is_valid_and_accepts_no_route():
    gw = gateway("g", "n", [listener("l", 80, "HTTP", allowedRoutes={"namespaces": {"from": "None"}})])
    assert gwcheck([gw])["findings"] == []
    result = gwcheck([gw], [route("r", "n", [{"name": "g"}])])
    assert ids(result) == ["ROUTE-NOT-ACCEPTED"] and "listener l: allowedRoutes.namespaces.from is None, so no route may attach" in result["findings"][0]["summary"]


def test_gateway_tls_is_rejected_on_every_non_tls_protocol():
    for protocol in ("HTTP", "TCP", "UDP"):
        result = gwcheck([gateway("g", "n", [listener("l", 80, protocol, tls={"mode": "Terminate"})])])
        assert [(f["severity"], f["summary"]) for f in found(result, "GW-LISTENER-TLS-UNUSED")] == [
            ("major", f"n/g: listener l: tls must not be set on a {protocol} listener; the API rejects it")]


def test_netpol_empty_egress_list_without_policy_types_is_ingress_only():
    # SetDefaults_NetworkPolicy: Egress is added only `if len(obj.Spec.Egress) != 0`
    row = coverage([netpol("p", "a", egress=[])], [pod("a", "p")])["namespaces"][0]
    assert (row["default_deny_ingress"], row["default_deny_egress"]) == (True, False)
    web_only = netpol("p", "shop", {"matchLabels": {"app": "web"}}, egress=[])
    result = reach([web_only])
    assert (result["egress_isolated"], result["ingress_isolated"], result["reachable"]) == (False, False, True)
    assert reach([web_only], source=API, destination=WEB, port=8080)["ingress_isolated"] is True
    listed = reach([netpol("p", "shop", {"matchLabels": {"app": "web"}}, egress=[{"to": [{"podSelector": {"matchLabels": {"app": "db"}}}]}])])
    assert (listed["egress_isolated"], listed["reachable"]) == (True, False)


def test_netpol_world_open_ip_block_rules_are_reported_even_under_a_default_deny():
    def world(*peers, ports=None, direction="Egress"):
        rule = {("to" if direction == "Egress" else "from"): list(peers), **({"ports": ports} if ports else {})}
        return coverage([netpol("deny", "a", types=["Ingress", "Egress"]),
                         netpol("out", "a", types=[direction], **{"egress" if direction == "Egress" else "ingress": [rule]})], [pod("a", "p")])
    flagged = world({"ipBlock": {"cidr": "0.0.0.0/0"}})
    assert [(f["severity"], f["summary"]) for f in found(flagged, "NETPOL-ALLOW-ANY-CIDR-EGRESS")] == [
        ("minor", "a/out: a egress rule admits every address (ipBlock /0) on all ports; pod IPs are included or not depending on the CNI")]
    assert found(world({"ipBlock": {"cidr": "::/0"}}), "NETPOL-ALLOW-ANY-CIDR-EGRESS")
    assert found(world({"ipBlock": {"cidr": "0.0.0.0/0"}}, direction="Ingress"), "NETPOL-ALLOW-ANY-CIDR-INGRESS")
    for quiet in (world({"ipBlock": {"cidr": "0.0.0.0/0", "except": ["10.0.0.0/8"]}}), world({"ipBlock": {"cidr": "0.0.0.0/0"}}, ports=[{"port": 443}]),
                  world({"ipBlock": {"cidr": "10.0.0.0/8"}}), world({"podSelector": {}})):
        assert not [f for f in quiet["findings"] if f["id"].startswith("NETPOL-ALLOW-ANY-CIDR")]


def test_egress_tcp_service_entry_with_resolution_none_and_no_addresses_opens_the_port():
    # istio.io ServiceEntry: resolution NONE on a TCP port without addresses allows traffic to any IP on that port
    entry = service_entry("db", ["db.example.com"], ((5432, "TCP"),))
    destination = [{"host": "db.example.com", "port": 5432}]
    strict = egress(destination, [entry])
    assert [(f["severity"], f["summary"]) for f in strict["findings"]] == [
        ("major", "payments/db: resolution NONE on a TCP port without addresses allows traffic to any IP on that port")]
    assert [f["severity"] for f in egress(destination, [entry], "ALLOW_ANY")["findings"]] == ["info"]
    closed_cases = [(service_entry("db", ["db.example.com"], ((5432, "TCP"),), addresses=["10.0.0.5/32"]), destination),
                    (service_entry("db", ["db.example.com"], ((5432, "TCP"),), resolution="DNS"), destination),
                    (service_entry("db", ["db.example.com"], ((5432, "TCP"),), resolution="STATIC"), destination),
                    (service_entry("db", ["db.example.com"], ((443, "TLS"),)), [{"host": "db.example.com", "port": 443}])]
    for closed, traffic in closed_cases:
        assert egress(traffic, [closed])["findings"] == []


def test_ingress_tls_rc2_is_a_weak_cipher():
    result = tls(tls_listener(cipher_suites=["TLS_RSA_WITH_RC2_CBC_40_SHA", "ECDHE-RSA-AES128-GCM-SHA256"]))
    assert [f["summary"] for f in found(result, "TLS-CIPHER-WEAK")] == [
        "public: cipher(s) weak (RC4, RC2, DES/3DES, NULL, EXPORT, anonymous or MD5): TLS_RSA_WITH_RC2_CBC_40_SHA"]
    duplicated = tls(tls_listener(hostnames=["a.example.com", "a.example.com"], certificate_sans=["b.example.com"]))
    assert [f["summary"] for f in found(duplicated, "TLS-SAN-MISMATCH")] == ["public: certificate SANs do not cover a.example.com"]


# An independent oracle of the Kubernetes NetworkPolicy rules (peer union, namespaceSelector/podSelector AND, ipBlock minus
# except, protocol-aware named ports, endPort, additive policies, default policyTypes) over seeded random policy sets.
_NP_NAMESPACES = {"a": {"team": "t1"}, "b": {"team": "t2"}, "c": {"team": "t1"}}
_NP_APPS = ("web", "api", "db")


def _np_matches(selector, labels):
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for expr in selector.get("matchExpressions") or []:
        has = expr["key"] in labels
        inside = has and labels[expr["key"]] in expr.get("values", [])
        if (expr["operator"] == "In" and not inside) or (expr["operator"] == "NotIn" and inside):
            return False
    return True


def _np_types(policy):
    spec = policy["spec"]
    return spec.get("policyTypes") or ["Ingress"] + (["Egress"] if spec.get("egress") else [])


def _np_peer(peer, policy_namespace, other):
    import ipaddress
    if "ipBlock" in peer:
        block, address = peer["ipBlock"], ipaddress.ip_address(other["ip"])
        return address in ipaddress.ip_network(block["cidr"]) and not any(address in ipaddress.ip_network(x) for x in block.get("except", []))
    if "namespaceSelector" in peer:
        if not _np_matches(peer["namespaceSelector"], {"kubernetes.io/metadata.name": other["namespace"], **_NP_NAMESPACES[other["namespace"]]}):
            return False
    elif other["namespace"] != policy_namespace:
        return False
    return "podSelector" not in peer or _np_matches(peer["podSelector"], other["labels"])


def _np_ports(ports, destination, port, protocol):
    if not ports:
        return True
    for entry in ports:
        if entry.get("protocol", "TCP") != protocol:
            continue
        wanted = entry.get("port")
        if wanted is None:
            return True
        if isinstance(wanted, str):
            if protocol == "TCP" and any(c["name"] == wanted and c["containerPort"] == port for c in destination["ports"]):
                return True
        elif wanted <= port <= entry.get("endPort", wanted):
            return True
    return False


def _np_side(policies, direction, subject, other, destination, port, protocol):
    selecting = [p for p in policies if p["metadata"]["namespace"] == subject["namespace"] and direction in _np_types(p)
                 and _np_matches(p["spec"]["podSelector"], subject["labels"])]
    if not selecting:
        return True
    key, peers_key = ("ingress", "from") if direction == "Ingress" else ("egress", "to")
    return any((not rule.get(peers_key) or any(_np_peer(x, p["metadata"]["namespace"], other) for x in rule[peers_key]))
               and _np_ports(rule.get("ports"), destination, port, protocol)
               for p in selecting for rule in p["spec"].get(key) or [])


def _np_random_policies(rng, count):
    def selector():
        roll = rng.random()
        if roll < .25:
            return {}
        if roll < .6:
            return {"matchLabels": {"app": rng.choice(_NP_APPS)}}
        if roll < .8:
            return {"matchLabels": {"tier": rng.choice("xy")}}
        return {"matchExpressions": [{"key": "app", "operator": rng.choice(["In", "NotIn"]), "values": rng.sample(_NP_APPS, 2)}]}

    def peer():
        roll = rng.random()
        if roll < .3:
            return {"podSelector": selector()}
        if roll < .55:
            return {"namespaceSelector": rng.choice([{}, {"matchLabels": {"team": "t1"}}, {"matchLabels": {"kubernetes.io/metadata.name": "b"}}])}
        if roll < .75:
            return {"namespaceSelector": rng.choice([{}, {"matchLabels": {"team": "t1"}}]), "podSelector": selector()}
        block = {"cidr": rng.choice(["10.0.0.0/16", "10.1.0.0/16", "10.0.0.0/8", "0.0.0.0/0"])}
        if rng.random() < .4:
            block["except"] = [rng.choice(["10.0.1.0/24", "10.1.0.0/24", "10.2.2.0/24"])]
        return {"ipBlock": block}

    def ports():
        if rng.random() < .4:
            return []
        out = []
        for _ in range(rng.randint(1, 2)):
            entry, roll = {}, rng.random()
            if rng.random() < .8:
                entry["protocol"] = rng.choice(["TCP", "UDP", "TCP"])
            if roll < .4:
                entry["port"] = rng.choice([8080, 9090, 80])
            elif roll < .6:
                entry["port"] = rng.choice(["http", "metrics", "nope"])
            elif roll < .8:
                entry.update(port=8000, endPort=9000)
            out.append(entry)
        return out

    def rule(peers_key):
        out = {}
        if rng.random() < .85:
            out[peers_key] = [peer() for _ in range(rng.randint(1, 2))]
        if (p := ports()):
            out["ports"] = p
        return out

    policies = []
    for index in range(count):
        spec = {"podSelector": selector()}
        types = rng.choice([["Ingress"], ["Egress"], ["Ingress", "Egress"], None])
        if types:
            spec["policyTypes"] = types
        if rng.random() < .8:
            spec["ingress"] = [rule("from") for _ in range(rng.randint(0, 2))]
        if rng.random() < .8:
            spec["egress"] = [rule("to") for _ in range(rng.randint(0, 2))]
        policies.append(res(f"p{index}", rng.choice(sorted(_NP_NAMESPACES)), spec))
    return policies


def test_reachability_matches_an_independent_networkpolicy_oracle_on_random_policy_sets():
    import random
    rng = random.Random(7)
    namespaces = [{"name": n, "labels": labels} for n, labels in _NP_NAMESPACES.items()]
    checked = 0
    for _ in range(250):
        pods = [{"namespace": ns, "name": f"{app}-{ns}", "labels": {"app": app, "tier": rng.choice("xy")}, "ip": f"10.{i}.{j}.5",
                 "ports": [{"name": "http", "containerPort": 8080}, {"name": "metrics", "containerPort": 9090}]}
                for i, ns in enumerate(sorted(_NP_NAMESPACES)) for j, app in enumerate(_NP_APPS)]
        policies = _np_random_policies(rng, rng.randint(1, 6))
        for _ in range(4):
            source, destination = rng.sample(pods, 2)
            port, protocol = rng.choice([8080, 9090, 80, 8500]), rng.choice(["TCP", "TCP", "UDP"])
            expected = (_np_side(policies, "Egress", source, destination, destination, port, protocol)
                        and _np_side(policies, "Ingress", destination, source, destination, port, protocol))
            result = ok("mesh_netpol_reachability", {"network_policies": policies, "pods": pods, "namespaces": namespaces, "source": ref(source),
                                                     "destination": ref(destination), "port": port, "protocol": protocol})
            assert result["reachable"] is expected, (policies, source["name"], destination["name"], port, protocol)
            checked += 1
    assert checked == 1000


def _dns_resolver_trace(name, ndots, search, resolvable):
    """Candidate names a glibc-style resolver sends until one is in `resolvable` (a trailing dot is absolute)."""
    if name.endswith("."):
        return 1
    absolute, searched = [name + "."], [f"{name}.{suffix}." for suffix in search]
    candidates = absolute + searched if name.count(".") >= ndots else searched + absolute
    for index, candidate in enumerate(candidates, 1):
        if candidate in resolvable:
            return index
    return len(candidates)


def test_dns_lookup_counts_match_a_simulated_resolver_for_every_ndots():
    service = "web.prod.svc.cluster.local."
    cluster_names = {"web": service, "web.prod": service, "web.prod.svc": service, "web.prod.svc.cluster.local": service,
                     "web.other": "web.other.svc.cluster.local.", "web.other.svc": "web.other.svc.cluster.local."}
    search = ["prod.svc.cluster.local", "svc.cluster.local", "cluster.local"]
    for ndots in range(0, 16):
        for name, answer in cluster_names.items():
            got = ok("mesh_dns_ndots", {"ndots": ndots, "namespace": "prod", "search_domains": search, "names": [{"name": name, "target": "cluster"}]})
            assert got["names"][0]["lookups"] == _dns_resolver_trace(name, ndots, search, {answer}), (ndots, name)
        for name in ("api.example.com", "example.com", "a.b.c.d.e.f", "localhost"):
            got = ok("mesh_dns_ndots", {"ndots": ndots, "namespace": "prod", "search_domains": search, "names": [{"name": name, "target": "external"}]})
            assert got["names"][0]["lookups"] == _dns_resolver_trace(name, ndots, search, {name + "."}), (ndots, name)


# Secret safety: results echo names, hosts and values from the caller's manifests. A credential pasted into any of them must be
# masked in findings, ids, lists and error messages.
CANARIES = [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "0123456789abcdef" * 4,
    "abandon ability able about above absent absorb abstract absurd abuse access accident",
    "https://admin:s3cr3tpassw0rd@evil.example/x",
]
CANARY_SECRET_PART = {CANARIES[4]: "s3cr3tpassw0rd"}


def _secret_payloads(name: str) -> dict:
    wl_a = {"namespace": "a", "name": name, "labels": {}}
    web_pod = {"namespace": "shop", "name": "web", "labels": {"app": "web"}}
    return {
        "mesh_authz_lint": {"policies": [res(name, "a", {"rules": [{}]})]},
        "mesh_mtls_coverage": {"peer_authentications": [pa(name, "a", "NOPE")], "workloads": [wl_a]},
        "mesh_traffic_split": {"virtual_services": [vs(name, "d", [{"route": [dest("a", 60), dest("b", 60)], "timeout": name}])], "destination_rules": []},
        "mesh_destination_policy": {"destination_rules": [policy_rule(name, {"tls": {"mode": name}})], "workloads": []},
        "mesh_gateway_api_check": {"gateways": [gateway(name, "n", [listener("l", 80, name, hostname=name)])],
                                   "routes": [route(name, "n", [{"name": name}])], "reference_grants": []},
        "mesh_netpol_coverage": {"network_policies": [netpol(name, name, egress=[{}])], "pods": [pod(name, name)]},
        "mesh_netpol_reachability": {"network_policies": [], "pods": [web_pod], "source": {"namespace": "shop", "name": "web"},
                                     "destination": {"namespace": "shop", "name": name}, "port": 80},
        "mesh_retry_amplification": {"calls": [hop(name, "x", 1), hop("x", name, 1)], "max_multiplier": 2},
        "mesh_timeout_hierarchy": {"calls": [tcall(name, "mid", 100), tcall("mid", name, 500)]},
        "mesh_dns_ndots": {"ndots": 5, "namespace": "shop", "search_domains": SEARCH, "names": [{"name": name, "target": "external"}]},
        "mesh_egress_coverage": {"destinations": [{"host": name, "port": 443}], "service_entries": [service_entry("se", ["other.example.com"])],
                                 "outbound_policy": "REGISTRY_ONLY"},
        "mesh_cidr_overlap": {"clusters": [{"name": "c1", "pod_cidrs": [name[:64]], "service_cidrs": []}], "connectivity": "routed"},
        "mesh_ingress_tls": {"listeners": [tls_listener(name[:253], hostnames=[name], certificate_sans=["other.example.com"], min_tls_version=name[:16],
                                                        cipher_suites=[name[:80]])]},
    }


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_no_tool_echoes_credential_shaped_input_values(tool):
    control = call(tool, _secret_payloads("plain-name")[tool])
    assert "plain-name" in json.dumps(control), (tool, control)  # the field really is echoed, so the canary check below is not vacuous
    for canary in CANARIES:
        result = call(tool, _secret_payloads(canary)[tool])
        text = json.dumps(result)
        assert CANARY_SECRET_PART.get(canary, canary) not in text, (tool, canary, text[:400])
        assert result["state"] in ("SUCCESS", "INVALID_INPUT"), result


def test_names_are_bounded_so_every_echoed_string_fits_its_output_schema():
    huge = "n" * 100_000
    rejected = invalid("mesh_mtls_coverage", {"peer_authentications": [], "workloads": [wl("a", huge)]}, "workloads")
    assert huge not in json.dumps(rejected) and rejected["message"] == "workloads[0].name is longer than 253 characters"
    longest = "n" * 253  # a DNS subdomain: the longest name Kubernetes accepts
    result = ok("mesh_mtls_coverage", {"peer_authentications": [pa("p", longest, "STRICT", {"app": "x"}, {8080: "DISABLE"})],
                                       "workloads": [{"namespace": longest, "name": longest, "labels": {"app": "x"}}]})
    assert [len(item) for item in result["non_strict_workloads"]] == [len(longest) * 2 + 1 + len(":STRICT-with-port-exceptions")]
    assert result["namespaces"][0]["namespace"] == longest and len(json.dumps(result)) < 10_000
    long_pod = {"namespace": longest, "name": longest, "labels": {}, "ip": ""}
    notes = ok("mesh_netpol_reachability", {"network_policies": [netpol("p", longest, types=["Ingress"], ingress=[{"from": [{"ipBlock": {"cidr": "10.0.0.0/8"}}]}])],
                                            "pods": [long_pod, {"namespace": longest, "name": "dst", "labels": {}}],
                                            "source": {"namespace": longest, "name": longest}, "destination": {"namespace": longest, "name": "dst"}, "port": 80})
    assert all(len(note) <= 400 for note in notes["notes"]) and notes["notes"]


def test_gateway_reference_grants_match_by_index_and_first_matching_grant_counts_as_used():
    sender = {"group": GW_GROUP, "kind": "HTTPRoute", "namespace": "a"}
    receiver = {"group": "", "kind": "Service"}
    first, second = grant("b", [sender], [receiver], name="first"), grant("b", [sender], [{**receiver, "name": "web"}], name="second")
    cross = route("r", "a", [{"name": "g"}], [{"backendRefs": [{"name": "web", "namespace": "b", "port": 80}]}])
    gw = gateway("g", "a", [listener("http", 80, "HTTP")])
    result = gwcheck([gw], [cross], [first, second])
    assert [f["summary"] for f in result["findings"]] == ["b/second: ReferenceGrant is not needed by any supplied Gateway or HTTPRoute"]
    assert [f["summary"] for f in gwcheck([gw], [cross], [second, first])["findings"]] == [
        "b/first: ReferenceGrant is not needed by any supplied Gateway or HTTPRoute"]
    named = gwcheck([gw], [cross], [grant("b", [sender], [{**receiver, "name": "other"}])])
    assert ids(named) == ["GRANT-UNUSED", "ROUTE-BACKEND-NO-GRANT"]


def test_gateway_non_string_kinds_and_groups_never_match_and_never_crash():
    odd = route("r", "a", [{"name": "g"}], [{"backendRefs": [{"name": "web", "namespace": "b", "kind": ["Service"], "port": 80}]}])
    gw = gateway("g", "a", [listener("http", 80, "HTTP")])
    weird_grant = grant("b", [{"group": ["x"], "kind": "HTTPRoute", "namespace": "a"}], [{"group": {"y": 1}, "kind": "Service", "name": ["web"]}])
    result = gwcheck([gw], [odd], [weird_grant])
    assert ids(result) == ["GRANT-UNUSED", "ROUTE-BACKEND-NO-GRANT"]
