"""Behaviour of the v2.2 A05 tools: be_perf_regression, be_dependency_class, be_route_auth_coverage, be_patch_shape_check."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tool_registry import call_tool, validate_registry

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a05-backend.tools.json"
NEW_TOOLS = ("be_perf_regression", "be_dependency_class", "be_route_auth_coverage", "be_patch_shape_check")
DIGEST = "sha256:" + "ab" * 32


def _fragment() -> list[dict]:
    return json.loads(FRAGMENT.read_text(encoding="utf-8")) if FRAGMENT.exists() else []


def _merged_document() -> dict:
    """The live registry with this fragment's entries added when the merge has not happened yet."""
    document = copy.deepcopy(tool_registry.load_document())
    agent = next(a for a in document["agents"] if a["id"] == "A05")
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(t) for t in _fragment() if t["name"] not in present)
    return document


@pytest.fixture(scope="module")
def registry() -> dict:
    return _merged_document()


def run(registry: dict, name: str, payload: dict, caller: str = "A05") -> dict:
    return call_tool(name, caller, payload, data=registry)


# --- contract ---------------------------------------------------------------


def test_fragment_declares_exactly_the_four_new_tools():
    entries = _fragment()
    if not entries:  # applied by the registry merge, which deletes the fragment
        document = tool_registry.load_document()
        names = {t["name"] for a in document["agents"] if a["id"] == "A05" for t in a["registered_tools"]}
        assert set(NEW_TOOLS) <= names
        return
    assert [t["name"] for t in entries] == list(NEW_TOOLS)
    for entry in entries:
        assert entry["owner"] == "A05"
        assert entry["permitted_callers"] == ["A05"]
        assert entry["side_effect_tier"] == "READ"
        assert "|" not in entry["purpose"]


def test_merged_registry_validates(tmp_path):
    document = _merged_document()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert validate_registry(path, root=ROOT) == []


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_examples_reach_their_declared_states(registry, name):
    tool = tool_registry.find_tool(registry, name)
    examples = tool["examples"]
    assert run(registry, name, examples["success"])["state"] == "SUCCESS"
    invalid = run(registry, name, examples["invalid_input"])
    assert invalid["state"] == "INVALID_INPUT" and invalid["field"]
    assert run(registry, name, examples["success"], caller=examples["unauthorized_caller"])["state"] == "UNAUTHORIZED"
    assert run(registry, name, examples["dependency"])["state"] == "DEPENDENCY_UNAVAILABLE"
    assert run(registry, name, examples["timeout"])["state"] == "TIMEOUT"
    assert run(registry, name, examples["partial"])["state"] == "PARTIAL_SUCCESS"


# --- be_perf_regression -----------------------------------------------------


def _bench(before: float, after: float, name: str = "b") -> dict:
    return {"benchmarks": [{"name": name, "before_ms": before, "after_ms": after}]}


def test_perf_exactly_at_budget_is_not_a_regression(registry):
    result = run(registry, "be_perf_regression", _bench(100, 110))
    assert result["regressed"] is False and result["worst_pct"] == 10.0 and result["regressions"] == []


def test_perf_just_over_budget_is_a_regression(registry):
    result = run(registry, "be_perf_regression", _bench(100, 110.01))
    assert result["regressed"] is True
    assert result["regressions"] == [{"name": "b", "pct": 10.01}]


def test_perf_custom_budget_moves_the_boundary(registry):
    payload = {**_bench(100, 110.01), "max_regression_pct": 10.01}
    assert run(registry, "be_perf_regression", payload)["regressed"] is False
    payload["max_regression_pct"] = 5
    assert run(registry, "be_perf_regression", payload)["regressed"] is True


def test_perf_improvement_gives_negative_worst_and_no_regression(registry):
    result = run(registry, "be_perf_regression", _bench(200, 150))
    assert result["worst_pct"] == -25.0 and result["regressed"] is False


def test_perf_reports_only_the_slow_benchmarks_and_the_worst(registry):
    payload = {"benchmarks": [
        {"name": "fast", "before_ms": 50, "after_ms": 40},
        {"name": "slow", "before_ms": 10, "after_ms": 15},
        {"name": "edge", "before_ms": 10, "after_ms": 11.5},
    ]}
    result = run(registry, "be_perf_regression", payload)
    assert result["checked"] == 3
    assert result["regressions"] == [{"name": "slow", "pct": 50.0}, {"name": "edge", "pct": 15.0}]
    assert result["worst_pct"] == 50.0


@pytest.mark.parametrize("before", [0, -1])
def test_perf_baseline_not_above_zero_is_invalid(registry, before):
    result = run(registry, "be_perf_regression", _bench(before, 5))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "benchmarks"


def test_perf_empty_list_and_oversized_list_are_invalid(registry):
    assert run(registry, "be_perf_regression", {"benchmarks": []})["state"] == "INVALID_INPUT"
    many = {"benchmarks": [{"name": f"b{i}", "before_ms": 1, "after_ms": 1} for i in range(201)]}
    assert run(registry, "be_perf_regression", many)["state"] == "INVALID_INPUT"


def test_perf_duplicate_name_and_negative_after_are_invalid_at_handler_level():
    from swarm.tools.a05_tools import perf_regression

    twice = {"benchmarks": [{"name": "x", "before_ms": 1, "after_ms": 1}] * 2}
    assert perf_regression(twice)["state"] == "INVALID_INPUT"
    assert perf_regression(_bench(1, -1))["state"] == "INVALID_INPUT"
    assert perf_regression({**_bench(1, 1), "max_regression_pct": -1})["field"] == "max_regression_pct"


# --- be_dependency_class ----------------------------------------------------


def _dep(manifest: dict, package: str, version: str, allowlist: list[str] | None = None) -> dict:
    return {
        "manifest": manifest,
        "proposed": [{"package": package, "version": version}],
        "allowlist": ["flat-cache"] if allowlist is None else allowlist,
    }


def _only(result: dict) -> dict:
    assert result["state"] == "SUCCESS"
    return result["items"][0]


def test_dependency_absent_from_manifest_is_new_and_l3(registry):
    result = run(registry, "be_dependency_class", _dep({}, "flat-cache", "7.0.1"))
    assert _only(result)["change"] == "new" and _only(result)["level"] == "L3" and result["l3_count"] == 1


def test_dependency_allowlisted_same_major_bump_is_l2(registry):
    for version, change in (("6.1.1", "patch"), ("6.2.0", "minor")):
        result = run(registry, "be_dependency_class", _dep({"flat-cache": "6.1.0"}, "flat-cache", version))
        assert (_only(result)["change"], _only(result)["level"], result["l3_count"]) == (change, "L2", 0)


def test_dependency_major_bump_is_l3(registry):
    item = _only(run(registry, "be_dependency_class", _dep({"flat-cache": "^6.1.0"}, "flat-cache", "7.0.0")))
    assert item["change"] == "major" and item["level"] == "L3"


def test_dependency_downgrade_is_l3_even_within_the_major(registry):
    item = _only(run(registry, "be_dependency_class", _dep({"flat-cache": "6.1.0"}, "flat-cache", "6.0.9")))
    assert item["change"] == "downgrade" and item["level"] == "L3"


def test_dependency_off_allowlist_update_is_l3_and_names_the_allowlist(registry):
    item = _only(run(registry, "be_dependency_class", _dep({"lodash": "4.17.20"}, "lodash", "4.17.21")))
    assert item["change"] == "patch" and item["level"] == "L3" and "allowlist" in item["reason"]


def test_dependency_unchanged_version_is_l2(registry):
    item = _only(run(registry, "be_dependency_class", _dep({"flat-cache": "v6.1.0"}, "flat-cache", "6.1.0")))
    assert item["change"] == "unchanged" and item["level"] == "L2"


def test_dependency_names_compare_case_insensitively(registry):
    item = _only(run(registry, "be_dependency_class", _dep({"Flat-Cache": "6.1.0"}, "FLAT-CACHE", "6.1.1")))
    assert item["package"] == "flat-cache" and item["level"] == "L2"


@pytest.mark.parametrize("version", ["6", "6.1", "latest", "6.1.0-rc1", ">=6.1.0"])
def test_dependency_malformed_proposed_version_is_invalid(registry, version):
    result = run(registry, "be_dependency_class", _dep({}, "flat-cache", version))
    assert result["state"] == "INVALID_INPUT"


def test_dependency_malformed_manifest_and_duplicates_are_invalid(registry):
    bad_manifest = run(registry, "be_dependency_class", _dep({"flat-cache": "x"}, "flat-cache", "1.0.0"))
    assert bad_manifest["state"] == "INVALID_INPUT" and bad_manifest["field"] == "manifest"
    twice = {"manifest": {}, "allowlist": [], "proposed": [{"package": "a", "version": "1.0.0"}] * 2}
    assert run(registry, "be_dependency_class", twice)["field"] == "proposed"
    assert run(registry, "be_dependency_class", {"manifest": {}, "allowlist": [], "proposed": []})["state"] == "INVALID_INPUT"


def test_dependency_mixed_batch_counts_l3(registry):
    payload = {
        "manifest": {"a": "1.0.0", "b": "1.0.0"},
        "allowlist": ["a", "b"],
        "proposed": [
            {"package": "a", "version": "1.1.0"},
            {"package": "b", "version": "2.0.0"},
            {"package": "c", "version": "1.0.0"},
        ],
    }
    result = run(registry, "be_dependency_class", payload)
    assert [i["level"] for i in result["items"]] == ["L2", "L3", "L3"] and result["l3_count"] == 2


# --- be_route_auth_coverage -------------------------------------------------


def _route(method: str, path: str, auth: bool) -> dict:
    return {"method": method, "path": path, "auth": auth}


def test_auth_all_protected_is_covered(registry):
    payload = {"routes": [_route("GET", "/u/:id", True), _route("POST", "/u", True)], "public": []}
    result = run(registry, "be_route_auth_coverage", payload)
    assert result == {"state": "SUCCESS", "checked": 2, "unprotected": [], "public_used": 0, "covered": True}


def test_auth_param_forms_and_trailing_slash_match_the_public_list(registry):
    payload = {
        "routes": [_route("get", "/u/:id", False), _route("GET", "/health/", False)],
        "public": [{"method": "GET", "path": "/u/{id}"}, {"method": "get", "path": "/health"}],
    }
    result = run(registry, "be_route_auth_coverage", payload)
    assert result["unprotected"] == [] and result["public_used"] == 2 and result["covered"] is True


def test_auth_unprotected_route_is_listed_with_method_and_path(registry):
    payload = {"routes": [_route("delete", "/orders/{id}", False), _route("GET", "/orders", True)], "public": []}
    result = run(registry, "be_route_auth_coverage", payload)
    assert result["unprotected"] == ["DELETE /orders/{id}"] and result["covered"] is False


def test_auth_public_entry_is_method_specific(registry):
    payload = {"routes": [_route("POST", "/login", False)], "public": [{"method": "GET", "path": "/login"}]}
    assert run(registry, "be_route_auth_coverage", payload)["unprotected"] == ["POST /login"]


def test_auth_protected_route_in_public_list_is_not_counted_as_public_use(registry):
    payload = {"routes": [_route("GET", "/ping", True)], "public": [{"method": "GET", "path": "/ping"}]}
    assert run(registry, "be_route_auth_coverage", payload)["public_used"] == 0


def test_auth_malformed_and_empty_input_is_invalid(registry):
    assert run(registry, "be_route_auth_coverage", {"routes": [], "public": []})["state"] == "INVALID_INPUT"
    no_slash = run(registry, "be_route_auth_coverage", {"routes": [_route("GET", "users", True)], "public": []})
    assert no_slash["state"] == "INVALID_INPUT" and no_slash["field"] == "routes"
    bad_public = run(registry, "be_route_auth_coverage", {"routes": [_route("GET", "/a", True)], "public": [{"method": "GET", "path": "a"}]})
    assert bad_public["field"] == "public"
    untyped = run(registry, "be_route_auth_coverage", {"routes": [{"method": "GET", "path": "/a", "auth": "yes"}], "public": []})
    assert untyped["state"] == "INVALID_INPUT"


def test_auth_oversized_route_list_is_invalid(registry):
    routes = [_route("GET", f"/r{i}", True) for i in range(501)]
    assert run(registry, "be_route_auth_coverage", {"routes": routes, "public": []})["state"] == "INVALID_INPUT"


# --- be_patch_shape_check ---------------------------------------------------


def _golden() -> dict:
    """The code.patch of prompts/A05-backend.md output_format, with a real digest."""
    return {
        "task_id": "T-884",
        "pr_url": "https://git/example/pr/512",
        "branch": "swarm/T-884",
        "commits_signed": True,
        "contracts_bound": ["API-Orders@1.3.0", "SC-User@2.0.1"],
        "tests_added": 14,
        "coverage_delta_pct": 3.2,
        "sbom_uri": "oci://sbom/x",
        "digest": DIGEST,
        "checks": {"lint": "pass", "typecheck": "pass", "unit": "pass", "contract_conformance": "pass"},
        "dependency_requests": [],
    }


def _check(registry: dict, patch: dict) -> dict:
    result = run(registry, "be_patch_shape_check", {"patch": patch})
    assert result["state"] == "SUCCESS"
    return result


def test_patch_golden_document_is_valid_and_ready(registry):
    assert _check(registry, _golden()) == {"state": "SUCCESS", "valid": True, "problems": [], "ready_for_review": True}


MUTATIONS = {
    "task_id": lambda p: p.update(task_id="../x"),
    "branch": lambda p: p.update(branch="swarm/T-999"),
    "commits_signed": lambda p: p.update(commits_signed=False),
    "contracts_bound": lambda p: p.update(contracts_bound=[]),
    "tests_added": lambda p: p.update(tests_added=0),
    "digest": lambda p: p.update(digest="sha256:abc"),
    "checks": lambda p: p.update(checks={}),
}


@pytest.mark.parametrize("field", list(MUTATIONS))
def test_patch_one_mutation_flags_exactly_that_field(registry, field):
    patch = _golden()
    MUTATIONS[field](patch)
    result = _check(registry, patch)
    assert result["valid"] is False and result["ready_for_review"] is False
    flagged = {p["field"] for p in result["problems"]}
    assert flagged == {field} or (field == "task_id" and field in flagged)


@pytest.mark.parametrize("field", ["task_id", "branch", "commits_signed", "contracts_bound", "tests_added", "digest", "checks"])
def test_patch_each_missing_field_is_reported(registry, field):
    patch = _golden()
    del patch[field]
    result = _check(registry, patch)
    assert field in {p["field"] for p in result["problems"]} and result["ready_for_review"] is False


def test_patch_contract_pin_needs_name_and_semver(registry):
    for pin in ("API-Orders", "API-Orders@1.3", "@1.3.0", "API Orders@1.3.0"):
        patch = _golden()
        patch["contracts_bound"] = ["API-Orders@1.3.0", pin]
        result = _check(registry, patch)
        assert [p["field"] for p in result["problems"]] == ["contracts_bound"], pin


def test_patch_failed_check_is_valid_shape_but_not_ready(registry):
    patch = _golden()
    patch["checks"]["unit"] = "fail"
    result = _check(registry, patch)
    assert result["valid"] is True and result["problems"] == [] and result["ready_for_review"] is False


def test_patch_skipped_checks_do_not_block_readiness(registry):
    patch = _golden()
    patch["checks"]["typecheck"] = "skipped:tool-missing"
    patch["checks"]["lint"] = "skipped"
    assert _check(registry, patch)["ready_for_review"] is True


def test_patch_unknown_check_value_is_flagged_by_check_name(registry):
    patch = _golden()
    patch["checks"]["unit"] = "ok"
    assert [p["field"] for p in _check(registry, patch)["problems"]] == ["checks.unit"]


def test_patch_wrong_types_do_not_crash(registry):
    patch = {"task_id": 5, "branch": 7, "commits_signed": "yes", "contracts_bound": "API@1.0.0", "tests_added": True, "digest": 1, "checks": []}
    result = _check(registry, patch)
    assert result["valid"] is False
    assert {"task_id", "branch", "commits_signed", "contracts_bound", "tests_added", "digest", "checks"} <= {p["field"] for p in result["problems"]}


def test_patch_must_be_an_object(registry):
    assert run(registry, "be_patch_shape_check", {"patch": []})["state"] == "INVALID_INPUT"
    assert run(registry, "be_patch_shape_check", {})["field"] == "patch"
    from swarm.tools.a05_tools import patch_shape_check

    assert patch_shape_check({"patch": []})["field"] == "patch"


# --- review fix-ups (RV-C) --------------------------------------------------------------------------------


def test_dependency_zero_x_minor_bump_is_l3(registry):
    """RV-C P2: lib 0.1.0 -> 0.2.0 on the allowlist returned minor / L2 'allowlisted and semver-compatible'."""
    item = _only(run(registry, "be_dependency_class", _dep({"lib": "0.1.0"}, "lib", "0.2.0", ["lib"])))
    assert item["change"] == "major" and item["level"] == "L3" and "0.x" in item["reason"]


def test_dependency_zero_x_patch_and_one_x_minor_stay_l2(registry):
    assert _only(run(registry, "be_dependency_class", _dep({"lib": "0.1.0"}, "lib", "0.1.5", ["lib"])))["level"] == "L2"
    one = _only(run(registry, "be_dependency_class", _dep({"lib": "1.1.0"}, "lib", "1.2.0", ["lib"])))
    assert one["change"] == "minor" and one["level"] == "L2"


def test_dependency_manifest_names_that_collide_after_lowercasing_are_invalid(registry):
    """RV-C P3: 'lib' and 'Lib' both became 'lib' and the later one silently won (produced 'downgrade')."""
    out = run(registry, "be_dependency_class", _dep({"lib": "2.0.0", "Lib": "1.0.0"}, "lib", "1.5.0", ["lib"]))
    assert out["state"] == "INVALID_INPUT" and out["field"] == "manifest"


def test_perf_overflowing_percentage_is_invalid_not_infinity(registry):
    """RV-C P3: before_ms=1e-300, after_ms=1e300 produced pct: Infinity."""
    out = run(registry, "be_perf_regression", {"benchmarks": [{"name": "x", "before_ms": 1e-300, "after_ms": 1e300}]})
    assert out["state"] == "INVALID_INPUT" and out["field"] == "benchmarks"


@pytest.mark.parametrize("branch", ["swarm/T-884", "bot-03-desk/T-884", "bot-00-a/T-884", "bot-06-seat-9/T-884"])
def test_patch_branch_accepts_the_generic_and_the_programming_desk_forms(registry, branch):
    """Skill-review finding: Programming Desk prescribes bot-0N-<seat>/<task_id>; only swarm/<task_id> was accepted."""
    patch = _golden()
    patch["branch"] = branch
    assert _check(registry, patch)["ready_for_review"] is True


@pytest.mark.parametrize(
    "branch",
    ["bot-07-desk/T-884", "bot-3-desk/T-884", "bot-03-Desk/T-884", "bot-03-desk/T-999", "bot-03-desk/x/T-884", "bot-03-/T-884", "main", "swarm/T-999"],
)
def test_patch_branch_rejects_other_forms_and_names_both_accepted_ones(registry, branch):
    patch = _golden()
    patch["branch"] = branch
    result = _check(registry, patch)
    problem = next(p for p in result["problems"] if p["field"] == "branch")
    assert "swarm/T-884" in problem["msg"] and "bot-0N-<seat>/T-884" in problem["msg"]
    assert result["ready_for_review"] is False
