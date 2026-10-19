"""Behaviour of the pre-existing A03 lane tools (ROLE-01): arch_adr_slug, arch_contract_ops, arch_semver_bump,
arch_field_breaks, arch_layer_violations, arch_cycle_check, arch_c4_fanout, arch_adr_transition, arch_nfr_budget.

Every assertion is on output values for specific inputs. Calls go through the registry with the agent id; the
handlers are also called directly where the registry would reject the input first. conftest evicts swarm* modules
between tests, so the registry and handlers are resolved at call time from the current module graph.
"""
from __future__ import annotations

import importlib

import pytest

AGENT = "A03"


def call(name: str, payload: dict, caller: str = AGENT) -> dict:
    return importlib.import_module("swarm.tool_registry").call_tool(name, caller, payload)


def handler(name: str):
    return getattr(importlib.import_module("swarm.tools.a03_tools"), name)


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result


# ---- arch_adr_slug -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Use Postgres", "use-postgres"),
        ("  Use   PostgreSQL 15 / pgbouncer!!  ", "use-postgresql-15-pgbouncer"),
        ("UPPER_case--Mixed", "upper-case-mixed"),
        ("a", "a"),
    ],
)
def test_adr_slug_lowercases_and_collapses_separators(title, slug):
    assert call("arch_adr_slug", {"title": title}) == {"state": "SUCCESS", "slug": slug}


def test_adr_slug_without_alphanumerics_falls_back_to_adr():
    assert call("arch_adr_slug", {"title": "!!! ---"})["slug"] == "adr"


def test_adr_slug_truncates_at_sixty_characters():
    assert call("arch_adr_slug", {"title": "x" * 100})["slug"] == "x" * 60
    assert call("arch_adr_slug", {"title": "y" * 60})["slug"] == "y" * 60


def test_adr_slug_drops_non_ascii_letters_like_the_adr_script():
    assert call("arch_adr_slug", {"title": "Ünïcode Café"})["slug"] == "n-code-caf"


def test_adr_slug_rejects_empty_missing_and_non_string_titles():
    invalid(call("arch_adr_slug", {"title": ""}), "title")
    invalid(call("arch_adr_slug", {}), "title")
    invalid(call("arch_adr_slug", {"title": 7}), "title")
    invalid(call("arch_adr_slug", {"title": "ok", "unknown": 1}), "unknown")


def test_adr_slug_is_restricted_to_the_architect():
    assert call("arch_adr_slug", {"title": "Use Postgres"}, caller="A01")["state"] == "UNAUTHORIZED"


# ---- arch_contract_ops ---------------------------------------------------------------------


def test_contract_ops_counts_openapi_methods_per_path():
    document = {
        "openapi": "3.0.3",
        "paths": {"/a": {"get": {}, "post": {}, "parameters": []}, "/b": {"delete": None, "x-ext": {}}},
    }
    assert call("arch_contract_ops", {"document": document}) == {"state": "SUCCESS", "operations": 3}


def test_contract_ops_counts_asyncapi_channels_and_v3_operations():
    v2 = {"asyncapi": "2.6.0", "channels": {"orders": {"publish": {}, "subscribe": {}}, "audit": {"publish": {}}}}
    assert call("arch_contract_ops", {"document": v2})["operations"] == 3
    v3 = {"asyncapi": "3.0.0", "operations": {"sendOrder": {"action": "send"}, "recvOrder": {}}}
    assert call("arch_contract_ops", {"document": v3})["operations"] == 2


def test_contract_ops_of_empty_document_is_zero():
    assert call("arch_contract_ops", {"document": {}}) == {"state": "SUCCESS", "operations": 0}
    assert call("arch_contract_ops", {"document": {"openapi": "3.0.3", "paths": {}}})["operations"] == 0


def test_contract_ops_ignores_non_dict_path_items_and_unknown_verbs():
    document = {"openapi": "3.0.3", "paths": {"/a": "nope", "/b": {"connect": {}, "get": {}}}}
    assert call("arch_contract_ops", {"document": document})["operations"] == 1


def test_contract_ops_rejects_non_object_documents():
    invalid(call("arch_contract_ops", {"document": []}), "document")
    invalid(call("arch_contract_ops", {"document": "openapi"}), "document")
    invalid(call("arch_contract_ops", {}), "document")


def test_contract_ops_handler_direct_non_object_message():
    result = handler_batch("contract_ops")({"document": []})
    assert result == {"state": "INVALID_INPUT", "field": "document", "message": "document must be an object"}


def test_contract_ops_is_open_to_frontend_but_not_to_others():
    document = {"openapi": "3.0.3", "paths": {"/a": {"get": {}}}}
    assert call("arch_contract_ops", {"document": document}, caller="A06")["operations"] == 1
    assert call("arch_contract_ops", {"document": document}, caller="A05")["state"] == "UNAUTHORIZED"


def handler_batch(name: str):
    return getattr(importlib.import_module("swarm.tools.batch_01_05"), name)


# ---- arch_semver_bump ----------------------------------------------------------------------


def semver(base, head, breaking=False, additive=False, caller=AGENT):
    return call(
        "arch_semver_bump",
        {"base_version": base, "head_version": head, "breaking_change": breaking, "additive_change": additive},
        caller,
    )


@pytest.mark.parametrize(
    ("base", "head", "bump"),
    [
        ("1.2.3", "2.0.0", "major"),
        ("1.2.3", "1.3.0", "minor"),
        ("1.2.3", "1.2.4", "patch"),
        ("1.2.3", "1.2.3", "none"),
        ("1.2.3", "1.2.2", "downgrade"),
        ("1.2.3", "1.1.9", "downgrade"),
        ("2.0.0", "1.9.9", "downgrade"),
        ("0.9.9", "1.0.0", "major"),
        ("1.2.3", "2.5.7", "major"),
        ("1.2.3", "1.3.9", "minor"),
    ],
)
def test_semver_classifies_the_step(base, head, bump):
    assert semver(base, head)["bump"] == bump


def test_semver_success_shape_and_compliant_without_change():
    assert semver("1.0.0", "1.0.1") == {"state": "SUCCESS", "bump": "patch", "required_bump": "none", "compliant": True}


def test_semver_breaking_change_requires_major_even_on_zero_major():
    assert semver("0.4.0", "0.5.0", breaking=True) == {
        "state": "SUCCESS", "bump": "minor", "required_bump": "major", "compliant": False,
    }
    assert semver("0.4.0", "1.0.0", breaking=True)["compliant"] is True


def test_semver_breaking_wins_over_additive():
    result = semver("1.0.0", "1.1.0", breaking=True, additive=True)
    assert result["required_bump"] == "major" and result["compliant"] is False


def test_semver_additive_change_requires_minor_and_accepts_major():
    assert semver("1.0.0", "1.0.1", additive=True) == {
        "state": "SUCCESS", "bump": "patch", "required_bump": "minor", "compliant": False,
    }
    assert semver("1.0.0", "1.1.0", additive=True)["compliant"] is True
    assert semver("1.0.0", "2.0.0", additive=True)["compliant"] is True


def test_semver_no_bump_fails_any_required_bump_and_passes_when_none_required():
    assert semver("1.0.0", "1.0.0", additive=True)["compliant"] is False
    assert semver("1.0.0", "1.0.0")["compliant"] is True


def test_semver_downgrade_is_never_compliant():
    result = semver("2.0.0", "1.0.0")
    assert result["bump"] == "downgrade" and result["required_bump"] == "none" and result["compliant"] is False


def test_semver_multi_digit_components_compare_numerically():
    assert semver("1.9.0", "1.10.0")["bump"] == "minor"
    assert semver("9.0.0", "10.0.0")["bump"] == "major"
    assert semver("1.0.9", "1.0.10")["bump"] == "patch"


@pytest.mark.parametrize("bad", ["1.0.0-rc.1", "1.0.0+build", "v1.0.0", "01.0.0", "1.0.x", "1.0.0.0"])
def test_semver_rejects_prerelease_build_and_malformed_versions(bad):
    invalid(semver(bad, "1.0.0"), "base_version")
    invalid(semver("1.0.0", bad), "head_version")


def test_semver_length_and_type_bounds_are_enforced_by_the_registry():
    invalid(semver("1.0", "1.0.1"), "base_version")  # shorter than 5
    invalid(semver("1.0.0", "1" * 20 + "." + "2" * 20 + ".3"), "head_version")  # longer than 32
    invalid(call("arch_semver_bump", {"base_version": "1.0.0", "head_version": "1.0.1", "breaking_change": "no",
                                      "additive_change": False}), "breaking_change")
    invalid(call("arch_semver_bump", {"base_version": "1.0.0", "head_version": "1.0.1", "breaking_change": False}),
            "additive_change")


def test_semver_base_is_checked_before_head_and_bad_head_alone_is_named():
    invalid(semver("bad..0", "also bad"), "base_version")
    invalid(semver("1.0.0", "bad..0"), "head_version")


def test_semver_is_restricted_to_the_architect():
    assert semver("1.0.0", "1.0.1", caller="A06")["state"] == "UNAUTHORIZED"


# ---- arch_field_breaks ---------------------------------------------------------------------


def field(name, type_="string", required=True):
    return {"name": name, "type": type_, "required": required}


def breaks(side, base, head, caller=AGENT):
    return call("arch_field_breaks", {"side": side, "base_fields": base, "head_fields": head}, caller)


def test_field_breaks_identical_sets_report_nothing():
    fields = [field("id", "integer"), field("note", required=False)]
    assert breaks("request", fields, fields) == {
        "state": "SUCCESS", "breaking_change": False, "breaking": [], "additive": [],
    }


def test_field_breaks_removed_field_is_breaking_on_both_sides():
    for side in ("request", "response"):
        result = breaks(side, [field("id"), field("note", required=False)], [field("id")])
        assert result["breaking"] == ["removed field note"] and result["breaking_change"] is True
        assert result["additive"] == []


def test_field_breaks_type_change_is_breaking_on_both_sides():
    for side in ("request", "response"):
        result = breaks(side, [field("id", "integer")], [field("id", "string")])
        assert result["breaking"] == ["type of id changed integer -> string"]


def test_field_breaks_request_side_new_required_field_is_breaking_new_optional_is_additive():
    result = breaks("request", [field("id")], [field("id"), field("tenant"), field("note", required=False)])
    assert result["breaking"] == ["new required field tenant"]
    assert result["additive"] == ["added field note"]
    assert result["breaking_change"] is True


def test_field_breaks_response_side_new_fields_are_always_additive():
    result = breaks("response", [field("id")], [field("id"), field("tenant"), field("note", required=False)])
    assert result["breaking"] == [] and result["breaking_change"] is False
    assert result["additive"] == ["added field note", "added field tenant"]


def test_field_breaks_optional_to_required_is_breaking_only_on_request():
    base, head = [field("n", required=False)], [field("n", required=True)]
    assert breaks("request", base, head)["breaking"] == ["field n became required"]
    response = breaks("response", base, head)
    assert response["breaking"] == [] and response["additive"] == ["field n became required"]


def test_field_breaks_required_to_optional_is_breaking_only_on_response():
    base, head = [field("n", required=True)], [field("n", required=False)]
    assert breaks("response", base, head)["breaking"] == ["field n became optional"]
    request = breaks("request", base, head)
    assert request["breaking"] == [] and request["additive"] == ["field n became optional"]
    assert request["breaking_change"] is False


def test_field_breaks_reports_type_and_requiredness_changes_of_one_field_together():
    result = breaks("request", [field("n", "integer", False)], [field("n", "string", True)])
    assert result["breaking"] == ["type of n changed integer -> string", "field n became required"]


def test_field_breaks_output_is_sorted_by_field_name_not_input_order():
    base = [field("zeta"), field("alpha"), field("mid")]
    result = breaks("response", base, [])
    assert result["breaking"] == ["removed field alpha", "removed field mid", "removed field zeta"]
    added = breaks("response", [], [field("zeta"), field("alpha")])
    assert added["additive"] == ["added field alpha", "added field zeta"]


def test_field_breaks_empty_sets_are_valid_and_clean():
    assert breaks("request", [], [])["breaking_change"] is False


def test_field_breaks_duplicate_names_name_the_offending_set():
    base = call("arch_field_breaks", {"side": "request", "base_fields": [field("a"), field("a")], "head_fields": []})
    invalid(base, "base_fields")
    assert "duplicate field name a in base_fields" in base["message"]
    head = breaks("request", [], [field("b"), field("b", "integer")])
    invalid(head, "head_fields")
    assert "duplicate field name b in head_fields" in head["message"]


def test_field_breaks_schema_violations_name_the_field():
    invalid(breaks("both", [], []), "side")
    invalid(call("arch_field_breaks", {"side": "request", "base_fields": []}), "head_fields")
    invalid(breaks("request", [{"name": "a", "type": "string"}], []), "base_fields")
    invalid(breaks("request", [field("")], []), "base_fields")
    invalid(breaks("request", [], [field("n" * 65)]), "head_fields")
    invalid(breaks("request", [field(f"f{i}") for i in range(201)], []), "base_fields")


def test_field_breaks_is_restricted_to_the_architect():
    assert breaks("request", [], [], caller="A05")["state"] == "UNAUTHORIZED"


# ---- arch_layer_violations -----------------------------------------------------------------

ORDER = ["api", "service", "data"]  # outermost first; modules may depend on their layer or later ones


def layers(assignments, edges, order=ORDER, caller=AGENT):
    return call(
        "arch_layer_violations",
        {
            "layer_order": order,
            "assignments": [{"module": module, "layer": layer} for module, layer in assignments],
            "edges": [{"from": a, "to": b} for a, b in edges],
        },
        caller,
    )


def test_layers_downward_and_same_layer_edges_are_clean():
    result = layers(
        [("ctl", "api"), ("svc", "service"), ("svc2", "service"), ("repo", "data")],
        [("ctl", "svc"), ("svc", "repo"), ("svc", "svc2"), ("ctl", "repo")],
    )
    assert result == {"state": "SUCCESS", "violation_count": 0, "checked_edges": 4, "violations": [], "unassigned": []}


def test_layers_upward_edges_are_reported_in_edge_order_with_layer_names():
    result = layers(
        [("ctl", "api"), ("svc", "service"), ("repo", "data")],
        [("repo", "svc"), ("svc", "ctl"), ("ctl", "svc"), ("repo", "ctl")],
    )
    assert result["violation_count"] == 3 and result["checked_edges"] == 4
    assert result["violations"] == [
        "repo (data) -> svc (service)",
        "svc (service) -> ctl (api)",
        "repo (data) -> ctl (api)",
    ]


def test_layers_self_edge_is_not_a_violation():
    result = layers([("a", "service")], [("a", "a")])
    assert result["violation_count"] == 0 and result["checked_edges"] == 1


def test_layers_edges_touching_unassigned_modules_are_reported_not_judged():
    result = layers(
        [("ctl", "api"), ("repo", "data")],
        [("repo", "ghost"), ("ghost", "ctl"), ("ghost2", "ghost"), ("ctl", "repo")],
    )
    assert result["checked_edges"] == 1 and result["violation_count"] == 0
    assert result["unassigned"] == ["ghost", "ghost2"]


def test_layers_no_edges_and_no_assignments_is_clean():
    result = layers([], [])
    assert result == {"state": "SUCCESS", "violation_count": 0, "checked_edges": 0, "violations": [], "unassigned": []}


def test_layers_duplicate_layer_names_are_invalid():
    invalid(layers([], [], order=["api", "api"]), "layer_order")


def test_layers_module_assigned_twice_is_invalid_even_to_the_same_layer():
    result = layers([("a", "api"), ("a", "api")], [])
    invalid(result, "assignments")
    assert "module a is assigned twice" in result["message"]


def test_layers_unknown_layer_in_assignment_is_invalid():
    result = layers([("a", "ui")], [])
    invalid(result, "assignments")
    assert "layer ui is not in layer_order" in result["message"]


def test_layers_schema_bounds_name_the_field():
    invalid(layers([], [], order=["only"]), "layer_order")
    invalid(layers([], [], order=[f"l{i}" for i in range(13)]), "layer_order")
    invalid(call("arch_layer_violations", {"layer_order": ORDER, "assignments": []}), "edges")
    invalid(call("arch_layer_violations", {"layer_order": ORDER, "assignments": [{"module": "a"}], "edges": []}),
            "assignments")
    invalid(call("arch_layer_violations", {"layer_order": ORDER, "assignments": [], "edges": [{"from": "a"}]}),
            "edges")


def test_layers_is_restricted_to_the_architect():
    assert layers([], [], caller="A06")["state"] == "UNAUTHORIZED"


# ---- arch_cycle_check ----------------------------------------------------------------------


def cycles(edges, caller=AGENT):
    return call("arch_cycle_check", {"edges": [{"from": a, "to": b} for a, b in edges]}, caller)


def test_cycle_check_empty_graph():
    assert cycles([]) == {
        "state": "SUCCESS", "has_cycle": False, "node_count": 0, "cyclic_node_count": 0, "cyclic_components": [],
    }


def test_cycle_check_acyclic_graph_has_nodes_but_no_cycle():
    result = cycles([("a", "b"), ("b", "c"), ("a", "c")])
    assert result["has_cycle"] is False and result["node_count"] == 3
    assert result["cyclic_components"] == [] and result["cyclic_node_count"] == 0


def test_cycle_check_two_node_cycle_lists_sorted_members():
    result = cycles([("b", "a"), ("a", "b")])
    assert result["has_cycle"] is True
    assert result["cyclic_components"] == ["a, b"] and result["cyclic_node_count"] == 2


def test_cycle_check_self_edge_is_a_cycle_of_one():
    result = cycles([("a", "a"), ("a", "b")])
    assert result["cyclic_components"] == ["a"] and result["cyclic_node_count"] == 1 and result["node_count"] == 2


def test_cycle_check_tail_nodes_are_not_part_of_the_cyclic_component():
    result = cycles([("x", "a"), ("a", "b"), ("b", "c"), ("c", "a"), ("c", "y")])
    assert result["cyclic_components"] == ["a, b, c"]
    assert result["node_count"] == 5 and result["cyclic_node_count"] == 3


def test_cycle_check_separate_components_are_sorted_by_first_member():
    result = cycles([("q", "r"), ("r", "q"), ("m", "n"), ("n", "m"), ("z", "z")])
    assert result["cyclic_components"] == ["m, n", "q, r", "z"]
    assert result["cyclic_node_count"] == 5


def test_cycle_check_two_cycles_sharing_a_node_are_one_component():
    result = cycles([("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")])
    assert result["cyclic_components"] == ["a, b, c"]


def test_cycle_check_duplicate_edges_do_not_change_the_result():
    once = cycles([("a", "b"), ("b", "a")])
    many = cycles([("a", "b"), ("a", "b"), ("b", "a"), ("b", "a")])
    assert once == many


def test_cycle_check_deep_chain_closed_into_a_ring_does_not_hit_recursion_limit():
    size = 3000
    edges = [(f"n{i:05d}", f"n{(i + 1) % size:05d}") for i in range(size)]
    result = cycles(edges)
    assert result["has_cycle"] is True and result["node_count"] == size and result["cyclic_node_count"] == size
    assert len(result["cyclic_components"]) == 1


def test_cycle_check_deep_open_chain_has_no_cycle():
    edges = [(f"n{i}", f"n{i + 1}") for i in range(3000)]
    result = cycles(edges)
    assert result["has_cycle"] is False and result["node_count"] == 3001


def test_cycle_check_rejects_malformed_edges_and_oversized_input():
    invalid(call("arch_cycle_check", {}), "edges")
    invalid(call("arch_cycle_check", {"edges": [{"from": "a"}]}), "edges")
    invalid(call("arch_cycle_check", {"edges": [{"from": "", "to": "a"}]}), "edges")
    invalid(call("arch_cycle_check", {"edges": [{"from": "a" * 201, "to": "b"}]}), "edges")
    invalid(call("arch_cycle_check", {"edges": [{"from": "a", "to": "b"}] * 5001}), "edges")


def test_cycle_check_is_restricted_to_the_architect():
    assert cycles([], caller="A05")["state"] == "UNAUTHORIZED"


# ---- arch_c4_fanout ------------------------------------------------------------------------


def c4(containers, relations, budget, caller=AGENT):
    return call(
        "arch_c4_fanout",
        {"containers": containers, "relations": [{"from": a, "to": b} for a, b in relations], "fan_out_budget": budget},
        caller,
    )


def test_c4_counts_and_within_budget():
    result = c4(["web", "api", "db"], [("web", "api"), ("api", "db")], 2)
    assert result == {
        "state": "SUCCESS", "container_count": 3, "relation_count": 2, "max_fan_out": 1,
        "over_budget": [], "isolated": [],
    }


def test_c4_budget_boundary_equal_is_ok_one_over_is_flagged():
    relations = [("hub", "a"), ("hub", "b"), ("hub", "c")]
    assert c4(["hub", "a", "b", "c"], relations, 3)["over_budget"] == []
    over = c4(["hub", "a", "b", "c"], relations, 2)
    assert over["over_budget"] == ["hub=3"] and over["max_fan_out"] == 3


def test_c4_over_budget_sorted_by_count_desc_then_name():
    containers = ["a", "b", "c", "d", "e", "f"]
    relations = [("b", "a"), ("b", "c"), ("b", "d"), ("e", "a"), ("e", "c"), ("e", "d"), ("c", "a"), ("c", "d")]
    result = c4(containers, relations, 1)
    assert result["over_budget"] == ["b=3", "e=3", "c=2"]


def test_c4_duplicate_relations_count_once():
    result = c4(["a", "b"], [("a", "b"), ("a", "b"), ("a", "b")], 1)
    assert result["relation_count"] == 1 and result["max_fan_out"] == 1 and result["over_budget"] == []


def test_c4_fan_out_is_outgoing_only_and_opposite_directions_are_two_relations():
    result = c4(["a", "b"], [("a", "b"), ("b", "a")], 1)
    assert result["relation_count"] == 2 and result["max_fan_out"] == 1
    inbound = c4(["a", "b", "c", "d"], [("b", "a"), ("c", "a"), ("d", "a")], 1)
    assert inbound["over_budget"] == [] and inbound["max_fan_out"] == 1


def test_c4_isolated_means_no_outgoing_and_no_incoming_relations():
    result = c4(["zed", "alone", "src", "sink"], [("src", "sink")], 5)
    assert result["isolated"] == ["alone", "zed"]


def test_c4_no_relations_makes_everything_isolated_and_max_zero():
    result = c4(["b", "a"], [], 1)
    assert result["max_fan_out"] == 0 and result["relation_count"] == 0 and result["isolated"] == ["a", "b"]


def test_c4_duplicate_container_names_are_invalid():
    result = c4(["a", "a"], [], 1)
    invalid(result, "containers")
    assert "unique" in result["message"]


def test_c4_unknown_container_in_relation_is_invalid_for_either_end():
    first = c4(["a"], [("a", "ghost")], 1)
    invalid(first, "relations")
    assert "unknown container in a -> ghost" in first["message"]
    invalid(c4(["a"], [("ghost", "a")], 1), "relations")


def test_c4_self_relation_is_invalid():
    result = c4(["a"], [("a", "a")], 1)
    invalid(result, "relations")
    assert "container a relates to itself" in result["message"]


def test_c4_schema_bounds_name_the_field():
    invalid(c4([], [], 1), "containers")
    invalid(c4(["a"], [], 0), "fan_out_budget")
    invalid(c4(["a"], [], 101), "fan_out_budget")
    invalid(c4(["a"], [], 1.5), "fan_out_budget")
    invalid(c4([f"c{i}" for i in range(201)], [], 1), "containers")
    invalid(call("arch_c4_fanout", {"containers": ["a"], "relations": []}), "fan_out_budget")


def test_c4_is_restricted_to_the_architect():
    assert c4(["a"], [], 1, caller="A04")["state"] == "UNAUTHORIZED"


# ---- arch_adr_transition -------------------------------------------------------------------


def transition(src, dst, by=None, caller=AGENT):
    payload = {"from_status": src, "to_status": dst}
    if by is not None:
        payload["superseded_by"] = by
    return call("arch_adr_transition", payload, caller)


@pytest.mark.parametrize(
    ("src", "dst"),
    [("proposed", "accepted"), ("proposed", "rejected"), ("accepted", "deprecated"), ("deprecated", "superseded", )],
)
def test_adr_transition_allowed_moves(src, dst):
    by = "ADR-0009" if dst == "superseded" else None
    result = transition(src, dst, by)
    assert result["state"] == "SUCCESS" and result["allowed"] is True
    expected = f"{src} -> {dst} is allowed" + (" by ADR-0009" if by else "")
    assert result["reason"] == expected


def test_adr_transition_accepted_to_superseded_needs_and_names_the_successor():
    refused = transition("accepted", "superseded")
    assert refused == {
        "state": "SUCCESS", "allowed": False,
        "reason": "superseded requires superseded_by, the id of the superseding ADR",
    }
    ok = transition("accepted", "superseded", "ADR-0042")
    assert ok == {"state": "SUCCESS", "allowed": True, "reason": "accepted -> superseded is allowed by ADR-0042"}


@pytest.mark.parametrize(("src", "dst"), [("proposed", "deprecated"), ("proposed", "superseded"),
                                           ("accepted", "proposed"), ("accepted", "rejected"),
                                           ("deprecated", "accepted"), ("deprecated", "proposed")])
def test_adr_transition_skipping_or_reversing_lifecycle_is_refused(src, dst):
    result = transition(src, dst, "ADR-0001")
    assert result["allowed"] is False and result["reason"] == f"{src} cannot move to {dst}"


@pytest.mark.parametrize("terminal", ["rejected", "superseded"])
def test_adr_transition_terminal_statuses_allow_nothing(terminal):
    for dst in ("proposed", "accepted", "rejected", "deprecated", "superseded"):
        if dst == terminal:
            continue
        result = transition(terminal, dst, "ADR-0001")
        assert result["allowed"] is False and result["reason"] == f"{terminal} is terminal"


@pytest.mark.parametrize("status", ["proposed", "accepted", "rejected", "deprecated", "superseded"])
def test_adr_transition_same_status_is_refused(status):
    result = transition(status, status)
    assert result["allowed"] is False and result["reason"] == f"status is already {status}"


def test_adr_transition_superseded_by_is_ignored_for_other_targets():
    result = transition("proposed", "accepted", "ADR-0003")
    assert result["allowed"] is True and result["reason"] == "proposed -> accepted is allowed"


def test_adr_transition_rejects_unknown_status_and_blank_successor():
    invalid(transition("draft", "accepted"), "from_status")
    invalid(transition("proposed", "done"), "to_status")
    invalid(transition("accepted", "superseded", ""), "superseded_by")
    invalid(call("arch_adr_transition", {"from_status": "proposed"}), "to_status")
    invalid(transition("accepted", "superseded", "x" * 65), "superseded_by")


def test_adr_transition_handler_treats_empty_successor_as_missing():
    result = handler("adr_transition")({"from_status": "accepted", "to_status": "superseded", "superseded_by": ""})
    assert result["allowed"] is False and "superseded_by" in result["reason"]


def test_adr_transition_is_restricted_to_the_architect():
    assert transition("proposed", "accepted", caller="A09")["state"] == "UNAUTHORIZED"


# ---- arch_nfr_budget -----------------------------------------------------------------------


def budget(metric, limit, direction="max"):
    return {"metric": metric, "limit": limit, "direction": direction}


def measure(metric, value):
    return {"metric": metric, "value": value}


def nfr(budgets, measurements, caller=AGENT):
    return call("arch_nfr_budget", {"budgets": budgets, "measurements": measurements}, caller)


def test_nfr_budget_all_within_budget():
    result = nfr([budget("p99_ms", 200), budget("uptime", 99.9, "min")],
                 [measure("p99_ms", 150), measure("uptime", 99.95)])
    assert result == {"state": "SUCCESS", "checked": 2, "within_budget": True, "breaches": [], "unmeasured": []}


def test_nfr_budget_max_breach_message_and_strict_boundary():
    over = nfr([budget("p99_ms", 200)], [measure("p99_ms", 200.5)])
    assert over["within_budget"] is False and over["breaches"] == ["p99_ms 200.5 > 200 (max)"]
    assert nfr([budget("p99_ms", 200)], [measure("p99_ms", 200)])["within_budget"] is True


def test_nfr_budget_min_breach_message_and_strict_boundary():
    under = nfr([budget("uptime", 99.9, "min")], [measure("uptime", 99.5)])
    assert under["breaches"] == ["uptime 99.5 < 99.9 (min)"] and under["within_budget"] is False
    assert nfr([budget("uptime", 99.9, "min")], [measure("uptime", 99.9)])["within_budget"] is True


def test_nfr_budget_unmeasured_budget_fails_closed():
    result = nfr([budget("p99_ms", 200), budget("rps", 100, "min")], [measure("p99_ms", 10)])
    assert result["checked"] == 1 and result["unmeasured"] == ["rps"] and result["breaches"] == []
    assert result["within_budget"] is False


def test_nfr_budget_no_measurements_at_all_is_unmeasured_and_not_within_budget():
    result = nfr([budget("a", 1), budget("b", 2)], [])
    assert result["checked"] == 0 and result["unmeasured"] == ["a", "b"] and result["within_budget"] is False


def test_nfr_budget_extra_measurements_without_budget_are_ignored():
    result = nfr([budget("a", 5)], [measure("a", 1), measure("other", 1_000_000)])
    assert result["checked"] == 1 and result["within_budget"] is True


def test_nfr_budget_collects_every_breach_in_budget_order_and_keeps_unmeasured_separate():
    result = nfr(
        [budget("z", 1), budget("a", 10, "min"), budget("m", 5), budget("gone", 1)],
        [measure("m", 6), measure("a", 1), measure("z", 0)],
    )
    assert result["breaches"] == ["a 1 < 10 (min)", "m 6 > 5 (max)"]
    assert result["unmeasured"] == ["gone"] and result["checked"] == 3 and result["within_budget"] is False


def test_nfr_budget_zero_and_negative_limits_are_real_limits():
    assert nfr([budget("errors", 0)], [measure("errors", 0)])["within_budget"] is True
    assert nfr([budget("errors", 0)], [measure("errors", 1)])["breaches"] == ["errors 1 > 0 (max)"]
    assert nfr([budget("delta", -5, "min")], [measure("delta", -6)])["within_budget"] is False


def test_nfr_budget_duplicate_budget_or_measurement_metrics_are_invalid():
    invalid(nfr([budget("a", 1), budget("a", 2)], []), "budgets")
    result = nfr([budget("a", 1)], [measure("a", 1), measure("a", 2)])
    invalid(result, "measurements")
    assert "metric a is measured twice" in result["message"]


def test_nfr_budget_schema_violations_name_the_field():
    invalid(nfr([], []), "budgets")
    invalid(nfr([budget("a", 1, "avg")], []), "budgets")
    invalid(nfr([budget("a", "1")], []), "budgets")
    invalid(nfr([budget("a", 1)], [measure("a", True)]), "measurements")
    invalid(nfr([budget("a", 1)], [measure("a", "5")]), "measurements")
    invalid(call("arch_nfr_budget", {"budgets": [budget("a", 1)]}), "measurements")
    invalid(nfr([budget("", 1)], []), "budgets")
    invalid(nfr([budget(f"m{i}", 1) for i in range(201)], []), "budgets")


def test_nfr_budget_infinite_measurement_is_invalid_input():
    invalid(nfr([budget("p99_ms", 200)], [measure("p99_ms", float("inf"))]), "measurements")


def test_nfr_budget_nan_measurement_is_invalid_input():
    # The registry refuses NaN/Infinity for every number field, so a non-measurement can never pass as within budget.
    result = nfr([budget("p99_ms", 200)], [measure("p99_ms", float("nan"))])
    invalid(result, "measurements")
    assert result["message"].endswith("expected a finite number")


def test_nfr_budget_is_restricted_to_the_architect():
    assert nfr([budget("a", 1)], [measure("a", 1)], caller="A10")["state"] == "UNAUTHORIZED"
