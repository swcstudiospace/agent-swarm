"""Behaviour tests for the A17 Platform Engineer tools (plat_catalog_lint .. plat_request_guardrails).

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A17", "a17-platform-engineer", "plat_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "plat_catalog_lint",
    "plat_template_params_check",
    "plat_composition_diff",
    "plat_claim_validate",
    "plat_maturity_scorecard",
    "plat_dora_bands",
    "plat_catalog_graph",
    "plat_ephemeral_env_policy",
    "plat_quota_fit",
    "plat_golden_path_coverage",
    "plat_api_deprecation",
    "plat_request_guardrails",
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


def rules(result: dict) -> list[tuple[str, str]]:
    """(rule, severity) of every finding, in result order."""
    return [(f["id"].split("@")[0], f["severity"]) for f in result["findings"]]


def has(result: dict, rule: str, severity: str, subject: str | None = None) -> bool:
    return any(
        f["id"].split("@")[0] == rule and f["severity"] == severity and (subject is None or f["id"].split("@", 1)[1] == subject)
        for f in result["findings"]
    )


def lacks(result: dict, rule: str) -> bool:
    return all(f["id"].split("@")[0] != rule for f in result["findings"])


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


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_success_example_is_a_deterministic_success(name):
    sample = next(t for t in lane_tools() if t["name"] == name)["examples"]["success"]
    first = ok(name, sample)
    assert first == ok(name, copy.deepcopy(sample))


# --- plat_catalog_lint ----------------------------------------------------------------------------------------

CLEAN = {
    "kind": "Component", "name": "payments-api", "type": "service", "lifecycle": "production", "owner": "group:default/payments",
    "system": "checkout", "tier": "tier-1", "description": "Payments API",
    "annotations": ["backstage.io/techdocs-ref", "backstage.io/source-location", "example.com/runbook"],
}


def lint(*entries: dict, **extra) -> dict:
    return ok("plat_catalog_lint", {"entries": list(entries), **extra})


def entry(**over) -> dict:
    merged = {**CLEAN, **over}
    return {k: v for k, v in merged.items() if v is not None}


def test_lint_clean_entity_passes_with_exact_output():
    assert lint(CLEAN) == {"state": "SUCCESS", "entry_count": 1, "finding_count": 0, "blocker_count": 0, "major_count": 0, "status": "pass", "findings": []}


def test_lint_bare_component_lists_every_missing_field_most_severe_first():
    result = lint({"kind": "Component", "name": "x"})
    assert rules(result) == [
        ("lifecycle-missing", "blocker"), ("owner-missing", "blocker"), ("type-missing", "major"),
        ("docs-missing", "minor"), ("system-missing", "minor"), ("tier-missing", "minor"),
        ("description-missing", "info"), ("source-location-missing", "info"),
    ]
    assert (result["blocker_count"], result["major_count"], result["finding_count"], result["status"]) == (2, 1, 8, "fail")


@pytest.mark.parametrize("name", ["a", "A1", "a-b_c.d", "x" * 63, "9lives", "a--b", "a..b", "a_.b", "a-_-b", "Z"])
def test_lint_accepts_valid_names(name):
    # upstream isValidObjectName: /^([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]$/, 1..63 chars: separators may repeat, ends are alphanumeric
    assert lacks(lint(entry(name=name)), "name-invalid")


@pytest.mark.parametrize("name", ["-a", "a-", "a b", "x" * 64, "a/b", "a:b", "ünï", ".", "_a", "a\n"])
def test_lint_rejects_invalid_names(name):
    assert has(lint(entry(name=name)), "name-invalid", "blocker")


def test_lint_kind_rules():
    assert has(lint(entry(kind="1x")), "kind-invalid", "blocker")
    assert has(lint(entry(kind="Widget")), "kind-unknown", "minor")
    for kind in ("Component", "API", "Resource", "System", "Domain", "Group", "User", "Location", "Template"):
        assert lacks(lint(entry(kind=kind)), "kind-unknown")


def test_lint_namespace_follows_the_upstream_dns_label_rule():
    # upstream isValidNamespace = lowercase DNS label: [a-z0-9]+(-+[a-z0-9]+)*, 1..63 chars (a--b valid; AZ09, a_b, a.b invalid)
    for good in ("default", "team-a", "a--b", "9", "x" * 63):
        assert lacks(lint(entry(namespace=good)), "namespace-invalid"), good
    for bad in ("Bad Ns", "Team-A", "a_b", "a.b", "-a", "a-", "x" * 64):
        assert has(lint(entry(namespace=bad)), "namespace-invalid", "blocker"), bad
    assert lint(entry(namespace="team-a", owner=None))["findings"][0]["id"] == "owner-missing@component:team-a/payments-api"
    # an entity whose name or namespace is not echo-safe is addressed by its position, never by the raw text
    assert lint(entry(namespace="Bad Ns", owner=None))["findings"][0]["id"].endswith("@entry#0")


def test_lint_duplicate_entities_are_a_blocker_case_insensitively():
    result = lint(entry(), entry(name="Payments-API"))
    assert has(result, "duplicate-entity", "blocker", "component:default/payments-api")
    assert "entry #0" in next(f for f in result["findings"] if f["id"].startswith("duplicate-entity"))["summary"]
    assert lacks(lint(entry(), entry(namespace="other")), "duplicate-entity")


def test_lint_owner_rules():
    assert has(lint(entry(owner=None)), "owner-missing", "blocker")
    assert has(lint(entry(owner="   ")), "owner-missing", "blocker")
    assert has(lint(entry(owner="not a ref")), "owner-invalid", "major")
    assert has(lint(entry(owner="component:default/x")), "owner-kind", "major")
    for owner in ("payments", "group:payments", "user:default/jane", "Group:Default/Payments"):
        result = lint(entry(owner=owner))
        assert lacks(result, "owner-invalid") and lacks(result, "owner-kind") and lacks(result, "owner-missing")


@pytest.mark.parametrize("kind, owner_required", [("Component", True), ("API", True), ("Resource", True), ("System", True), ("Domain", True),
                                                   ("Group", False), ("User", False), ("Location", False), ("Template", False)])
def test_lint_owner_is_required_only_for_the_kinds_that_need_one(kind, owner_required):
    result = lint({"kind": kind, "name": "thing"})
    assert has(result, "owner-missing", "blocker") is owner_required


def test_lint_group_and_template_need_a_type_and_nothing_else_here():
    # Backstage: Group spec.type and Template spec.type are required; their owner is not
    assert lint({"kind": "Group", "name": "payments", "type": "team"})["status"] == "pass"
    assert has(lint({"kind": "Group", "name": "payments"}), "type-missing", "major")
    assert has(lint({"kind": "Template", "name": "tpl"}), "type-missing", "major")
    assert lint({"kind": "User", "name": "jane"})["status"] == "pass"
    assert lint({"kind": "Location", "name": "loc"})["status"] == "pass"


@pytest.mark.parametrize("kind, needed", [("Component", True), ("API", True), ("Resource", True), ("Group", True), ("Template", True), ("System", False), ("Domain", False)])
def test_lint_type_is_required_for_the_kinds_whose_spec_type_is_required(kind, needed):
    assert has(lint({"kind": kind, "name": "t", "owner": "g"}), "type-missing", "major") is needed


def test_lint_lifecycle_rules():
    assert has(lint(entry(lifecycle=None)), "lifecycle-missing", "blocker")
    assert has(lint({"kind": "API", "name": "a", "owner": "g"}), "lifecycle-missing", "blocker")
    assert lacks(lint({"kind": "Resource", "name": "r", "owner": "g"}), "lifecycle-missing")
    assert has(lint(entry(lifecycle="prod")), "lifecycle-nonstandard", "minor")
    assert has(lint(entry(lifecycle="Production")), "lifecycle-nonstandard", "minor")
    for value in ("experimental", "production", "deprecated"):
        assert lacks(lint(entry(lifecycle=value)), "lifecycle-nonstandard")


def test_lint_system_rules_depend_on_lifecycle():
    assert has(lint(entry(system=None)), "system-missing", "major")
    assert has(lint(entry(system=None, lifecycle="experimental")), "system-missing", "minor")
    assert has(lint(entry(system="component:default/x")), "system-invalid", "major")
    assert has(lint(entry(system="bad system")), "system-invalid", "major")
    assert lacks(lint(entry(system="system:default/checkout")), "system-invalid")
    assert lacks(lint({"kind": "System", "name": "s", "owner": "g"}), "system-missing")


def test_lint_tier_rules_and_custom_tiers():
    assert has(lint(entry(tier=None)), "tier-missing", "major")
    assert has(lint(entry(tier=None, lifecycle="experimental")), "tier-missing", "minor")
    assert has(lint(entry(tier="tier-9")), "tier-unknown", "minor")
    for tier in ("tier-0", "tier-1", "tier-2", "tier-3"):
        assert lacks(lint(entry(tier=tier)), "tier-unknown")
    assert lacks(lint(entry(tier="gold"), allowed_tiers=["gold", "silver"]), "tier-unknown")
    assert has(lint(entry(tier="tier-1"), allowed_tiers=["gold"]), "tier-unknown", "minor")
    assert lacks(lint({"kind": "API", "name": "a", "owner": "g", "lifecycle": "production"}), "tier-missing")


def test_lint_docs_and_source_annotations():
    bare = entry(annotations=[])
    assert has(lint(bare), "docs-missing", "major")
    assert has(lint(entry(annotations=[], lifecycle="experimental")), "docs-missing", "minor")
    assert has(lint(bare), "source-location-missing", "info")
    assert lacks(lint(entry(annotations=["backstage.io/techdocs-ref", "backstage.io/source-location", "x/runbook"])), "docs-missing")
    assert has(lint({"kind": "System", "name": "s", "owner": "g"}), "docs-missing", "minor")
    assert lacks(lint({"kind": "Resource", "name": "r", "owner": "g", "type": "db"}), "docs-missing")


def test_lint_runbook_rules():
    base = ["backstage.io/techdocs-ref", "backstage.io/source-location"]
    assert has(lint(entry(annotations=base)), "runbook-missing", "major")  # production tier-1
    assert has(lint(entry(annotations=base, tier="tier-0")), "runbook-missing", "major")
    assert has(lint(entry(annotations=base, tier="tier-3")), "runbook-missing", "minor")
    assert lacks(lint(entry(annotations=base, lifecycle="experimental")), "runbook-missing")
    for key in ("example.com/runbook", "example.com/Runbook-URL", "runbook", "x.io/runbook_url", "x.io/runbook-link"):
        assert lacks(lint(entry(annotations=[*base, key])), "runbook-missing"), key
    assert has(lint(entry(annotations=[*base, "example.com/runbooks"])), "runbook-missing", "major")
    assert lacks(lint(entry(annotations=base, links=["On-call Runbook"])), "runbook-missing")
    assert has(lint(entry(annotations=base, links=["Dashboard"])), "runbook-missing", "major")


def test_lint_description_is_info_only_and_not_required_for_groups():
    result = lint(entry(description=None))
    assert rules(result) == [("description-missing", "info")] and result["status"] == "pass"


def test_lint_depends_on_rules():
    result = lint(entry(depends_on=["bad dep!", "component:default/payments-api", "db", "resource:default/db", "Resource:Default/DB", "db"]))
    assert has(result, "dependson-invalid", "major")
    assert has(result, "dependson-self", "major")
    dup = [f for f in result["findings"] if f["id"].startswith("dependson-duplicate")]
    assert len(dup) == 2  # the second `db` and the second resource:default/db spelling
    clean = lint(entry(depends_on=["resource:default/db", "user-service"]))
    assert clean["findings"] == []


def test_lint_findings_are_cut_at_300_with_the_true_total():
    result = lint(*[{"kind": "Component", "name": f"svc-{i}"} for i in range(200)])
    assert result["finding_count"] == 1600 and len(result["findings"]) == 300
    assert result["findings"][0]["severity"] == "blocker"
    assert result["blocker_count"] == 400 and result["major_count"] == 200


def test_lint_invalid_input():
    invalid("plat_catalog_lint", {"entries": [CLEAN], "allowed_tiers": ["tier-1", "  "]}, "allowed_tiers")
    invalid("plat_catalog_lint", {"entries": []}, "entries")
    invalid("plat_catalog_lint", {"entries": [{"kind": "Component"}]}, "entries")


# --- plat_template_params_check -------------------------------------------------------------------------------


def tparam(name, type_="string", required=True, **extra):
    return {"name": name, "type": type_, "required": required, **extra}


def check(template, golden):
    return ok("plat_template_params_check", {"template_parameters": template, "golden_inputs": golden})


def test_template_params_compatible_exact_output():
    result = check(
        [tparam("region", enum_values=["eu-west-1"]), tparam("owner"), tparam("note", required=False)],
        [tparam("region", allowed_values=["eu-west-1", "us-east-1"]), tparam("owner")],
    )
    assert result == {
        "state": "SUCCESS", "parameter_count": 3, "golden_input_count": 2, "missing_required_count": 0, "type_mismatch_count": 0,
        "extra_parameter_count": 1, "finding_count": 1, "verdict": "compatible",
        "findings": [{"id": "extra-parameter@note", "severity": "info", "summary": "template parameter is not part of the golden-path contract"}],
    }


def test_template_params_missing_required_is_a_blocker_and_missing_optional_is_info():
    result = check([tparam("a")], [tparam("a"), tparam("b"), tparam("c", required=False)])
    assert rules(result) == [("input-missing", "blocker"), ("optional-input-absent", "info")]
    assert (result["missing_required_count"], result["verdict"]) == (1, "incompatible")
    assert result["findings"][0]["id"] == "input-missing@b"


def test_template_params_type_rules():
    assert has(check([tparam("n", "integer")], [tparam("n", "string")]), "type-mismatch", "major")
    assert check([tparam("n", "integer")], [tparam("n", "string")])["type_mismatch_count"] == 1
    assert lacks(check([tparam("n", "integer")], [tparam("n", "number")]), "type-mismatch")  # integer narrows number
    assert has(check([tparam("n", "number")], [tparam("n", "integer")]), "type-mismatch", "major")  # number widens integer


def test_template_params_required_enforcement_needs_a_default_or_required():
    assert has(check([tparam("a", required=False)], [tparam("a")]), "required-not-enforced", "major")
    assert lacks(check([tparam("a", required=False, default="x")], [tparam("a")]), "required-not-enforced")
    assert lacks(check([tparam("a", required=False)], [tparam("a", required=False)]), "required-not-enforced")
    assert lacks(check([tparam("a", required=False, default="")], [tparam("a")]), "required-not-enforced")  # an empty default is a default


def test_template_params_allowed_values():
    golden = [tparam("size", allowed_values=["s", "m"])]
    assert has(check([tparam("size")], golden), "unrestricted-values", "major")
    assert has(check([tparam("size", enum_values=[])], golden), "unrestricted-values", "major")
    outside = check([tparam("size", enum_values=["m", "xl", "l"])], golden)
    assert [f["summary"] for f in outside["findings"]] == ["template offers 2 value(s) outside the golden-path allowed values"]
    assert lacks(check([tparam("size", enum_values=["m"])], golden), "values-not-allowed")
    assert has(check([tparam("size", enum_values=["s"], default="xl")], golden), "default-not-allowed", "major")
    assert lacks(check([tparam("size", enum_values=["s"], default="s")], golden), "default-not-allowed")
    assert lacks(check([tparam("size")], [tparam("size", allowed_values=[])]), "unrestricted-values")


def test_template_params_default_differs_is_minor():
    result = check([tparam("r", default="eu")], [tparam("r", default="us")])
    assert rules(result) == [("default-differs", "minor")] and result["verdict"] == "compatible"
    assert lacks(check([tparam("r", default="us")], [tparam("r", default="us")]), "default-differs")


def test_template_params_invalid_input():
    invalid("plat_template_params_check", {"template_parameters": [tparam("a"), tparam("a")], "golden_inputs": []}, "template_parameters")
    invalid("plat_template_params_check", {"template_parameters": [], "golden_inputs": [tparam("  ")]}, "golden_inputs")


# --- plat_composition_diff ------------------------------------------------------------------------------------


def fld(path, type_="string", required=False, **extra):
    return {"path": path, "type": type_, "required": required, **extra}


def diff(old, new, old_version="v1alpha1", new_version="v1beta1"):
    return ok("plat_composition_diff", {"old_version": old_version, "new_version": new_version, "old_fields": old, "new_fields": new})


def test_diff_identical_schemas_are_compatible():
    f = [fld("a", required=True, enum_values=["x"], minimum=1, maximum=2)]
    assert diff(f, copy.deepcopy(f)) == {
        "state": "SUCCESS", "breaking_count": 0, "additive_count": 0, "change_class": "none", "requires_new_version": False,
        "finding_count": 0, "verdict": "compatible", "findings": [],
    }


def test_diff_added_fields_optional_is_additive_required_is_breaking():
    result = diff([fld("a")], [fld("a"), fld("b"), fld("c", required=True)])
    assert (result["breaking_count"], result["additive_count"], result["change_class"], result["requires_new_version"]) == (1, 1, "breaking", True)
    assert has(result, "field-added", "info", "b") and has(result, "field-added-required", "major", "c")
    assert result["verdict"] == "breaking-new-version"


def test_diff_removed_field_is_breaking_even_when_optional():
    result = diff([fld("a"), fld("b", required=True)], [fld("a")])
    assert has(result, "field-removed", "major", "b") and result["breaking_count"] == 1


def test_diff_same_version_breaking_change_is_a_blocker():
    result = diff([fld("a")], [], old_version="v1", new_version="v1")
    assert result["verdict"] == "breaking-same-version"
    assert has(result, "breaking-change-in-same-version", "blocker", "v1")
    assert result["breaking_count"] == 1 and result["finding_count"] == 2
    assert diff([fld("a")], [fld("a"), fld("b")], old_version="v1", new_version="v1")["verdict"] == "compatible"


def test_diff_type_changes():
    assert has(diff([fld("n", "integer")], [fld("n", "number")]), "type-widened", "info")
    widened = diff([fld("n", "integer")], [fld("n", "number")])
    assert (widened["breaking_count"], widened["additive_count"], widened["change_class"]) == (0, 1, "additive")
    for before, after in (("number", "integer"), ("string", "integer"), ("boolean", "string")):
        result = diff([fld("n", before)], [fld("n", after)])
        assert has(result, "type-changed", "major") and result["breaking_count"] == 1
    # constraint drift is not compared on top of an incompatible type change
    assert diff([fld("n", "string", minimum=1)], [fld("n", "integer", minimum=5)])["finding_count"] == 1


def test_diff_required_flag():
    assert has(diff([fld("a")], [fld("a", required=True)]), "required-added", "major")
    assert has(diff([fld("a", required=True)], [fld("a")]), "required-dropped", "info")


def test_diff_enum_changes():
    assert has(diff([fld("a")], [fld("a", enum_values=["x"])]), "enum-added", "major")
    assert has(diff([fld("a", enum_values=["x"])], [fld("a")]), "enum-dropped", "info")
    both = diff([fld("a", enum_values=["x", "y"])], [fld("a", enum_values=["y", "z"])])
    assert has(both, "enum-values-removed", "major") and has(both, "enum-values-added", "info")
    assert [f["summary"] for f in both["findings"]] == ["1 value(s) no longer accepted", "1 value(s) now accepted"]
    assert diff([fld("a", enum_values=["x"])], [fld("a", enum_values=["x", "y"])])["breaking_count"] == 0
    assert diff([fld("a", enum_values=[])], [fld("a")])["finding_count"] == 0  # an empty enum is no enum


@pytest.mark.parametrize("key, rule, old, new, severity", [
    ("minimum", "minimum-tightened", 1, 2, "major"), ("minimum", "minimum-loosened", 2, 1, "info"),
    ("maximum", "maximum-tightened", 10, 5, "major"), ("maximum", "maximum-loosened", 5, 10, "info"),
    ("min_length", "min-length-tightened", 1, 3, "major"), ("min_length", "min-length-loosened", 3, 1, "info"),
    ("max_length", "max-length-tightened", 63, 30, "major"), ("max_length", "max-length-loosened", 30, 63, "info"),
])
def test_diff_bounds_tightening_breaks_and_loosening_is_additive(key, rule, old, new, severity):
    typed = "string" if "length" in key else "number"
    result = diff([fld("a", typed, **{key: old})], [fld("a", typed, **{key: new})])
    assert rules(result) == [(rule, severity)]
    assert result["breaking_count"] == (1 if severity == "major" else 0)


@pytest.mark.parametrize("key, rule_stem", [("minimum", "minimum"), ("maximum", "maximum"), ("min_length", "min-length"), ("max_length", "max-length")])
def test_diff_adding_a_bound_breaks_and_removing_it_is_additive_and_equal_bounds_are_silent(key, rule_stem):
    typed = "string" if "length" in key else "number"
    assert rules(diff([fld("a", typed)], [fld("a", typed, **{key: 3})])) == [(f"{rule_stem}-added", "major")]
    assert rules(diff([fld("a", typed, **{key: 3})], [fld("a", typed)])) == [(f"{rule_stem}-removed", "info")]
    assert diff([fld("a", typed, **{key: 3})], [fld("a", typed, **{key: 3})])["finding_count"] == 0


def test_diff_decimal_bounds_compare_exactly():
    assert diff([fld("a", "number", minimum=0.1)], [fld("a", "number", minimum=0.10)])["finding_count"] == 0
    assert rules(diff([fld("a", "number", maximum=0.3)], [fld("a", "number", maximum=0.30000000000000004)])) == [("maximum-loosened", "info")]


def test_diff_invalid_input():
    invalid("plat_composition_diff", {"old_version": "v1", "new_version": "v2", "old_fields": [fld("a"), fld("a")], "new_fields": []}, "old_fields")
    invalid("plat_composition_diff", {"old_version": "v1", "new_version": "v2", "old_fields": [], "new_fields": [fld("a..b")]}, "new_fields")
    invalid("plat_composition_diff", {"old_version": "v1", "new_version": "v2", "old_fields": [fld("a", "number", minimum=5, maximum=1)], "new_fields": []}, "old_fields")
    invalid("plat_composition_diff", {"old_version": "v1", "new_version": "v2", "old_fields": [], "new_fields": [fld("a", min_length=5, max_length=1)]}, "new_fields")
    invalid("plat_composition_diff", {"old_version": "v1", "new_version": "v2", "old_fields": [], "new_fields": [fld(" a")]}, "new_fields")


# --- plat_claim_validate --------------------------------------------------------------------------------------


def cl(path, type_, value):
    return {"path": path, "type": type_, "value": value}


def validate(schema, claim):
    return ok("plat_claim_validate", {"schema": schema, "claim": claim})


SCHEMA = [
    fld("parameters.size", required=True, enum_values=["small", "large"]),
    fld("parameters.replicas", "integer", minimum=1, maximum=5),
    fld("parameters.name", min_length=3, max_length=8),
    fld("parameters.ratio", "number", minimum=0.5, maximum=1.5),
    fld("parameters.backup", "boolean"),
]


def test_claim_valid_exact_output():
    claim = [cl("parameters.size", "string", "small"), cl("parameters.replicas", "integer", "3"), cl("parameters.backup", "boolean", "true")]
    assert validate(SCHEMA, claim) == {"state": "SUCCESS", "checked_count": 3, "violation_count": 0, "valid": True, "finding_count": 0, "findings": []}


def test_claim_missing_required_field():
    result = validate(SCHEMA, [cl("parameters.backup", "boolean", "false")])
    assert rules(result) == [("required-missing", "blocker")] and result["findings"][0]["id"] == "required-missing@parameters.size"
    assert (result["violation_count"], result["valid"]) == (1, False)


def test_claim_required_binds_only_when_the_parent_object_exists():
    schema = [fld("db.size", required=True), fld("db.name"), fld("top", required=True)]
    assert [f["id"] for f in validate(schema, [])["findings"]] == ["required-missing@top"]
    assert [f["id"] for f in validate(schema, [cl("top", "string", "x"), cl("db.name", "string", "n")])["findings"]] == ["required-missing@db.size"]
    assert validate(schema, [cl("top", "string", "x")])["valid"] is True
    nested = [fld("a", "object", required=True), fld("a.b", required=True)]
    assert [f["id"] for f in validate(nested, [])["findings"]] == ["required-missing@a"]


def test_claim_unknown_field_is_major():
    result = validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.color", "string", "red")])
    assert rules(result) == [("unknown-field", "major")] and result["valid"] is False and result["violation_count"] == 1


def test_claim_type_rules():
    result = validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.replicas", "string", "3")])
    assert rules(result) == [("type-mismatch", "blocker")]
    # an integer literal satisfies a number schema; a whole number literal satisfies an integer schema; a fraction does not
    assert validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.ratio", "integer", "1")])["valid"] is True
    assert validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.replicas", "number", "3.0")])["valid"] is True
    assert has(validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.replicas", "number", "3.5")]), "type-mismatch", "blocker")
    # after a type mismatch no other rule is applied to that field
    assert validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.replicas", "string", "99")])["finding_count"] == 1


def test_claim_enum_rules():
    result = validate(SCHEMA, [cl("parameters.size", "string", "huge")])
    assert rules(result) == [("enum-violation", "blocker")] and result["findings"][0]["summary"] == "value is not one of the 2 allowed values"
    numeric = [fld("n", "number", enum_values=["2.0", "4"])]
    assert validate(numeric, [cl("n", "integer", "2")])["valid"] is True
    assert validate(numeric, [cl("n", "number", "4.00")])["valid"] is True
    assert has(validate(numeric, [cl("n", "number", "3")]), "enum-violation", "blocker")


@pytest.mark.parametrize("value, rule", [("1", None), ("0", "below-minimum"), ("5", None), ("6", "above-maximum")])
def test_claim_integer_bounds_are_inclusive(value, rule):
    result = validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.replicas", "integer", value)])
    assert [r for r, _ in rules(result)] == ([rule] if rule else [])


@pytest.mark.parametrize("value, rule", [("0.5", None), ("0.49", "below-minimum"), ("1.5", None), ("1.51", "above-maximum"), ("1.5e0", None), ("1.4999e0", None)])
def test_claim_number_bounds_are_exact_decimals(value, rule):
    result = validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.ratio", "number", value)])
    assert [r for r, _ in rules(result)] == ([rule] if rule else [])


@pytest.mark.parametrize("value, rule", [("abc", None), ("ab", "too-short"), ("abcdefgh", None), ("abcdefghi", "too-long"), ("ääää", None)])
def test_claim_string_length_bounds_count_characters(value, rule):
    result = validate(SCHEMA, [cl("parameters.size", "string", "small"), cl("parameters.name", "string", value)])
    assert [r for r, _ in rules(result)] == ([rule] if rule else [])


def test_claim_invalid_input():
    base = [cl("parameters.size", "string", "small")]
    for bad in (cl("parameters.backup", "boolean", "yes"), cl("parameters.replicas", "integer", "1.5"), cl("parameters.replicas", "integer", "--5"),
                cl("parameters.ratio", "number", "abc"), cl("parameters.ratio", "number", "1e500"), cl("parameters.ratio", "number", " 1"),
                cl("parameters.ratio", "number", "1_0")):
        invalid("plat_claim_validate", {"schema": SCHEMA, "claim": [*base, bad]}, "claim")
    invalid("plat_claim_validate", {"schema": SCHEMA, "claim": [*base, *base]}, "claim")
    invalid("plat_claim_validate", {"schema": [fld("a"), fld("a")], "claim": []}, "schema")
    invalid("plat_claim_validate", {"schema": [fld("a", "number", minimum=3, maximum=2)], "claim": []}, "schema")


# --- plat_maturity_scorecard ----------------------------------------------------------------------------------


def svc(name="s", tier="tier-3", **over):
    base = {"name": name, "tier": tier, "has_owner": True, "has_runbook": True, "has_slo": True, "has_on_call": True, "test_coverage_pct": 80,
            "sast": True, "dependency_scan": True, "secret_scan": True}
    return {**base, **over}


def score(*services, **extra):
    return ok("plat_maturity_scorecard", {"services": list(services), **extra})


def test_scorecard_gold_service_exact_output():
    assert score(svc("api", "tier-0")) == {
        "state": "SUCCESS", "service_count": 1, "average_level": 3.0, "level_counts": {"none": 0, "bronze": 0, "silver": 0, "gold": 1}, "below_required_count": 0,
        "services": [{"name": "api", "level": 3, "level_name": "gold", "passed": 8, "total": 8, "gaps": [], "meets_required": True}],
        "finding_count": 0, "status": "pass", "findings": [],
    }


@pytest.mark.parametrize("over, level, gaps", [
    ({"secret_scan": False}, 2, ["secret-scan"]),
    ({"sast": False, "dependency_scan": False}, 2, ["sast", "dependency-scan"]),
    ({"has_slo": False}, 1, ["slo"]),
    ({"has_on_call": False}, 1, ["on-call"]),
    ({"test_coverage_pct": 69.9}, 1, ["test-coverage"]),
    ({"has_runbook": False}, 0, ["runbook"]),
    ({"has_owner": False, "has_runbook": False}, 0, ["owner", "runbook"]),
])
def test_scorecard_levels_are_cumulative(over, level, gaps):
    row = score(svc(**over))["services"][0]
    assert (row["level"], row["level_name"], row["gaps"]) == (level, ("none", "bronze", "silver", "gold")[level], gaps)


def test_scorecard_gold_checks_without_bronze_do_not_lift_the_level():
    row = score(svc(has_owner=False))["services"][0]
    assert row["level"] == 0 and row["passed"] == 7


def test_scorecard_coverage_threshold_boundary_and_override():
    assert score(svc(test_coverage_pct=70))["services"][0]["level"] == 3
    assert score(svc(test_coverage_pct=69.99))["services"][0]["level"] == 1
    assert score(svc(test_coverage_pct=80), min_coverage_pct=80.5)["services"][0]["level"] == 1
    assert score(svc(test_coverage_pct=80), min_coverage_pct=80)["services"][0]["level"] == 3


def test_scorecard_required_levels_by_tier_default_and_override():
    low = {"has_slo": False}  # silver gap: level bronze
    result = score(svc("a", "tier-0", **low), svc("b", "tier-1", **low), svc("c", "tier-2", **low), svc("d", "tier-3", has_owner=False))
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("below-required-level@a", "major"), ("below-required-level@b", "major")]
    assert "tier-0 requires gold, service is bronze; gaps: slo" in result["findings"][0]["summary"]
    assert result["below_required_count"] == 2 and result["status"] == "fail"
    assert [r["meets_required"] for r in result["services"]] == [False, False, True, True]
    override = {"tier_0": 0, "tier_1": 0, "tier_2": 2, "tier_3": 0}
    relaxed = score(svc("a", "tier-0", **low), svc("c", "tier-2", **low), required_levels=override)
    assert [(f["id"], f["severity"]) for f in relaxed["findings"]] == [("below-required-level@c", "minor")] and relaxed["status"] == "warn"


def test_scorecard_counts_average_and_sorting():
    result = score(svc("z", has_owner=False), svc("m", has_slo=False), svc("a"), svc("b"))
    assert [r["name"] for r in result["services"]] == ["a", "b", "m", "z"]
    assert result["level_counts"] == {"none": 1, "bronze": 1, "silver": 0, "gold": 2}
    assert result["average_level"] == 1.75


def test_scorecard_invalid_input():
    invalid("plat_maturity_scorecard", {"services": [svc("a"), svc("a")]}, "services")
    invalid("plat_maturity_scorecard", {"services": [svc("  ")]}, "services")


# --- plat_dora_bands ------------------------------------------------------------------------------------------


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def dora(deployments, incidents=(), days=7, start=T0):
    return ok("plat_dora_bands", {"window_start": iso(start), "window_end": iso(start + timedelta(days=days)), "deployments": list(deployments), "incidents": list(incidents)})


def dep(i, lead_h=2.0, at=None, base=T0):
    deployed = at if at is not None else base + timedelta(hours=12 + i)
    return {"id": f"d{i}", "committed_at": iso(deployed - timedelta(hours=lead_h)), "deployed_at": iso(deployed)}


def inc(i, after_min=10, resolve_min=40, resolved=True, base=T0):
    deployed = base + timedelta(hours=12 + i)
    out = {"started_at": iso(deployed + timedelta(minutes=after_min)), "deployment_id": f"d{i}"}
    if resolved:
        out["resolved_at"] = iso(deployed + timedelta(minutes=resolve_min))
    return out


def test_dora_mixed_window_exact_output():
    result = dora([dep(i) for i in range(7)], [inc(2, resolve_min=30)])
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "deployment_count": 7, "failed_deployment_count": 1, "unresolved_failure_count": 0, "deployments_per_day": 1.0,
        "median_lead_time_hours": 2.0, "change_failure_rate": 0.1429, "median_recovery_hours": 0.5, "frequency_band": "elite", "lead_time_band": "elite",
        "change_failure_band": "medium", "recovery_band": "elite", "overall_band": "medium", "finding_count": 1,
    }
    assert rules(result) == [("change-failure-rate-medium", "minor")]


@pytest.mark.parametrize("count, days, band", [(1, 1, "elite"), (1, 7, "high"), (2, 14, "high"), (2, 15, "medium"), (1, 8, "medium"), (1, 30, "medium"), (1, 31, "low"), (2, 60, "medium"), (2, 61, "low")])
def test_dora_frequency_band_boundaries(count, days, band):
    # elite >= 1/day, high >= 1/week, medium >= 1/30 days (exact fractions of the window)
    result = dora([dep(i, at=T0 + timedelta(hours=1 + i)) for i in range(count)], days=days)
    assert result["frequency_band"] == band


@pytest.mark.parametrize("lead_h, band", [(23.99, "elite"), (24, "high"), (167.99, "high"), (168, "medium"), (719.99, "medium"), (720, "low")])
def test_dora_lead_time_band_boundaries(lead_h, band):
    deployments = [dep(i, lead_h=lead_h, at=T0 + timedelta(days=1 + i)) for i in range(3)]
    assert dora(deployments)["lead_time_band"] == band


def test_dora_lead_time_is_the_median_not_the_mean():
    leads = [1, 2, 100]  # mean 34.3 h, median 2 h
    result = dora([dep(i, lead_h=h, at=T0 + timedelta(days=1 + i)) for i, h in enumerate(leads)])
    assert result["median_lead_time_hours"] == 2.0 and result["lead_time_band"] == "elite"
    even = dora([dep(i, lead_h=h, at=T0 + timedelta(days=1 + i)) for i, h in enumerate([10, 20])])
    assert even["median_lead_time_hours"] == 15.0


@pytest.mark.parametrize("failures, band", [(1, "elite"), (2, "high"), (3, "medium"), (4, "low")])
def test_dora_change_failure_rate_band_boundaries(failures, band):
    # 20 deployments: 1/20 = 5% elite (inclusive), 2/20 = 10% high, 3/20 = 15% medium, 4/20 = 20% low
    result = dora([dep(i) for i in range(20)], [inc(i) for i in range(failures)], days=20)
    assert result["change_failure_band"] == band and result["change_failure_rate"] == failures / 20


@pytest.mark.parametrize("minutes, band", [(59, "elite"), (60, "high"), (1439, "high"), (1440, "medium"), (10079, "medium"), (10080, "low")])
def test_dora_recovery_band_boundaries(minutes, band):
    result = dora([dep(i) for i in range(7)], [inc(0, resolve_min=minutes)])
    assert result["recovery_band"] == band and result["median_recovery_hours"] == round(minutes / 60, 4)


def test_dora_unresolved_failure_pins_recovery_to_low_and_is_reported():
    result = dora([dep(i) for i in range(7)], [inc(1, resolved=False), inc(2)])
    assert (result["unresolved_failure_count"], result["failed_deployment_count"], result["recovery_band"]) == (1, 2, "low")
    assert result["median_recovery_hours"] == 0.6667  # only the resolved failure (40 min) contributes
    assert has(result, "unresolved-failures", "major") and has(result, "recovery-time-low", "major")


def test_dora_no_failures_leaves_recovery_out_of_the_overall_band():
    result = dora([dep(i) for i in range(7)])
    assert (result["recovery_band"], result["median_recovery_hours"], result["change_failure_rate"], result["change_failure_band"]) == ("none", -1.0, 0.0, "elite")
    assert result["overall_band"] == "elite" and result["findings"] == []


def test_dora_two_incidents_on_one_deployment_count_once_and_use_the_latest_resolution():
    result = dora([dep(i) for i in range(7)], [inc(3, resolve_min=30), inc(3, after_min=20, resolve_min=90)])
    # recovery runs from deployed_at to the latest resolved_at: the latest resolution is +90 min, so 1.5 h
    assert (result["failed_deployment_count"], result["median_recovery_hours"]) == (1, 1.5)
    unresolved = dora([dep(i) for i in range(7)], [inc(3, resolve_min=30), inc(3, after_min=20, resolved=False)])
    assert unresolved["unresolved_failure_count"] == 1 and unresolved["median_recovery_hours"] == -1.0


def test_dora_window_membership_is_start_inclusive_end_exclusive_and_outside_failures_are_ignored():
    inside = dep(0, at=T0)
    at_end = dep(1, at=T0 + timedelta(days=7))
    before = dep(2, at=T0 - timedelta(seconds=1))
    late_incident = {"started_at": iso(T0 + timedelta(days=7, minutes=5)), "deployment_id": "d1"}
    result = dora([inside, at_end, before], [late_incident])
    assert (result["deployment_count"], result["failed_deployment_count"]) == (1, 0)


def test_dora_incidents_without_a_deployment_are_not_change_failures():
    result = dora([dep(i) for i in range(7)], [{"started_at": iso(T0 + timedelta(hours=30)), "deployment_id": ""}, {"started_at": iso(T0 + timedelta(hours=31))}])
    assert result["failed_deployment_count"] == 0


def test_dora_no_deployments_in_window():
    result = dora([dep(0, at=T0 - timedelta(days=1))])
    assert result["overall_band"] == "none" and (result["frequency_band"], result["lead_time_band"]) == ("none", "none")
    assert (result["median_lead_time_hours"], result["median_recovery_hours"], result["deployments_per_day"]) == (-1.0, -1.0, 0.0)
    assert rules(result) == [("no-deployments", "major")]


def test_dora_overall_is_the_weakest_applicable_band():
    result = dora([dep(i, lead_h=200) for i in range(7)])
    assert (result["frequency_band"], result["lead_time_band"], result["overall_band"]) == ("elite", "medium", "medium")
    assert has(result, "lead-time-medium", "minor")
    slow = dora([dep(0, lead_h=800)], days=7)
    assert slow["overall_band"] == "low" and has(slow, "lead-time-low", "major")


def test_dora_invalid_input():
    good = {"window_start": iso(T0), "window_end": iso(T0 + timedelta(days=1)), "deployments": [], "incidents": []}
    invalid("plat_dora_bands", {**good, "window_start": "2026-09-01T00:00:00"}, "window_start")
    invalid("plat_dora_bands", {**good, "window_end": "not-a-time"}, "window_end")
    invalid("plat_dora_bands", {**good, "window_end": good["window_start"]}, "window_end")
    invalid("plat_dora_bands", {**good, "deployments": [dep(1), dep(1)]}, "deployments")
    invalid("plat_dora_bands", {**good, "deployments": [{"id": "x", "committed_at": "2026-09-01T10:00:00Z", "deployed_at": "2026-09-01T09:00:00Z"}]}, "deployments")
    invalid("plat_dora_bands", {**good, "deployments": [{"id": "x", "committed_at": "2026-09-01T10:00:00Z", "deployed_at": "2026-09-01 11:00:00"}]}, "deployments")
    invalid("plat_dora_bands", {**good, "deployments": [dep(1)], "incidents": [{"started_at": iso(T0 + timedelta(days=1)), "deployment_id": "nope"}]}, "incidents")
    invalid("plat_dora_bands", {**good, "deployments": [dep(1)], "incidents": [{"started_at": iso(T0), "deployment_id": "d1"}]}, "incidents")
    invalid("plat_dora_bands", {**good, "deployments": [dep(1)], "incidents": [{"started_at": iso(T0 + timedelta(days=1)), "resolved_at": iso(T0), "deployment_id": "d1"}]}, "incidents")


def test_dora_offsets_are_compared_as_instants():
    deployed = "2026-09-02T14:00:00+02:00"  # 12:00Z
    result = ok("plat_dora_bands", {"window_start": "2026-09-02T12:00:00Z", "window_end": "2026-09-03T12:00:00Z",
                                     "deployments": [{"id": "a", "committed_at": "2026-09-02T09:00:00Z", "deployed_at": deployed}], "incidents": []})
    assert result["deployment_count"] == 1 and result["median_lead_time_hours"] == 3.0


# --- plat_catalog_graph ---------------------------------------------------------------------------------------


def node(ref, deps=(), lifecycle="production", owner="group:default/t"):
    out = {"ref": ref, "depends_on": list(deps)}
    if lifecycle:
        out["lifecycle"] = lifecycle
    if owner:
        out["owner"] = owner
    return out


def graph(*entries):
    return ok("plat_catalog_graph", {"entries": list(entries)})


def test_graph_chain_exact_output():
    result = graph(node("component:default/web", ["component:default/api"]), node("component:default/api", ["resource:default/db"]), node("resource:default/db"))
    assert result == {
        "state": "SUCCESS", "entry_count": 3, "edge_count": 2, "cycle_count": 0, "cycles": [], "dangling_count": 0, "orphan_count": 0, "unowned_count": 0, "max_depth": 2,
        "most_depended_on": [
            {"ref": "resource:default/db", "direct_dependents": 1, "transitive_dependents": 2},
            {"ref": "component:default/api", "direct_dependents": 1, "transitive_dependents": 1},
        ],
        "finding_count": 0, "status": "pass", "findings": [],
    }


def test_graph_references_are_normalised_with_default_kind_and_namespace():
    result = graph(node("Web", ["API", "resource:db"]), node("component:default/api"), node("Resource:Default/DB"))
    assert (result["edge_count"], result["dangling_count"], result["max_depth"]) == (2, 0, 1)


def test_graph_cycle_is_a_blocker_and_collapses_for_depth():
    result = graph(node("a", ["b"]), node("b", ["a"]), node("c", ["a"]))
    assert result["cycles"] == [["component:default/a", "component:default/b"]] and result["cycle_count"] == 1
    assert result["findings"][0]["id"] == "dependency-cycle@component:default/a" and result["findings"][0]["severity"] == "blocker"
    assert "component:default/a -> component:default/b -> component:default/a" in result["findings"][0]["summary"]
    assert (result["max_depth"], result["status"]) == (1, "fail")
    top = result["most_depended_on"][0]
    assert (top["ref"], top["transitive_dependents"], top["direct_dependents"]) == ("component:default/a", 2, 2)


def test_graph_self_dependency_is_a_cycle_of_one():
    result = graph(node("a", ["a"]))
    assert result["cycles"] == [["component:default/a"]] and rules(result) == [("self-dependency", "blocker")]
    assert result["orphan_count"] == 0 and result["most_depended_on"] == []


def test_graph_dangling_orphan_and_unowned():
    result = graph(node("a", ["ghost"]), node("lonely"), node("api:default/spec", owner=None), node("group:default/g"))
    assert (result["dangling_count"], result["orphan_count"], result["unowned_count"]) == (1, 1, 1)
    assert has(result, "dangling-dependency", "minor", "component:default/a")
    assert has(result, "orphan", "minor", "component:default/lonely")
    assert has(result, "unowned", "major", "api:default/spec")
    assert lacks(result, "orphan") is False and not any(f["id"].startswith("orphan@api") or f["id"].startswith("orphan@group") for f in result["findings"])
    assert result["status"] == "fail"


def test_graph_lifecycle_conflicts():
    result = graph(
        node("prod", ["old", "exp"]), node("old", lifecycle="deprecated"), node("exp", lifecycle="experimental"),
        node("beta", ["old", "exp"], lifecycle="experimental"), node("older", ["old"], lifecycle="deprecated"), node("blank", ["old"], lifecycle=""),
    )
    assert has(result, "deprecated-dependency", "major", "component:default/prod")
    assert has(result, "deprecated-dependency", "minor", "component:default/beta")
    assert has(result, "deprecated-dependency", "minor", "component:default/blank")
    assert has(result, "experimental-dependency", "major", "component:default/prod")
    assert not any(f["id"].endswith("component:default/older") for f in result["findings"])
    assert not any(f["id"].startswith("experimental-dependency") and f["id"].endswith("beta") for f in result["findings"])


def test_graph_most_depended_on_is_top_five_by_transitive_dependents():
    entries = [node(f"leaf{i}", ["core"]) for i in range(3)] + [node("core", ["base"]), node("base")] + [node(f"x{i}", [f"x{i + 1}"]) for i in range(6)] + [node("x6")]
    result = graph(*entries)
    got = result["most_depended_on"]
    assert len(got) == 5
    assert (got[0]["ref"], got[0]["transitive_dependents"]) == ("component:default/x6", 6)
    assert [r["transitive_dependents"] for r in got] == sorted((r["transitive_dependents"] for r in got), reverse=True)
    assert next(r for r in got if r["ref"] == "component:default/base") == {"ref": "component:default/base", "direct_dependents": 1, "transitive_dependents": 4}


def test_graph_invalid_input():
    invalid("plat_catalog_graph", {"entries": [node("bad ref!")]}, "entries")
    invalid("plat_catalog_graph", {"entries": [node("a"), node("component:default/A")]}, "entries")
    invalid("plat_catalog_graph", {"entries": [node("a", ["no good"])]}, "entries")


# --- plat_ephemeral_env_policy --------------------------------------------------------------------------------

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def env(name="pr-1", age_h=12, ttl=48, hourly=0.5, idle_h=1, owner="group:default/web", **extra):
    out = {"name": name, "created_at": iso(NOW - timedelta(hours=age_h)), "ttl_hours": ttl, "hourly_cost_usd": hourly}
    if idle_h is not None:
        out["last_active_at"] = iso(NOW - timedelta(hours=idle_h))
    if owner:
        out["owner"] = owner
    return {**out, **extra}


def envs(*items, max_ttl=72, idle_after=24, max_cost=100, **extra):
    return ok("plat_ephemeral_env_policy", {"checked_at": iso(NOW), "max_ttl_hours": max_ttl, "idle_after_hours": idle_after, "max_cost_usd": max_cost, "environments": list(items), **extra})


def test_env_policy_healthy_environment_exact_output():
    assert envs(env()) == {
        "state": "SUCCESS", "environment_count": 1, "expired_count": 0, "idle_count": 0, "over_budget_count": 0, "total_accrued_cost_usd": 6.0,
        "total_projected_cost_usd": 24.0, "reclaim": [], "finding_count": 0, "status": "pass", "findings": [],
    }


def test_env_policy_expiry_boundary_is_inclusive():
    assert envs(env(age_h=47.99, idle_h=1))["expired_count"] == 0
    at = envs(env(age_h=48))
    assert at["expired_count"] == 1 and at["reclaim"] == ["pr-1"] and has(at, "expired", "major", "pr-1")
    assert envs(env(age_h=49))["expired_count"] == 1


def test_env_policy_missing_ttl_is_a_blocker_and_policy_maximum_applies():
    result = envs(env(ttl=0, age_h=71, idle_h=1))
    assert has(result, "ttl-missing", "blocker", "pr-1") and result["expired_count"] == 0
    assert result["total_projected_cost_usd"] == 36.0  # 72 h x 0.5
    assert envs(env(ttl=0, age_h=72))["expired_count"] == 1


def test_env_policy_ttl_above_maximum_is_capped_at_the_maximum():
    result = envs(env(ttl=200, age_h=72, idle_h=1))
    assert has(result, "ttl-above-policy", "major", "pr-1") and result["expired_count"] == 1
    assert result["total_projected_cost_usd"] == 36.0
    assert lacks(envs(env(ttl=72, age_h=10)), "ttl-above-policy")


def test_env_policy_idle_boundary_is_inclusive_and_defaults_to_creation_time():
    assert envs(env(age_h=40, idle_h=23.99))["idle_count"] == 0
    at = envs(env(age_h=40, idle_h=24))
    assert (at["idle_count"], at["reclaim"]) == (1, ["pr-1"]) and has(at, "idle", "minor", "pr-1")
    never_active = envs(env(age_h=30, idle_h=None))
    assert never_active["idle_count"] == 1
    assert envs(env(age_h=23, idle_h=None))["idle_count"] == 0
    assert envs(env(age_h=30, idle_h=None, last_active_at=""))["idle_count"] == 1


def test_env_policy_cost_budget_rules():
    equal = envs(env(age_h=10, ttl=20, hourly=5), max_cost=100)  # accrued 50, projected 100
    assert equal["over_budget_count"] == 0 and equal["findings"] == []
    projected = envs(env(age_h=10, ttl=21, hourly=5), max_cost=100)  # projected 105 > 100
    assert has(projected, "projected-cost-over-budget", "minor", "pr-1") and projected["over_budget_count"] == 0
    accrued = envs(env(age_h=21, ttl=48, hourly=5, idle_h=1), max_cost=100)  # accrued 105 > 100
    assert has(accrued, "cost-over-budget", "major", "pr-1") and lacks(accrued, "projected-cost-over-budget") and accrued["over_budget_count"] == 1
    assert accrued["total_accrued_cost_usd"] == 105.0


def test_env_policy_total_budget_is_strictly_exceeded():
    items = [env("a", age_h=10, hourly=1), env("b", age_h=10, hourly=2)]  # accrued 10 + 20
    assert lacks(envs(*items, total_budget_usd=30), "total-budget-exceeded")
    over = envs(*items, total_budget_usd=29.99)
    assert has(over, "total-budget-exceeded", "major") and over["findings"][0]["id"] == "total-budget-exceeded"


def test_env_policy_owner_missing_and_reclaim_is_sorted_union():
    result = envs(env("zeta", age_h=50, owner=None), env("alpha", age_h=40, idle_h=30), env("mid"))
    assert result["reclaim"] == ["alpha", "zeta"] and has(result, "owner-missing", "minor", "zeta")
    assert result["environment_count"] == 3 and (result["expired_count"], result["idle_count"]) == (1, 1)


def test_env_policy_invalid_input():
    base = {"checked_at": iso(NOW), "max_ttl_hours": 72, "idle_after_hours": 24, "max_cost_usd": 100}
    invalid("plat_ephemeral_env_policy", {**base, "environments": [env(age_h=-1)]}, "environments")
    invalid("plat_ephemeral_env_policy", {**base, "environments": [env(), env()]}, "environments")
    invalid("plat_ephemeral_env_policy", {**base, "environments": [env(name="  ")]}, "environments")
    invalid("plat_ephemeral_env_policy", {**base, "environments": [env(last_active_at=iso(NOW + timedelta(hours=1)))]}, "environments")
    invalid("plat_ephemeral_env_policy", {**base, "environments": [env(last_active_at=iso(NOW - timedelta(hours=13)))]}, "environments")
    invalid("plat_ephemeral_env_policy", {**base, "checked_at": "2026-10-20T12:00:00", "environments": [env()]}, "checked_at")
    invalid("plat_ephemeral_env_policy", {**base, "environments": [env(created_at="yesterday")]}, "environments")


# --- plat_quota_fit -------------------------------------------------------------------------------------------


def grant(team, resource="cpu", quota=10, used=5):
    return {"team": team, "resource": resource, "quota": quota, "used": used}


def quota(capacity, allocations, **extra):
    return ok("plat_quota_fit", {"capacity": capacity, "allocations": allocations, **extra})


def test_quota_fit_exact_output_with_reserve():
    result = quota([{"resource": "cpu", "total": 100}], [grant("payments", quota=40, used=30), grant("search", quota=50, used=20)], reserve_pct=10)
    assert result == {
        "state": "SUCCESS",
        "resources": [{"resource": "cpu", "capacity": 100.0, "allocatable": 90.0, "allocated": 90.0, "used": 50.0, "allocation_ratio": 1.0, "usage_ratio": 0.5, "fits": True, "shortfall": 0.0}],
        "oversubscribed_count": 0, "over_quota_count": 0, "finding_count": 0, "status": "pass", "findings": [],
    }


def test_quota_fit_boundary_is_inclusive_and_shortfall_is_reported():
    over = quota([{"resource": "cpu", "total": 100}], [grant("a", quota=45), grant("b", quota=46)], reserve_pct=10)
    row = over["resources"][0]
    assert (row["fits"], row["shortfall"], row["allocation_ratio"]) == (False, 1.0, 1.0111)
    assert has(over, "oversubscribed", "major", "cpu") and over["oversubscribed_count"] == 1 and over["status"] == "fail"


def test_quota_fit_overcommit_scales_allocatable():
    result = quota([{"resource": "cpu", "total": 100}], [grant("a", quota=100), grant("b", quota=80)], reserve_pct=10, overcommit_ratio=2)
    assert result["resources"][0]["allocatable"] == 180.0 and result["resources"][0]["fits"] is True
    assert quota([{"resource": "cpu", "total": 100}], [grant("a", quota=181)], reserve_pct=10, overcommit_ratio=2)["oversubscribed_count"] == 1


def test_quota_fit_multiple_resources_are_independent_and_sorted():
    result = quota([{"resource": "memory", "total": 64}, {"resource": "cpu", "total": 10}],
                   [grant("a", "cpu", 11, 0), grant("a", "memory", 10, 0)])
    assert [(r["resource"], r["fits"]) for r in result["resources"]] == [("cpu", False), ("memory", True)]


@pytest.mark.parametrize("used, rule, severity", [(9, "quota-hot", "minor"), (8.9, None, None), (10, "quota-hot", "minor"), (10.01, "over-quota", "major"),
                                                    (2, None, None), (1.99, "quota-idle", "info"), (0, "quota-idle", "info")])
def test_quota_fit_hot_idle_and_over_quota_boundaries(used, rule, severity):
    result = quota([{"resource": "cpu", "total": 1000}], [grant("a", quota=10, used=used)])
    assert rules(result) == ([(rule, severity)] if rule else [])


def test_quota_fit_zero_quota_cases():
    assert quota([{"resource": "cpu", "total": 10}], [grant("a", quota=0, used=0)])["findings"] == []
    assert has(quota([{"resource": "cpu", "total": 10}], [grant("a", quota=0, used=1)]), "over-quota", "major", "a/cpu")


def test_quota_fit_idle_summary_names_the_reclaimable_amount():
    result = quota([{"resource": "cpu", "total": 1000}], [grant("a", quota=100, used=10)])
    assert result["findings"][0]["summary"] == "used 0.1 of quota (< 0.2): 90.0 could be reclaimed"


def test_quota_fit_invalid_input():
    invalid("plat_quota_fit", {"capacity": [{"resource": "cpu", "total": 1}], "allocations": [grant("a", "gpu")]}, "allocations")
    invalid("plat_quota_fit", {"capacity": [{"resource": "cpu", "total": 1}], "allocations": [grant("a"), grant("a")]}, "allocations")
    invalid("plat_quota_fit", {"capacity": [{"resource": "cpu", "total": 1}], "allocations": [grant("  ")]}, "allocations")
    invalid("plat_quota_fit", {"capacity": [{"resource": "cpu", "total": 1}, {"resource": "cpu", "total": 2}], "allocations": [grant("a")]}, "capacity")
    invalid("plat_quota_fit", {"capacity": [{"resource": " ", "total": 1}], "allocations": [grant("a")]}, "capacity")
    invalid("plat_quota_fit", {"capacity": [{"resource": "cpu", "total": 1}], "allocations": [grant("a")], "reserve_pct": 100}, "reserve_pct")


# --- plat_golden_path_coverage --------------------------------------------------------------------------------

PATHS = [{"id": "python-service", "latest_version": "2.1.0", "min_supported_version": "1.4.0"}]


def gs(name, template="python-service", version="2.1.0", tier="tier-3"):
    out = {"name": name}
    if tier is not None:
        out["tier"] = tier
    if template is not None:
        out["template"] = template
    if version is not None:
        out["template_version"] = version
    return out


def coverage(services, target=0.5, paths=PATHS):
    return ok("plat_golden_path_coverage", {"target_ratio": target, "golden_paths": paths, "services": services})


def test_coverage_exact_output():
    result = coverage([gs("a"), gs("b", version="1.9.0"), gs("c", version="1.3.9"), gs("d", template=None, version=None), gs("e", template="custom", version="1.0.0")], target=0.6)
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "service_count": 5, "on_path_count": 3, "current_count": 1, "outdated_count": 1, "unsupported_count": 1, "off_path_count": 2,
        "coverage_ratio": 0.6, "supported_ratio": 0.4, "meets_target": True,
        "by_path": [{"id": "python-service", "services": 3, "current": 1, "outdated": 1, "unsupported": 1}],
        "finding_count": 4, "status": "fail",
    }
    assert rules(result) == [("golden-path-unsupported", "major"), ("custom-template", "minor"), ("golden-path-outdated", "minor"), ("no-golden-path", "minor")]


@pytest.mark.parametrize("version, rule", [("2.1.0", None), ("2.1.1", None), ("2.0.9", "golden-path-outdated"), ("1.4.0", "golden-path-outdated"),
                                           ("1.3.99", "golden-path-unsupported"), ("v2.1.0", None), ("10.0.0", None), ("1.10.0", "golden-path-outdated")])
def test_coverage_version_boundaries(version, rule):
    result = coverage([gs("a", version=version)], target=0)
    assert [r for r, _ in rules(result)] == ([rule] if rule else [])


def test_coverage_target_boundary_is_inclusive():
    services = [gs("a"), gs("b", template=None, version=None)]
    assert coverage(services, target=0.5)["meets_target"] is True
    short = coverage(services, target=0.51)
    assert short["meets_target"] is False and has(short, "coverage-below-target", "major")


def test_coverage_off_path_severity_follows_tier():
    result = coverage([gs("c0", template=None, tier="tier-0"), gs("c1", template="x", tier="tier-1"), gs("c2", template=None, tier="tier-2"), gs("c3", template=None, tier=None)], target=0)
    assert has(result, "no-golden-path", "major", "c0") and has(result, "custom-template", "major", "c1")
    assert has(result, "no-golden-path", "minor", "c2") and has(result, "no-golden-path", "minor", "c3")


def test_coverage_unusable_version_is_unsupported_and_major():
    result = coverage([gs("a", version=None), gs("b", version="latest"), gs("c", version="1.2")], target=0)
    assert result["unsupported_count"] == 3 and [r for r, _ in rules(result)] == ["version-unknown"] * 3


def test_coverage_two_paths_by_path_sorted():
    paths = [*PATHS, {"id": "go-service", "latest_version": "1.0.0", "min_supported_version": "1.0.0"}]
    result = coverage([gs("a"), gs("b", template="go-service", version="1.0.0"), gs("c", template="go-service", version="1.0.0")], paths=paths, target=1)
    assert [(p["id"], p["services"], p["current"]) for p in result["by_path"]] == [("go-service", 2, 2), ("python-service", 1, 1)]
    assert (result["coverage_ratio"], result["supported_ratio"], result["status"]) == (1.0, 1.0, "pass")


def test_coverage_invalid_input():
    bad = {"target_ratio": 0.5, "services": [gs("a")]}
    invalid("plat_golden_path_coverage", {**bad, "golden_paths": [{"id": "p", "latest_version": "1.0", "min_supported_version": "1.0.0"}]}, "golden_paths")
    invalid("plat_golden_path_coverage", {**bad, "golden_paths": [{"id": "p", "latest_version": "1.0.0", "min_supported_version": "2.0.0"}]}, "golden_paths")
    invalid("plat_golden_path_coverage", {**bad, "golden_paths": [*PATHS, *PATHS]}, "golden_paths")
    invalid("plat_golden_path_coverage", {"target_ratio": 0.5, "golden_paths": PATHS, "services": [gs("a"), gs("a")]}, "services")
    invalid("plat_golden_path_coverage", {"target_ratio": 0.5, "golden_paths": PATHS, "services": [gs(" ")]}, "services")


# --- plat_api_deprecation -------------------------------------------------------------------------------------

CHECKED = datetime(2026, 10, 20, tzinfo=timezone.utc)


def api(name="catalog", version="v1", stability="ga", state="deprecated", consumers=0, announced=None, sunset=None):
    out = {"name": name, "version": version, "stability": stability, "state": state, "active_consumers": consumers}
    if announced is not None:
        out["deprecated_at"] = iso(announced)
    if sunset is not None:
        out["sunset_at"] = iso(sunset)
    return out


def deprecations(*apis, **extra):
    return ok("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": list(apis), **extra})


def test_deprecation_healthy_api_exact_output():
    announced = CHECKED - timedelta(days=30)
    result = deprecations(api(consumers=3, announced=announced, sunset=announced + timedelta(days=400)))
    assert result["findings"] == [{"id": "consumers-pending-migration@catalog/v1", "severity": "minor", "summary": "3 consumer(s) must migrate within 370.0 days"}]
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "api_count": 1, "deprecated_count": 1, "past_sunset_count": 0, "removable_count": 0, "at_risk_count": 0, "finding_count": 1, "status": "warn",
    }


@pytest.mark.parametrize("stability, days, short", [
    ("ga", 365, False), ("ga", 364, True), ("beta", 274, False), ("beta", 273, True), ("alpha", 1, False), ("alpha", 0.5, False),
])
def test_deprecation_notice_window_by_stability(stability, days, short):
    announced = CHECKED - timedelta(days=10)
    result = deprecations(api(stability=stability, announced=announced, sunset=announced + timedelta(days=days), consumers=0))
    assert has(result, "window-too-short", "major", "catalog/v1") is short


def test_deprecation_window_override_replaces_the_table():
    announced = CHECKED - timedelta(days=10)
    sunset = announced + timedelta(days=100)
    assert has(deprecations(api(announced=announced, sunset=sunset), min_window_days=101), "window-too-short", "major")
    assert lacks(deprecations(api(announced=announced, sunset=sunset), min_window_days=100), "window-too-short")
    assert lacks(deprecations(api(stability="beta", announced=announced, sunset=sunset), min_window_days=0), "window-too-short")


def test_deprecation_sunset_passed_with_consumers_is_a_blocker_and_boundary_is_inclusive():
    announced = CHECKED - timedelta(days=500)
    at = deprecations(api(consumers=2, announced=announced, sunset=CHECKED))
    assert has(at, "sunset-passed-with-consumers", "blocker", "catalog/v1")
    assert (at["past_sunset_count"], at["at_risk_count"], at["status"]) == (1, 1, "fail")
    before = deprecations(api(consumers=2, announced=announced, sunset=CHECKED + timedelta(seconds=1)))
    assert lacks(before, "sunset-passed-with-consumers") and before["past_sunset_count"] == 0


def test_deprecation_removable_when_sunset_passed_without_consumers():
    announced = CHECKED - timedelta(days=500)
    result = deprecations(api(consumers=0, announced=announced, sunset=CHECKED - timedelta(days=1)))
    assert rules(result) == [("removable", "info")] and (result["removable_count"], result["past_sunset_count"], result["status"]) == (1, 1, "pass")


def test_deprecation_imminent_boundary_uses_warn_days():
    announced = CHECKED - timedelta(days=400)
    at = deprecations(api(consumers=1, announced=announced, sunset=CHECKED + timedelta(days=30)))
    assert has(at, "sunset-imminent", "major", "catalog/v1") and at["at_risk_count"] == 1
    assert has(deprecations(api(consumers=1, announced=announced, sunset=CHECKED + timedelta(days=31))), "consumers-pending-migration", "minor")
    assert has(deprecations(api(consumers=1, announced=announced, sunset=CHECKED + timedelta(days=45)), warn_days=45), "sunset-imminent", "major")
    assert rules(deprecations(api(consumers=0, announced=announced, sunset=CHECKED + timedelta(days=5)))) == []


def test_deprecation_missing_dates_are_major():
    announced = CHECKED - timedelta(days=10)
    no_sunset = deprecations(api(announced=announced))
    assert rules(no_sunset) == [("sunset-missing", "major")]
    no_announce = deprecations(api(sunset=CHECKED + timedelta(days=400)))
    assert rules(no_announce) == [("deprecation-date-missing", "major")]
    none = deprecations(api())
    assert rules(none) == [("deprecation-date-missing", "major"), ("sunset-missing", "major")] and none["at_risk_count"] == 1


def test_deprecation_active_api_with_a_sunset_date_is_flagged():
    result = deprecations(api(state="active", sunset=CHECKED + timedelta(days=1)))
    assert rules(result) == [("sunset-on-active-api", "minor")] and result["deprecated_count"] == 0
    assert deprecations(api(state="active"))["findings"] == []


def test_deprecation_counts_across_apis_are_in_name_version_order():
    announced = CHECKED - timedelta(days=500)
    result = deprecations(
        api("zeta", consumers=1, announced=announced, sunset=CHECKED - timedelta(days=1)),
        api("alpha", "v2", state="active"),
        api("alpha", "v1", consumers=0, announced=announced, sunset=CHECKED - timedelta(days=1)),
    )
    assert [f["id"] for f in result["findings"]] == ["sunset-passed-with-consumers@zeta/v1", "removable@alpha/v1"]
    assert (result["api_count"], result["deprecated_count"], result["past_sunset_count"], result["removable_count"], result["at_risk_count"]) == (3, 2, 2, 1, 1)


def test_deprecation_invalid_input():
    announced = CHECKED - timedelta(days=10)
    invalid("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [api(announced=announced, sunset=announced)]}, "apis")
    invalid("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [api(announced=CHECKED + timedelta(days=1))]}, "apis")
    invalid("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [api(), api()]}, "apis")
    invalid("plat_api_deprecation", {"checked_at": "2026-10-20", "apis": [api()]}, "checked_at")
    invalid("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [{**api(), "sunset_at": "soon"}]}, "apis")
    invalid("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [api(name=" ")]}, "apis")


# --- plat_request_guardrails ----------------------------------------------------------------------------------

POLICY = {
    "allowed_regions": ["eu-west-1", "us-east-1"], "allowed_classes": ["standard", "premium"], "size_order": ["small", "medium", "large"],
    "auto_approve_size": "medium", "max_size": "large", "max_cpu_cores": 16, "max_memory_gib": 64, "max_storage_gib": 1000, "min_prod_replicas": 2,
}


def rq(rid="r1", environment="prod", region="eu-west-1", size="medium", service_class="standard", replicas=3, cpu=4, memory=16, storage=100):
    return {"id": rid, "environment": environment, "region": region, "size": size, "service_class": service_class, "replicas": replicas,
            "cpu_cores": cpu, "memory_gib": memory, "storage_gib": storage}


def guard(*requests, **policy):
    return ok("plat_request_guardrails", {**POLICY, **policy, "requests": list(requests)})


def test_guardrails_allowed_request_exact_output():
    assert guard(rq()) == {
        "state": "SUCCESS", "request_count": 1, "allowed_count": 1, "needs_approval_count": 0, "denied_count": 0,
        "decisions": [{"id": "r1", "decision": "allow"}], "finding_count": 0, "status": "pass", "findings": [],
    }


def test_guardrails_size_ladder():
    result = guard(rq("s", size="small"), rq("m", size="medium"), rq("l", size="large"), rq("x", size="huge"))
    assert {d["id"]: d["decision"] for d in result["decisions"]} == {"s": "allow", "m": "allow", "l": "needs_approval", "x": "deny"}
    assert has(result, "size-needs-approval", "minor", "l") and has(result, "size-unknown", "major", "x")
    assert (result["allowed_count"], result["needs_approval_count"], result["denied_count"], result["status"]) == (2, 1, 1, "fail")
    ceiling = guard(rq("l", size="large"), max_size="medium", auto_approve_size="small")
    assert has(ceiling, "size-above-max", "major", "l") and ceiling["decisions"][0]["decision"] == "deny"
    assert guard(rq("l", size="large"), auto_approve_size="large")["decisions"][0]["decision"] == "allow"


def test_guardrails_region_and_class_are_exact_matches():
    result = guard(rq("a", region="ap-south-1"), rq("b", region="EU-WEST-1"), rq("c", service_class="gold"), rq("d", service_class="premium", region="us-east-1"))
    assert {d["id"]: d["decision"] for d in result["decisions"]} == {"a": "deny", "b": "deny", "c": "deny", "d": "allow"}
    assert has(result, "region-not-allowed", "major", "a") and has(result, "region-not-allowed", "major", "b") and has(result, "class-not-allowed", "major", "c")


@pytest.mark.parametrize("key, rule, cap", [("cpu", "cpu-above-cap", 16), ("memory", "memory-above-cap", 64), ("storage", "storage-above-cap", 1000)])
def test_guardrails_caps_are_inclusive(key, rule, cap):
    assert guard(rq(**{key: cap}))["decisions"][0]["decision"] == "allow"
    over = guard(rq(**{key: cap + 0.5}))
    assert over["decisions"][0]["decision"] == "deny" and has(over, rule, "major", "r1")


def test_guardrails_prod_replica_minimum_applies_to_prod_only():
    assert guard(rq(replicas=2))["decisions"][0]["decision"] == "allow"
    low = guard(rq(replicas=1))
    assert low["decisions"][0]["decision"] == "deny" and has(low, "replicas-below-prod-minimum", "major", "r1")
    assert guard(rq(environment="dev", replicas=1), rq("s", environment="staging", replicas=1))["allowed_count"] == 2


def test_guardrails_deny_wins_over_approval_and_every_reason_is_reported():
    result = guard(rq(size="large", region="mars-1", replicas=1))
    assert result["decisions"] == [{"id": "r1", "decision": "deny"}]
    assert sorted(r for r, _ in rules(result)) == ["region-not-allowed", "replicas-below-prod-minimum", "size-needs-approval"]


def test_guardrails_decisions_are_sorted_by_id():
    assert [d["id"] for d in guard(rq("b"), rq("a"), rq("c"))["decisions"]] == ["a", "b", "c"]


def test_guardrails_invalid_input():
    request = [rq()]
    invalid("plat_request_guardrails", {**POLICY, "size_order": ["small", "small", "medium", "large"], "requests": request}, "size_order")
    invalid("plat_request_guardrails", {**POLICY, "size_order": ["small", " ", "medium", "large"], "requests": request}, "size_order")
    invalid("plat_request_guardrails", {**POLICY, "auto_approve_size": "xl", "requests": request}, "auto_approve_size")
    invalid("plat_request_guardrails", {**POLICY, "max_size": "xl", "requests": request}, "max_size")
    invalid("plat_request_guardrails", {**POLICY, "auto_approve_size": "large", "max_size": "small", "requests": request}, "auto_approve_size")
    invalid("plat_request_guardrails", {**POLICY, "requests": [rq(), rq()]}, "requests")
    invalid("plat_request_guardrails", {**POLICY, "requests": [rq(" ")]}, "requests")


# --- independent review: upstream facts, recomputed defects, output bounds and secret-safety ------------------------


def test_lint_depends_on_resolves_relative_references_in_the_namespace_of_the_entity():
    # Backstage descriptor format: spec.dependsOn defaults to kind Component and to the namespace of the entity itself
    assert has(lint(entry(namespace="payments", depends_on=["payments-api"])), "dependson-self", "major")
    assert lacks(lint(entry(namespace="payments", depends_on=["default/payments-api", "component:default/payments-api"])), "dependson-self")
    assert has(lint(entry(namespace="payments", depends_on=["db", "component:payments/db"])), "dependson-duplicate", "minor")
    assert lacks(lint(entry(namespace="payments", depends_on=["db", "component:default/db"])), "dependson-duplicate")


def test_lint_does_not_strip_the_name_so_a_padded_name_is_invalid():
    assert has(lint(entry(name=" payments-api")), "name-invalid", "blocker")
    assert has(lint(entry(name="payments-api\n")), "name-invalid", "blocker")
    assert has(lint(entry(namespace=" ")), "namespace-invalid", "blocker")


def test_graph_relative_dependency_refs_resolve_in_the_namespace_of_the_entity():
    result = graph(node("component:team-a/web", ["api", "default/shared"]), node("component:team-a/api"), node("component:default/shared"), node("component:default/api"))
    assert (result["edge_count"], result["dangling_count"]) == (2, 0)
    assert {r["ref"] for r in result["most_depended_on"]} == {"component:team-a/api", "component:default/shared"}  # default/api is not a dependency
    ghost = graph(node("component:team-a/web", ["api"]), node("component:default/api"))
    assert has(ghost, "dangling-dependency", "minor", "component:team-a/web") and "component:team-a/api" in ghost["findings"][0]["summary"]


def test_graph_cycle_summary_names_a_real_loop_not_the_sorted_members():
    result = graph(node("a", ["c"]), node("c", ["b"]), node("b", ["a"]))
    finding = result["findings"][0]
    assert finding["id"] == "dependency-cycle@component:default/a" and result["cycles"] == [["component:default/a", "component:default/b", "component:default/c"]]
    assert "3 entities" in finding["summary"]
    assert "component:default/a -> component:default/c -> component:default/b -> component:default/a" in finding["summary"]
    assert "a -> component:default/b -> component:default/c" not in finding["summary"]  # the sorted member order is not a dependency path


def test_graph_unowned_applies_only_to_the_kinds_that_have_an_owner():
    kinds = ("group", "user", "location", "template", "component", "resource", "system", "domain", "api")
    result = graph(*[node(f"{kind}:default/x", owner=None) for kind in kinds])
    assert sorted(f["id"] for f in result["findings"] if f["id"].startswith("unowned@")) == [f"unowned@{k}:default/x" for k in sorted(("component", "resource", "system", "domain", "api"))]
    assert result["unowned_count"] == 5


def test_diff_required_child_of_a_new_optional_parent_is_not_breaking():
    new = [fld("name"), fld("backup", "object"), fld("backup.schedule", required=True)]
    result = diff([fld("name")], new)
    assert (result["breaking_count"], result["change_class"], result["verdict"], result["requires_new_version"]) == (0, "additive", "compatible", False)
    assert has(result, "field-added-under-optional-parent", "info", "backup.schedule") and has(result, "field-added", "info", "backup")
    assert validate(new, [cl("name", "string", "x")])["valid"] is True  # the claim validator agrees: a claim that never sets `backup` still validates
    assert diff([fld("name")], new, old_version="v1", new_version="v1")["verdict"] == "compatible"
    # an existing optional parent, an unlisted parent or a required new parent keep the field breaking
    assert has(diff([fld("name"), fld("backup", "object")], new), "field-added-required", "major", "backup.schedule")
    assert has(diff([fld("name")], [fld("name"), fld("backup.schedule", required=True)]), "field-added-required", "major", "backup.schedule")
    required_parent = diff([fld("name")], [fld("name"), fld("backup", "object", True), fld("backup.schedule", required=True)])
    assert has(required_parent, "field-added-required", "major", "backup") and has(required_parent, "field-added-required", "major", "backup.schedule")
    deep = diff([fld("name")], [fld("name"), fld("a", "object"), fld("a.b", "object", True), fld("a.b.c", required=True)])
    assert deep["breaking_count"] == 0 and has(deep, "field-added-under-optional-parent", "info", "a.b") and has(deep, "field-added-under-optional-parent", "info", "a.b.c")


def test_rounding_is_half_up_on_the_exact_value_not_to_even():
    # 1 failure in 32 deployments is exactly 0.03125: half-up at 4 places is 0.0313 (float round() would give 0.0312)
    failing = {"started_at": iso(T0 + timedelta(hours=2)), "deployment_id": "d0"}
    result = dora([dep(i, at=T0 + timedelta(hours=1 + i)) for i in range(32)], [failing], days=32)
    assert result["change_failure_rate"] == 0.0313 and result["change_failure_band"] == "elite"
    # eight services at levels 3,3,3,0,0,0,0,0: the average 9/8 = 1.125 rounds to 1.13
    services = [svc(f"g{i}") for i in range(3)] + [svc(f"n{i}", has_owner=False) for i in range(5)]
    assert score(*services)["average_level"] == 1.13


def _wide_enum(i: int, j: int) -> str:
    return f"v{i:03d}-{j:03d}-" + "x" * 100


def test_outputs_stay_far_below_256_kib_on_worst_case_inputs():
    limit = 150_000
    old = [{"path": f"p{i:03d}" + "q" * 190, "type": "string", "required": False, "enum_values": [_wide_enum(i, j) for j in range(100)]} for i in range(300)]
    new = [{**o, "enum_values": [_wide_enum(i, j) + "z" for j in range(100)]} for i, o in enumerate(old)]
    result = diff(old, new)
    assert result["finding_count"] == 600 and len(json.dumps(result)) < limit
    result = validate(old, [{"path": o["path"], "type": "string", "value": "nope"} for o in old])
    assert result["finding_count"] == 300 and len(json.dumps(result)) < limit
    template = [{"name": f"n{i:03d}" + "a" * 100, "type": "string", "required": True, "enum_values": [_wide_enum(i, j) + "q" for j in range(100)]} for i in range(200)]
    golden = [{"name": t["name"], "type": "string", "required": True, "allowed_values": [_wide_enum(i, j) for j in range(100)]} for i, t in enumerate(template)]
    result = check(template, golden)
    assert result["finding_count"] == 200 and len(json.dumps(result)) < limit
    entries = [{"kind": "Component", "name": "n" * 110 + f"{i:03d}", "namespace": "N" * 100, "owner": "o" * 120, "system": "s" * 120, "depends_on": ["d " * 50] * 50, "lifecycle": "x" * 60, "tier": "t" * 60} for i in range(200)]
    result = lint(*entries)
    assert result["finding_count"] > 300 and len(json.dumps(result)) < limit
    ring = [node(f"component:default/{'n' * 40}{i:03d}", [f"component:default/{'n' * 40}{(i + 1) % 300:03d}"] + [f"component:default/ghost-{'g' * 30}-{k}-{i}" for k in range(29)]) for i in range(300)]
    result = graph(*ring)
    assert result["cycle_count"] == 1 and result["dangling_count"] == 300 * 29 and len(json.dumps(result)) < limit


SECRETS = {
    "aws-key-id": ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSF"),
    "github-token": ("ghp_" + "a1B2c3D4" * 4 + "e5F6", "ghp_a1B2"),
    "private-key-hex": ("0123456789abcdef" * 4, "01234567"),
    "mnemonic": ("act add age air all arm art ask aim ago bar bed", "act add age air"),
    "url-credentials": ("https://deploy:hunter2-s3cret@db.internal.example/prod", "hunter2"),
}
# values that fail the identifier grammar (spaces, `:` `@` `/` runs): never echoed even where an identifier is
UNSAFE_IDENTIFIERS = ["mnemonic", "url-credentials"]


def assert_secret_free(result, label: str) -> None:
    text = json.dumps(result, ensure_ascii=False)
    full, needle = SECRETS[label]
    assert full not in text and needle not in text, f"{label} leaked: {text[:400]}"


@pytest.mark.parametrize("label", list(SECRETS))
def test_secret_safety_value_fields_are_never_echoed(label):
    s = SECRETS[label][0]
    short = s[:30]  # template_version allows 30 chars, lifecycle and tier 60
    assert_secret_free(lint(entry(type=s, lifecycle=s[:60], tier=s[:60], description=s, system=s, owner=s, depends_on=[s, f"component:{s}"[:120]], annotations=["backstage.io/techdocs-ref", s, f"{s}/runbook"], links=[s])), label)
    assert_secret_free(lint(entry(owner=f"{s}:default/x", system=f"{s}:default/x")), label)
    assert_secret_free(check([tparam("pw", default=s, enum_values=[s, "ok"]), tparam("n", enum_values=["a"])],
                             [tparam("pw", allowed_values=["ok"], default="other"), tparam("n", allowed_values=[s], default=s)]), label)
    assert_secret_free(check([tparam("pw", default="x")], [tparam("pw", default=s)]), label)
    assert_secret_free(diff([fld("a", enum_values=[s, "k"])], [fld("a", enum_values=["k", s + "x"])]), label)
    schema = [fld("e", enum_values=["x"]), fld("i", "integer", minimum=1), fld("s", max_length=5), fld("n", "number", maximum=1), fld("b", "boolean")]
    assert_secret_free(validate(schema, [cl("e", "string", s), cl("s", "string", s)]), label)
    for bad in (cl("i", "integer", s), cl("n", "number", s), cl("b", "boolean", s)):
        assert_secret_free(call("plat_claim_validate", {"schema": schema, "claim": [bad]}), label)
    assert_secret_free(call("plat_dora_bands", {"window_start": s, "window_end": "2026-09-02T00:00:00Z", "deployments": [], "incidents": []}), label)
    assert_secret_free(call("plat_dora_bands", {"window_start": iso(T0), "window_end": iso(T0 + timedelta(days=1)), "deployments": [{"id": "d", "committed_at": s, "deployed_at": iso(T0)}], "incidents": []}), label)
    assert_secret_free(graph(node("a", ["b"], lifecycle=s[:60], owner=s), node("b", lifecycle=s[:60])), label)
    assert_secret_free(call("plat_catalog_graph", {"entries": [node("a", [s])]}), label)
    assert_secret_free(call("plat_ephemeral_env_policy", {"checked_at": iso(NOW), "max_ttl_hours": 72, "idle_after_hours": 24, "max_cost_usd": 100, "environments": [env(owner=s, last_active_at=s)]}), label)
    assert_secret_free(envs(env(owner=s)), label)
    assert_secret_free(coverage([gs("a", template=s, version=short, tier=s[:60])], target=0), label)
    assert_secret_free(coverage([gs("a", version=short, tier=s[:60])], target=0), label)
    assert_secret_free(call("plat_api_deprecation", {"checked_at": s, "apis": [api()]}), label)
    assert_secret_free(call("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [{**api(), "deprecated_at": s}]}), label)
    assert_secret_free(guard(rq(region=s[:60], service_class=s[:60], size=s[:30])), label)


@pytest.mark.parametrize("label", UNSAFE_IDENTIFIERS)
def test_secret_safety_identifier_fields_that_fail_the_grammar_are_never_echoed(label):
    s = SECRETS[label][0]
    for field in ("kind", "name", "namespace"):
        assert_secret_free(lint(entry(**{field: s}), entry(**{**{"name": "other"}, field: s})), label)
    assert_secret_free(check([tparam(s), tparam(s + "x")], [tparam(s, "integer"), tparam("b")]), label)
    assert_secret_free(diff([fld(s, enum_values=["a"]), fld("r" + s)], [fld(s, enum_values=["b"]), fld("n" + s, required=True)]), label)
    assert_secret_free(diff([fld(s)], [], old_version=s[:63], new_version=s[:63]), label)
    assert_secret_free(validate([fld(s, required=True), fld("m")], [cl("m", "string", "x"), cl("u" + s, "string", "y")]), label)
    assert_secret_free(score(svc(s, tier="tier-0", has_owner=False)), label)
    assert_secret_free(call("plat_catalog_graph", {"entries": [node(s)]}), label)
    assert_secret_free(call("plat_dora_bands", {"window_start": iso(T0), "window_end": iso(T0 + timedelta(days=1)), "deployments": [dep(1)], "incidents": [{"started_at": iso(T0 + timedelta(days=1)), "deployment_id": s}]}), label)
    assert_secret_free(call("plat_dora_bands", {"window_start": iso(T0), "window_end": iso(T0 + timedelta(days=1)), "deployments": [{**dep(1), "id": s}, {**dep(2), "id": s}], "incidents": []}), label)
    assert_secret_free(envs(env(name=s, age_h=60, ttl=100)), label)
    assert_secret_free(call("plat_ephemeral_env_policy", {"checked_at": iso(NOW), "max_ttl_hours": 72, "idle_after_hours": 24, "max_cost_usd": 100, "environments": [env(name=s), env(name=s)]}), label)
    assert_secret_free(quota([{"resource": s, "total": 10}], [grant(s, s, 20, 30)]), label)
    assert_secret_free(call("plat_quota_fit", {"capacity": [{"resource": "cpu", "total": 10}], "allocations": [grant(s, "cpu", 20, 30), grant(s, "gpu", 1, 1)]}), label)
    assert_secret_free(coverage([gs(s, template=None, version=None)], target=1), label)
    assert_secret_free(call("plat_golden_path_coverage", {"target_ratio": 0.5, "golden_paths": PATHS, "services": [gs(s), gs(s)]}), label)
    assert_secret_free(deprecations(api(name=s, state="active", sunset=CHECKED + timedelta(days=1))), label)
    assert_secret_free(call("plat_api_deprecation", {"checked_at": iso(CHECKED), "apis": [api(name=s), api(name=s)]}), label)
    assert_secret_free(guard(rq(s, size="huge", region="mars")), label)
    assert_secret_free(call("plat_request_guardrails", {**POLICY, "requests": [rq(s), rq(s)]}), label)


def test_quota_quantities_round_half_up_at_two_places():
    row = quota([{"resource": "cpu", "total": 1}], [grant("a", quota=0.125, used=0.0625)], overcommit_ratio=0.125)["resources"][0]
    assert (row["allocatable"], row["allocated"], row["used"], row["allocation_ratio"]) == (0.13, 0.13, 0.06, 1.0)  # 0.125 is a tie: half-up, not to even


def test_lint_findings_name_only_grammar_valid_entities_and_never_the_raw_text():
    result = lint({"kind": "Component", "name": "ok-name"}, {"kind": "bad kind", "name": "x"}, {"kind": "Component", "name": "bad name!"})
    subjects = {f["id"].split("@", 1)[1] for f in result["findings"]}
    assert subjects == {"component:default/ok-name", "entry#1", "entry#2"}
    assert all("bad kind" not in f["summary"] and "bad name" not in f["summary"] for f in result["findings"])


def test_severity_decisions_ignore_the_letter_case_of_lifecycle_and_tier():
    # `Production` / `Tier-1` are flagged as nonstandard but must not downgrade the production and critical-tier severities
    base = ["backstage.io/techdocs-ref", "backstage.io/source-location"]
    result = lint(entry(annotations=base, lifecycle="Production", tier="Tier-1"))
    assert has(result, "runbook-missing", "major") and has(result, "lifecycle-nonstandard", "minor") and has(result, "tier-unknown", "minor")
    assert has(lint(entry(annotations=[], lifecycle="PRODUCTION")), "docs-missing", "major")
    flagged = graph(node("a", ["b"], lifecycle="Production"), node("b", lifecycle="Deprecated"), node("c", ["d"], lifecycle="production"), node("d", lifecycle="EXPERIMENTAL"))
    assert has(flagged, "deprecated-dependency", "major", "component:default/a") and has(flagged, "experimental-dependency", "major", "component:default/c")
    off = coverage([gs("a", template=None, version=None, tier="Tier-0")], target=0)
    assert has(off, "no-golden-path", "major", "a")


def test_dora_recovery_runs_from_the_deployment_to_the_latest_resolution():
    # contract basis: deployed_at -> latest resolved_at. d0 deploys at T0+12h; the incident resolves 8 days + 30 min later, so 192.5 h (low)
    late = {"started_at": iso(T0 + timedelta(days=8, hours=12)), "resolved_at": iso(T0 + timedelta(days=8, hours=12, minutes=30)), "deployment_id": "d0"}
    result = dora([dep(0)], [late], days=20)
    assert (result["failed_deployment_count"], result["median_recovery_hours"], result["recovery_band"]) == (1, 192.5, "low")
