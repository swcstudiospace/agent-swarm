"""Behaviour of the v2.1 A05 lane tools: be_route_norm, be_openapi_path_params, be_route_collision, be_method_status_check,
be_semver_range_class, be_license_allowlist_check, be_coverage_delta, be_acceptance_coverage.

The generated contract probes only prove the declared states; these tests assert the OUTPUT VALUES for specific inputs and
one case per branch of each handler (swarm/tools/a05_tools.py, swarm/tools/batch_01_05.py)."""
from __future__ import annotations

import importlib

import pytest

LEGACY_TOOLS = (
    "be_route_norm",
    "be_openapi_path_params",
    "be_route_collision",
    "be_method_status_check",
    "be_semver_range_class",
    "be_license_allowlist_check",
    "be_coverage_delta",
    "be_acceptance_coverage",
)


def _live_registry():
    """The swarm.tool_registry module `call_tool` resolves against right now.

    conftest's swarm_dir fixture purges every swarm* module between tests, so a module-level import can bind a stale instance."""
    return importlib.import_module("swarm.tool_registry")


def run(name: str, payload: dict, caller: str = "A05") -> dict:
    return _live_registry().call_tool(name, caller, payload)


def invalid(result: dict, field: str) -> dict:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    assert result["message"], result
    return result


# --- shared contract: caller gate, unknown fields, probe isolation ---------------------------------------------------

_MINIMAL = {
    "be_route_norm": {"path": "/a"},
    "be_openapi_path_params": {"template": "/a"},
    "be_route_collision": {"routes": [{"method": "GET", "path": "/a"}]},
    "be_method_status_check": {"method": "GET", "status": 200, "allowed_methods": ["GET"], "allowed_statuses": [200]},
    "be_semver_range_class": {"range": "^1.0.0"},
    "be_license_allowlist_check": {"packages": [{"name": "a", "license": "MIT"}], "allowlist": ["MIT"]},
    "be_coverage_delta": {"before": 50, "after": 60},
    "be_acceptance_coverage": {"criteria": ["AC1"], "covered": ["AC1"]},
}


def test_legacy_tools_are_registered_for_a05():
    registry = _live_registry()
    data = registry.load_document()
    for name in LEGACY_TOOLS:
        tool = registry.find_tool(data, name)
        assert tool is not None and tool["owner"] == "A05", name
        assert "A05" in tool["permitted_callers"], name
        assert registry.tool_tier(tool) == "READ", name


@pytest.mark.parametrize("name", LEGACY_TOOLS)
def test_minimal_payload_succeeds_for_a05(name):
    assert run(name, _MINIMAL[name])["state"] == "SUCCESS"


@pytest.mark.parametrize("name", LEGACY_TOOLS)
def test_a01_orchestrator_is_refused_without_reaching_the_handler(name):
    result = run(name, _MINIMAL[name], caller="A01")
    assert result == {"state": "UNAUTHORIZED", "caller": "A01", "target": name, "reason": "caller is not permitted"}


@pytest.mark.parametrize("name", LEGACY_TOOLS)
def test_unexpected_field_is_invalid_input_naming_it(name):
    result = run(name, {**_MINIMAL[name], "surprise": 1})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "surprise"


@pytest.mark.parametrize("name", LEGACY_TOOLS)
def test_probe_value_outside_the_enum_is_invalid_input(name):
    invalid(run(name, {**_MINIMAL[name], "probe": "bogus"}), "probe")


@pytest.mark.parametrize("name", LEGACY_TOOLS)
def test_partial_probe_short_circuits_the_real_computation(name):
    assert run(name, {**_MINIMAL[name], "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}


def test_only_route_norm_is_open_to_a06():
    assert run("be_route_norm", {"path": "/a/"}, caller="A06") == {"state": "SUCCESS", "normalized": "/a"}
    for name in LEGACY_TOOLS[1:]:
        assert run(name, _MINIMAL[name], caller="A06")["state"] == "UNAUTHORIZED", name


# --- be_route_norm ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/users/:id/", "/users/{}"),  # Express param + trailing slash
        ("/users/{id}", "/users/{}"),  # OpenAPI param
        ("/users/<int:id>/posts/", "/users/{}/posts"),  # Flask converter
        ("/users/<id>", "/users/{}"),  # Flask bare
        ("/users/{id:\\d+}/x", "/users/{}/x"),  # chi regex constraint
        ("/users/:id?verbose=1", "/users/{}"),  # query string is dropped
        ("/a/{x}/b/{y}", "/a/{}/b/{}"),  # names are anonymised, count is kept
        ("/a/:id/b/:id2/", "/a/{}/b/{}"),
        ("/static/files", "/static/files"),  # nothing to rewrite
        ("/", "/"),  # root stays root
        ("///", "/"),  # trailing slashes collapse to root
        ("?x=1", "/"),  # empty path before the query becomes root
        ("/a//b/", "/a//b"),  # only the trailing slash is stripped
        ("/a/{x}{y}", "/a/{}{}"),
        ("/a/:id.json", "/a/{}.json"),
    ],
)
def test_route_norm_rewrites_every_supported_param_style(path, expected):
    assert run("be_route_norm", {"path": path}) == {"state": "SUCCESS", "normalized": expected}


def test_route_norm_styles_that_name_the_same_shape_normalise_equal():
    forms = ["/orders/:id/items/:item", "/orders/{id}/items/{item}", "/orders/<int:id>/items/<item>/"]
    assert {run("be_route_norm", {"path": form})["normalized"] for form in forms} == {"/orders/{}/items/{}"}


def test_route_norm_matches_the_conformance_script_it_wraps():
    from swarm.tools.support import script

    norm = script("be_contract_conformance").norm_path
    for path in ("/a/:id/", "/b/{x}", "/c/<int:n>", "/", "/d?q=1"):
        assert run("be_route_norm", {"path": path})["normalized"] == norm(path)


def test_route_norm_missing_empty_and_non_string_path_name_the_field():
    assert invalid(run("be_route_norm", {}), "path")["message"] == "missing required field path"
    assert invalid(run("be_route_norm", {"path": ""}), "path")["message"] == "shorter than 1"
    assert invalid(run("be_route_norm", {"path": 5}), "path")["message"] == "expected string"
    invalid(run("be_route_norm", {"path": None}), "path")
    invalid(run("be_route_norm", {"path": ["/a"]}), "path")


def test_route_norm_handles_a_long_path_in_linear_time_and_keeps_it():
    long_path = "/" + "a" * 20000
    assert run("be_route_norm", {"path": long_path})["normalized"] == long_path


@pytest.mark.parametrize("path", ["/a/{id:int}/b", "/a/{id:uuid}", "/a/{path:path}"])
def test_route_norm_typed_brace_params_normalise_like_plain_braces(path):
    # the chi `{name:type}` rule must run before the Express `:name` rule, otherwise `{id:int}` becomes `{id{int}}`
    expected = path.split("/{")[0] + "/{}" + ("/b" if path.endswith("/b") else "")
    assert run("be_route_norm", {"path": path})["normalized"] == expected


# --- be_openapi_path_params ------------------------------------------------------------------------------------------


def test_path_params_returns_names_in_template_order_with_count():
    assert run("be_openapi_path_params", {"template": "/users/{id}/posts/{post_id}"}) == {
        "state": "SUCCESS",
        "params": ["id", "post_id"],
        "param_count": 2,
        "duplicates": [],
    }


def test_path_params_static_template_and_root_have_no_params():
    expected = {"state": "SUCCESS", "params": [], "param_count": 0, "duplicates": []}
    assert run("be_openapi_path_params", {"template": "/health"}) == expected
    assert run("be_openapi_path_params", {"template": "/"}) == expected


def test_path_params_duplicates_are_listed_once_sorted_and_all_occurrences_kept():
    result = run("be_openapi_path_params", {"template": "/a/{y}/{x}/{y}/{x}/{y}/{z}"})
    assert result["params"] == ["y", "x", "y", "x", "y", "z"]
    assert result["param_count"] == 6
    assert result["duplicates"] == ["x", "y"]


def test_path_params_adjacent_params_and_suffix_after_param_are_split_correctly():
    assert run("be_openapi_path_params", {"template": "/a/{x}{y}"})["params"] == ["x", "y"]
    assert run("be_openapi_path_params", {"template": "/a/{x}.json"})["params"] == ["x"]


def test_path_params_names_are_case_sensitive_so_id_and_Id_are_not_duplicates():
    result = run("be_openapi_path_params", {"template": "/a/{id}/{Id}"})
    assert result["params"] == ["id", "Id"] and result["duplicates"] == []


def test_path_params_underscore_leading_and_digit_containing_names_are_valid():
    assert run("be_openapi_path_params", {"template": "/a/{_x}/{p2}"})["params"] == ["_x", "p2"]


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("no-slash", "template must start with '/'"),
        ("/{}", "invalid parameter name ''"),
        ("/a/{1x}", "invalid parameter name '1x'"),
        ("/a/{a-b}", "invalid parameter name 'a-b'"),
        ("/a/{ id }", "invalid parameter name ' id '"),
        ("/a/{\u00e9}", "invalid parameter name '\u00e9'"),
        ("/a/{{x}}", "nested '{' in path template"),
        ("/a/x}", "unmatched '}' in path template"),
        ("/a/{x", "unclosed '{' in path template"),
        ("/a/{x}/{", "unclosed '{' in path template"),
    ],
)
def test_path_params_malformed_templates_are_invalid_input_on_the_template_field(template, message):
    assert invalid(run("be_openapi_path_params", {"template": template}), "template")["message"] == message


def test_path_params_schema_bounds_missing_empty_non_string_and_length():
    assert invalid(run("be_openapi_path_params", {}), "template")["message"] == "missing required field template"
    assert invalid(run("be_openapi_path_params", {"template": ""}), "template")["message"] == "shorter than 1"
    invalid(run("be_openapi_path_params", {"template": 3}), "template")
    at_limit = "/" + "a" * 511
    assert run("be_openapi_path_params", {"template": at_limit})["param_count"] == 0
    assert invalid(run("be_openapi_path_params", {"template": at_limit + "a"}), "template")["message"] == "longer than 512"


def test_path_params_handler_called_directly_matches_the_registry_result():
    from swarm.tools import a05_tools

    payload = {"template": "/a/{x}/{x}"}
    assert a05_tools.openapi_path_params(payload) == run("be_openapi_path_params", payload)


# --- be_route_collision ----------------------------------------------------------------------------------------------


def _routes(*pairs: tuple[str, str]) -> dict:
    return {"routes": [{"method": method, "path": path} for method, path in pairs]}


def test_collision_same_method_different_param_styles_collide_with_exact_message():
    result = run("be_route_collision", _routes(("GET", "/users/{id}"), ("GET", "/users/:uid")))
    assert result == {
        "state": "SUCCESS",
        "routes_checked": 2,
        "collision_count": 1,
        "collisions": ["GET /users/{} <- /users/{id}, /users/:uid"],
    }


def test_collision_different_methods_on_the_same_path_do_not_collide():
    result = run("be_route_collision", _routes(("GET", "/users/{id}"), ("POST", "/users/{id}")))
    assert result["collision_count"] == 0 and result["collisions"] == [] and result["routes_checked"] == 2


def test_collision_method_is_trimmed_and_upper_cased_and_paths_lose_a_trailing_slash():
    result = run("be_route_collision", _routes(("get", "/users/{id}"), (" GET ", "/users/{name}/")))
    assert result["collisions"] == ["GET /users/{} <- /users/{id}, /users/{name}/"]


def test_collision_static_segment_does_not_collide_with_a_param_segment():
    result = run("be_route_collision", _routes(("GET", "/a/{x}"), ("GET", "/a/foo")))
    assert result["collision_count"] == 0


def test_collision_three_way_group_is_one_collision_in_input_order():
    result = run("be_route_collision", _routes(("GET", "/a/{x}"), ("GET", "/a/{y}"), ("GET", "/a/:z")))
    assert result["collision_count"] == 1
    assert result["collisions"] == ["GET /a/{} <- /a/{x}, /a/{y}, /a/:z"]


def test_collision_independent_groups_are_reported_separately_in_first_seen_order():
    result = run(
        "be_route_collision",
        _routes(("GET", "/b/{x}"), ("POST", "/a/{x}"), ("GET", "/b/:y"), ("POST", "/a/:y"), ("GET", "/solo")),
    )
    assert result["routes_checked"] == 5 and result["collision_count"] == 2
    assert result["collisions"] == ["GET /b/{} <- /b/{x}, /b/:y", "POST /a/{} <- /a/{x}, /a/:y"]


def test_collision_exact_duplicate_registrations_collide():
    result = run("be_route_collision", _routes(("GET", "/a"), ("GET", "/a"), ("GET", "/a")))
    assert result["collisions"] == ["GET /a <- /a, /a, /a"]


def test_collision_trailing_slash_and_root_variants_collide():
    assert run("be_route_collision", _routes(("GET", "/a/"), ("GET", "/a")))["collisions"] == ["GET /a <- /a/, /a"]
    assert run("be_route_collision", _routes(("GET", "/"), ("GET", "//")))["collisions"] == ["GET / <- /, //"]


def test_collision_multi_param_shapes_compare_positionally_ignoring_names():
    result = run("be_route_collision", _routes(("GET", "/a/{x}/b/{y}"), ("GET", "/a/:p/b/:q")))
    assert result["collisions"] == ["GET /a/{}/b/{} <- /a/{x}/b/{y}, /a/:p/b/:q"]
    assert run("be_route_collision", _routes(("GET", "/a/{x}/b"), ("GET", "/a/{x}/{y}")))["collision_count"] == 0


def test_collision_literal_empty_braces_equal_a_named_param():
    result = run("be_route_collision", _routes(("GET", "/a/{x}"), ("GET", "/a/{}")))
    assert result["collisions"] == ["GET /a/{} <- /a/{x}, /a/{}"]


def test_collision_paths_are_case_sensitive():
    assert run("be_route_collision", _routes(("GET", "/Users"), ("GET", "/users")))["collision_count"] == 0


def test_collision_500_routes_is_the_accepted_maximum_and_501_is_refused():
    ok = run("be_route_collision", {"routes": [{"method": "GET", "path": f"/r{i}"} for i in range(500)]})
    assert ok["routes_checked"] == 500 and ok["collision_count"] == 0
    refused = run("be_route_collision", {"routes": [{"method": "GET", "path": f"/r{i}"} for i in range(501)]})
    assert invalid(refused, "routes")["message"] == "more than 500 items"


@pytest.mark.parametrize(
    ("routes", "message"),
    [
        ([{"method": "GET", "path": "a"}], "route 0 needs a method and a path starting with '/'"),
        ([{"method": "  ", "path": "/a"}], "route 0 needs a method and a path starting with '/'"),
        ([{"method": "GET", "path": "  "}], "route 0 needs a method and a path starting with '/'"),
        ([{"method": "GET", "path": "/ok"}, {"method": "GET", "path": "bad"}], "route 1 needs a method and a path starting with '/'"),
    ],
)
def test_collision_route_without_method_or_rooted_path_names_its_index(routes, message):
    assert invalid(run("be_route_collision", {"routes": routes}), "routes")["message"] == message


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing required field routes"),
        ({"routes": []}, "fewer than 1 items"),
        ({"routes": "x"}, "expected array"),
        ({"routes": [{"method": "GET"}]}, "[0] path: missing required field path"),
        ({"routes": [{"path": "/a"}]}, "[0] method: missing required field method"),
        ({"routes": [{"method": "GET", "path": "/a", "x": 1}]}, "[0] x: unexpected field x"),
        ({"routes": [{"method": "GETGETGETGET", "path": "/a"}]}, "[0] method: longer than 10"),
        ({"routes": [{"method": "GET", "path": "/" + "a" * 512}]}, "[0] path: longer than 512"),
    ],
)
def test_collision_schema_violations_name_routes(payload, message):
    assert invalid(run("be_route_collision", payload), "routes")["message"] == message


def test_collision_does_not_treat_flask_angle_params_as_params():
    # documented forms are '{x}' and ':x'; '<int:id>' is a literal segment here, so it does not collide with '{x}'
    result = run("be_route_collision", _routes(("GET", "/a/{x}"), ("GET", "/a/<int:id>")))
    assert result["collision_count"] == 0


# --- be_method_status_check ------------------------------------------------------------------------------------------


def _ms(method="GET", status=200, methods=("GET",), statuses=(200,)) -> dict:
    return {"method": method, "status": status, "allowed_methods": list(methods), "allowed_statuses": list(statuses)}


def test_method_status_pass_when_both_allowed_normalises_method_case():
    assert run("be_method_status_check", _ms("get", 200, ("GET", "POST"), (200, 201))) == {
        "state": "SUCCESS",
        "method": "GET",
        "status_class": 2,
        "method_allowed": True,
        "status_allowed": True,
        "verdict": "PASS",
    }


def test_method_status_each_failure_axis_is_reported_independently():
    both = run("be_method_status_check", _ms("DELETE", 404, ("GET",), (200,)))
    assert (both["method_allowed"], both["status_allowed"], both["verdict"]) == (False, False, "FAIL")
    status_only = run("be_method_status_check", _ms("GET", 404, ("GET",), (200,)))
    assert (status_only["method_allowed"], status_only["status_allowed"], status_only["verdict"]) == (True, False, "FAIL")
    method_only = run("be_method_status_check", _ms("DELETE", 200, ("GET",), (200,)))
    assert (method_only["method_allowed"], method_only["status_allowed"], method_only["verdict"]) == (False, True, "FAIL")


@pytest.mark.parametrize(("status", "klass"), [(100, 1), (199, 1), (200, 2), (301, 3), (404, 4), (499, 4), (500, 5), (599, 5)])
def test_method_status_class_is_the_hundreds_digit_across_the_range(status, klass):
    result = run("be_method_status_check", _ms("GET", status, ("GET",), (status,)))
    assert result["status_class"] == klass and result["verdict"] == "PASS"


@pytest.mark.parametrize(("status", "message"), [(99, "below minimum 100"), (600, "above maximum 599"), (0, "below minimum 100")])
def test_method_status_out_of_range_status_is_invalid_input(status, message):
    assert invalid(run("be_method_status_check", _ms(status=status)), "status")["message"] == message


@pytest.mark.parametrize("status", [True, 200.0, 200.5, "200", None])
def test_method_status_status_must_be_a_true_integer(status):
    assert invalid(run("be_method_status_check", _ms(status=status)), "status")["message"] == "expected integer"


def test_method_status_method_and_allowlist_entries_are_trimmed_and_case_folded():
    result = run("be_method_status_check", _ms(" patch ", 100, (" Patch",), (100,)))
    assert result["method"] == "PATCH" and result["verdict"] == "PASS"


def test_method_status_empty_allowlists_fail_closed():
    result = run("be_method_status_check", _ms("GET", 200, (), ()))
    assert (result["method_allowed"], result["status_allowed"], result["verdict"]) == (False, False, "FAIL")


def test_method_status_whitespace_only_allowlist_entry_allows_nothing():
    assert run("be_method_status_check", _ms("GET", 404, ("  ",), (404,)))["method_allowed"] is False


@pytest.mark.parametrize("method", ["G3T", "\u00e9", "   ", "GE T", "GET/POST"])
def test_method_status_method_must_be_1_to_10_ascii_letters(method):
    assert invalid(run("be_method_status_check", _ms(method=method)), "method")["message"] == "method must be 1-10 ASCII letters"


def test_method_status_method_longer_than_ten_is_a_schema_error_and_ten_is_accepted():
    assert invalid(run("be_method_status_check", _ms(method="GETGETGETGET")), "method")["message"] == "longer than 10"
    assert run("be_method_status_check", _ms("A" * 10, 200, ("A" * 10,), (200,)))["verdict"] == "PASS"


def test_method_status_list_size_limits_and_item_types():
    assert invalid(run("be_method_status_check", _ms(methods=["GET"] * 21)), "allowed_methods")["message"] == "more than 20 items"
    assert invalid(run("be_method_status_check", _ms(statuses=[200] * 51)), "allowed_statuses")["message"] == "more than 50 items"
    assert run("be_method_status_check", _ms(methods=["GET"] * 20, statuses=[200] * 50))["verdict"] == "PASS"
    assert invalid(run("be_method_status_check", _ms(statuses=[700])), "allowed_statuses")["message"] == "[0] above maximum 599"
    assert invalid(run("be_method_status_check", _ms(statuses=[200, "x"])), "allowed_statuses")["message"] == "[1] expected integer"
    assert invalid(run("be_method_status_check", {**_ms(), "allowed_methods": "GET"}), "allowed_methods")["message"] == "expected array"


def test_method_status_missing_required_fields_each_name_themselves():
    for field in ("method", "status", "allowed_methods", "allowed_statuses"):
        payload = _ms()
        del payload[field]
        assert invalid(run("be_method_status_check", payload), field)["message"] == f"missing required field {field}"


# --- be_semver_range_class -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "bounded"),
    [
        ("*", "wildcard", False),
        ("x", "wildcard", False),
        ("X", "wildcard", False),
        (" * ", "wildcard", False),
        ("^1.2.3", "caret", True),
        ("^1", "caret", True),
        ("^0.0.0", "caret", True),
        ("~1.2", "tilde", True),
        ("~0.0.1", "tilde", True),
        ("1.2.3 - 2.0.0", "hyphen", True),
        ("1 - 2", "hyphen", True),
        (">=1.0.0", "comparator", False),
        (">1.0.0", "comparator", False),
        (">1.0.0 >2.0.0", "comparator", False),
        (">=1.0.0 <2.0.0", "comparator", True),
        (">=1.0.0\t<2.0.0", "comparator", True),
        ("<2", "comparator", True),
        ("<=1.2.3", "comparator", True),
        ("=1.2.3", "comparator", True),
        ("1.2.3", "exact", True),
        ("\n^1.0.0", "caret", True),
    ],
)
def test_semver_classification_and_upper_bound_for_each_branch(text, kind, bounded):
    assert run("be_semver_range_class", {"range": text}) == {"state": "SUCCESS", "kind": kind, "upper_bounded": bounded}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("^", "^ range needs a version"),
        ("~", "~ range needs a version"),
        ("^1.2.3.4", "^ range needs a version"),
        ("^1.x", "^ range needs a version"),
        ("~ 1.0.0", "~ range needs a version"),
        ("^1.0.0 ^2.0.0", "^ range needs a version"),
        ("a - b", "hyphen range needs two versions"),
        ("1.2.3 - x", "hyphen range needs two versions"),
        ("1.0.0 || 2.0.0", "union ranges ('||') are not supported"),
        (">=1.0.0 || <0.5", "union ranges ('||') are not supported"),
        (">= 1.0.0", "invalid comparator token '>='"),
        ("<>1.0.0", "invalid comparator token '<>1.0.0'"),
        (">=1.0.0 junk", "invalid comparator token 'junk'"),
        ("==1.0.0", "invalid comparator token '==1.0.0'"),
        ("latest", "unrecognized semver range 'latest'"),
        ("1.2", "unrecognized semver range '1.2'"),
        ("v1.2.3", "unrecognized semver range 'v1.2.3'"),
        ("1.2.3 -", "unrecognized semver range '1.2.3 -'"),
    ],
)
def test_semver_unsupported_or_malformed_ranges_are_invalid_input_with_the_reason(text, message):
    assert invalid(run("be_semver_range_class", {"range": text}), "range")["message"] == message


def test_semver_union_is_checked_before_the_comparator_branch_so_no_partial_classification_leaks():
    result = run("be_semver_range_class", {"range": "^1.0.0 || ^2.0.0"})
    assert result["state"] == "INVALID_INPUT" and "kind" not in result


def test_semver_schema_bounds_empty_and_overlong_and_non_string():
    assert invalid(run("be_semver_range_class", {"range": ""}), "range")["message"] == "shorter than 1"
    assert invalid(run("be_semver_range_class", {"range": "^" + "1" * 64}), "range")["message"] == "longer than 64"
    assert invalid(run("be_semver_range_class", {}), "range")["message"] == "missing required field range"
    invalid(run("be_semver_range_class", {"range": 1}), "range")
    # 64 characters is accepted by the schema and falls through to the handler's own verdict
    assert invalid(run("be_semver_range_class", {"range": "x" * 64}), "range")["message"].startswith("unrecognized semver range")


def test_semver_whitespace_only_range_is_a_wildcard_after_trimming():
    # the schema only requires one character; the handler trims, and an empty trim is the unbounded wildcard
    assert run("be_semver_range_class", {"range": "   "}) == {"state": "SUCCESS", "kind": "wildcard", "upper_bounded": False}


# --- be_license_allowlist_check --------------------------------------------------------------------------------------


def _lic(packages: list[tuple[str, str]], allowlist: list[str]) -> dict:
    return {"packages": [{"name": n, "license": lic} for n, lic in packages], "allowlist": allowlist}


def test_license_reports_only_violations_with_the_exact_format():
    result = run("be_license_allowlist_check", _lic([("a", "MIT"), ("b", "GPL-3.0")], ["MIT", "Apache-2.0"]))
    assert result == {"state": "SUCCESS", "checked_count": 2, "violation_count": 1, "violations": ["b (GPL-3.0)"]}


def test_license_comparison_is_case_insensitive_and_trims_both_sides():
    assert run("be_license_allowlist_check", _lic([("a", "mit"), ("b", " MIT ")], [" mit "]))["violation_count"] == 0


def test_license_or_expression_passes_when_any_alternative_is_allowed():
    assert run("be_license_allowlist_check", _lic([("a", "MIT OR GPL-3.0")], ["MIT"]))["violation_count"] == 0
    assert run("be_license_allowlist_check", _lic([("a", "GPL-3.0 OR LGPL-3.0")], ["MIT"]))["violations"] == ["a (GPL-3.0 OR LGPL-3.0)"]
    assert run("be_license_allowlist_check", _lic([("a", "MIT or Apache-2.0")], ["Apache-2.0"]))["violation_count"] == 0


def test_license_and_expression_needs_every_term_allowed():
    assert run("be_license_allowlist_check", _lic([("a", "MIT AND GPL-3.0")], ["MIT"]))["violations"] == ["a (MIT AND GPL-3.0)"]
    assert run("be_license_allowlist_check", _lic([("a", "MIT AND BSD-3-Clause")], ["MIT", "BSD-3-Clause"]))["violation_count"] == 0


def test_license_and_binds_tighter_than_or():
    expr = "GPL-3.0 OR MIT AND Apache-2.0"
    assert run("be_license_allowlist_check", _lic([("a", expr)], ["MIT", "Apache-2.0"]))["violation_count"] == 0
    assert run("be_license_allowlist_check", _lic([("a", expr)], ["MIT"]))["violation_count"] == 1
    assert run("be_license_allowlist_check", _lic([("a", expr)], ["GPL-3.0"]))["violation_count"] == 0


def test_license_operators_may_be_separated_by_any_whitespace_including_newlines():
    assert run("be_license_allowlist_check", _lic([("a", "MIT\nOR\nGPL")], ["GPL"]))["violation_count"] == 0


@pytest.mark.parametrize(
    "expression",
    ["(MIT OR Apache-2.0)", "(MIT)", "MIT AND (Apache-2.0)", "MIT OR", "OR MIT", "MIT AND", "MIT WITH Classpath-exception-2.0", "Apache-2.0+", "MIT/Apache-2.0", "UNLICENSED"],
)
def test_license_unparseable_or_parenthesised_expressions_fail_closed(expression):
    result = run("be_license_allowlist_check", _lic([("a", expression)], ["MIT", "Apache-2.0"]))
    assert result["violations"] == [f"a ({expression})"] and result["violation_count"] == 1


def test_license_empty_license_is_a_violation_displayed_as_none():
    result = run("be_license_allowlist_check", _lic([("a", "")], ["MIT"]))
    assert result["violations"] == ["a (none)"]


def test_license_whitespace_only_license_is_a_violation():
    result = run("be_license_allowlist_check", _lic([("a", "   ")], ["MIT"]))
    assert result["violation_count"] == 1 and result["violations"][0].startswith("a (")


def test_license_no_packages_checks_nothing_and_passes():
    assert run("be_license_allowlist_check", _lic([], ["MIT"])) == {
        "state": "SUCCESS",
        "checked_count": 0,
        "violation_count": 0,
        "violations": [],
    }


def test_license_violations_keep_input_order_and_duplicates_are_not_merged():
    result = run("be_license_allowlist_check", _lic([("z", "GPL"), ("a", "MIT"), ("z", "GPL"), ("m", "AGPL")], ["MIT"]))
    assert result["checked_count"] == 4 and result["violation_count"] == 3
    assert result["violations"] == ["z (GPL)", "z (GPL)", "m (AGPL)"]


def test_license_allowlist_with_only_blank_entries_allows_nothing():
    assert run("be_license_allowlist_check", _lic([("a", "MIT")], ["  "]))["violation_count"] == 1


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"packages": []}, "allowlist", "missing required field allowlist"),
        ({"allowlist": ["MIT"]}, "packages", "missing required field packages"),
        (_lic([("a", "MIT")], []), "allowlist", "fewer than 1 items"),
        (_lic([("a", "MIT")], ["MIT"] * 201), "allowlist", "more than 200 items"),
        (_lic([("a", "MIT")], ["x" * 65]), "allowlist", "[0] longer than 64"),
        (_lic([("a", "MIT")] * 501, ["MIT"]), "packages", "more than 500 items"),
        (_lic([("a", "x" * 501)], ["MIT"]), "packages", "[0] license: longer than 500"),
        (_lic([("", "MIT")], ["MIT"]), "packages", "[0] name: shorter than 1"),
        ({"packages": [{"name": "a"}], "allowlist": ["MIT"]}, "packages", "[0] license: missing required field license"),
        ({"packages": "MIT", "allowlist": ["MIT"]}, "packages", "expected array"),
    ],
)
def test_license_schema_violations_name_the_field(payload, field, message):
    assert invalid(run("be_license_allowlist_check", payload), field)["message"] == message


def test_license_limits_are_inclusive():
    result = run("be_license_allowlist_check", _lic([("a", "MIT")] * 500, ["MIT"] * 200))
    assert result["checked_count"] == 500 and result["violation_count"] == 0
    assert run("be_license_allowlist_check", _lic([("a", "MIT")], ["x" * 64, "MIT"]))["violation_count"] == 0


# --- be_coverage_delta -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after", "delta", "direction", "regressed"),
    [
        (80, 85, 5.0, "up", False),
        (85, 80, -5.0, "down", True),
        (80, 80, 0.0, "flat", False),
        (0, 0, 0.0, "flat", False),
        (100, 100, 0.0, "flat", False),
        (0, 100, 100.0, "up", False),
        (100, 0, -100.0, "down", True),
        (0.1, 0.3, 0.2, "up", False),  # float noise (0.19999...) is rounded away
        (82.5, 80.25, -2.25, "down", True),
        (80.123, 80.125, 0.002, "up", False),  # full precision: a small change is never reported as flat
        (80.004, 80.0, -0.004, "down", True),
        (80.0, 79.999, -0.001, "down", True),
        (1e-9, 0, -1e-9, "down", True),
    ],
)
def test_coverage_delta_values_direction_and_regression(before, after, delta, direction, regressed):
    result = run("be_coverage_delta", {"before": before, "after": after})
    assert result == {"state": "SUCCESS", "delta": delta, "direction": direction, "regressed": regressed}


def test_coverage_delta_flat_result_is_positive_zero_never_negative_zero():
    import math

    result = run("be_coverage_delta", {"before": 80.0, "after": 80.0})
    assert result["delta"] == 0.0 and math.copysign(1.0, result["delta"]) == 1.0


def test_coverage_delta_keeps_full_precision():
    assert run("be_coverage_delta", {"before": 10, "after": 10.126})["delta"] == 0.126
    assert run("be_coverage_delta", {"before": 33.3333, "after": 66.6666})["delta"] == 33.3333


@pytest.mark.parametrize(("before", "after"), [(80.004, 80.0), (80.0, 79.999), (1e-9, 0)])
def test_coverage_delta_tiny_drop_has_consistent_direction_and_regressed_flag(before, after):
    # direction, delta sign and regressed all derive from the same comparison
    result = run("be_coverage_delta", {"before": before, "after": after})
    assert result["regressed"] is True and result["direction"] == "down" and result["delta"] < 0


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"before": -1, "after": 5}, "before", "below minimum 0"),
        ({"before": 5, "after": -0.01}, "after", "below minimum 0"),
        ({"before": 100.01, "after": 5}, "before", "above maximum 100"),
        ({"before": 5, "after": 101}, "after", "above maximum 100"),
        ({"before": 5}, "after", "missing required field after"),
        ({"after": 5}, "before", "missing required field before"),
        ({"before": "5", "after": 5}, "before", "expected number"),
        ({"before": True, "after": 5}, "before", "expected number"),
        ({"before": 5, "after": None}, "after", "expected number"),
    ],
)
def test_coverage_delta_out_of_range_missing_or_non_numeric_names_the_field(payload, field, message):
    assert invalid(run("be_coverage_delta", payload), field)["message"] == message


def test_coverage_delta_handler_rejects_non_finite_numbers_directly():
    from swarm.tools import a05_tools

    for bad in (float("nan"), float("inf")):
        result = a05_tools.coverage_delta({"before": bad, "after": 5})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "before"
        result = a05_tools.coverage_delta({"before": 5, "after": bad})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "after"


# --- be_acceptance_coverage ------------------------------------------------------------------------------------------


def test_acceptance_reports_uncovered_in_criteria_order_and_rounded_ratio():
    result = run("be_acceptance_coverage", {"criteria": ["AC3", "AC1", "AC2"], "covered": ["AC2"]})
    assert result == {
        "state": "SUCCESS",
        "total_count": 3,
        "covered_count": 1,
        "uncovered": ["AC3", "AC1"],
        "unmapped": [],
        "ratio": 0.3333,
    }


def test_acceptance_full_coverage_has_ratio_one_and_nothing_uncovered():
    result = run("be_acceptance_coverage", {"criteria": ["AC1", "AC2"], "covered": ["AC2", "AC1"]})
    assert (result["covered_count"], result["uncovered"], result["ratio"]) == (2, [], 1.0)


def test_acceptance_no_coverage_has_ratio_zero_and_all_uncovered():
    result = run("be_acceptance_coverage", {"criteria": ["AC1", "AC2"], "covered": []})
    assert (result["covered_count"], result["uncovered"], result["ratio"]) == (0, ["AC1", "AC2"], 0.0)


def test_acceptance_ratio_rounds_to_four_decimals():
    assert run("be_acceptance_coverage", {"criteria": ["a", "b", "c"], "covered": ["a", "b"]})["ratio"] == 0.6667
    criteria = [f"C{i}" for i in range(7)]
    assert run("be_acceptance_coverage", {"criteria": criteria, "covered": criteria[:1]})["ratio"] == 0.1429


def test_acceptance_duplicate_criteria_count_once():
    result = run("be_acceptance_coverage", {"criteria": ["AC1", "AC1", "AC2"], "covered": ["AC1"]})
    assert (result["total_count"], result["covered_count"], result["uncovered"], result["ratio"]) == (2, 1, ["AC2"], 0.5)


def test_acceptance_covered_ids_that_match_no_criterion_are_unmapped_sorted_and_deduped():
    result = run("be_acceptance_coverage", {"criteria": ["AC1"], "covered": ["AC9", "AC1", "AC8", "AC9"]})
    assert result["unmapped"] == ["AC8", "AC9"]
    assert (result["covered_count"], result["ratio"]) == (1, 1.0)  # stray ids never inflate coverage


def test_acceptance_matching_is_exact_case_and_whitespace_sensitive():
    result = run("be_acceptance_coverage", {"criteria": ["AC1"], "covered": ["ac1", " AC1"]})
    assert result["covered_count"] == 0 and result["uncovered"] == ["AC1"] and result["unmapped"] == [" AC1", "ac1"]


def test_acceptance_repeated_covered_ids_do_not_double_count():
    result = run("be_acceptance_coverage", {"criteria": ["AC1", "AC2"], "covered": ["AC1", "AC1", "AC1"]})
    assert (result["covered_count"], result["ratio"]) == (1, 0.5)


def test_acceptance_limits_500_criteria_and_1000_covered_inclusive():
    criteria = [f"A{i}" for i in range(500)]
    result = run("be_acceptance_coverage", {"criteria": criteria, "covered": criteria[:250] + criteria[:250]})
    assert (result["total_count"], result["covered_count"], result["ratio"]) == (500, 250, 0.5)
    assert result["uncovered"] == criteria[250:]
    assert invalid(run("be_acceptance_coverage", {"criteria": criteria + ["x"], "covered": []}), "criteria")["message"] == "more than 500 items"
    assert invalid(run("be_acceptance_coverage", {"criteria": ["AC1"], "covered": ["x"] * 1001}), "covered")["message"] == "more than 1000 items"
    assert run("be_acceptance_coverage", {"criteria": ["AC1"], "covered": ["x"] * 1000})["unmapped"] == ["x"]


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"criteria": [], "covered": []}, "criteria", "fewer than 1 items"),
        ({"criteria": ["AC1"]}, "covered", "missing required field covered"),
        ({"covered": []}, "criteria", "missing required field criteria"),
        ({"criteria": [""], "covered": []}, "criteria", "[0] shorter than 1"),
        ({"criteria": ["x" * 65], "covered": []}, "criteria", "[0] longer than 64"),
        ({"criteria": ["AC1"], "covered": ["x" * 65]}, "covered", "[0] longer than 64"),
        ({"criteria": [1], "covered": []}, "criteria", "[0] expected string"),
        ({"criteria": "AC1", "covered": []}, "criteria", "expected array"),
        ({"criteria": ["AC1"], "covered": [None]}, "covered", "[0] expected string"),
    ],
)
def test_acceptance_schema_violations_name_the_field(payload, field, message):
    assert invalid(run("be_acceptance_coverage", payload), field)["message"] == message


def test_acceptance_handler_rejects_an_empty_criteria_list_even_when_called_directly():
    from swarm.tools import a05_tools

    result = a05_tools.acceptance_coverage({"criteria": [], "covered": []})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "criteria"
    assert a05_tools.acceptance_coverage({"criteria": ["a"], "covered": ["a"]})["ratio"] == 1.0
