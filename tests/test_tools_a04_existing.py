"""Behaviour of the v2.1 A04 lane tools (the generated probes only assert the terminal state).

Covers ux_contrast, ux_token_name_check, ux_target_size_check, ux_scale_monotonic_check, ux_spacing_scale_check,
ux_state_coverage_check, ux_focus_contrast_check, ux_motion_duration_check and ux_breakpoint_order_check through the
registry (input schema, caller check, handler) and, for a few rules, through the handler function directly.

conftest's swarm_dir fixture evicts swarm* modules between tests, so `call_tool` and the handler module are resolved
lazily from the current module graph.
"""
from __future__ import annotations

import importlib

import pytest


def run(name: str, payload: dict, caller: str = "A04") -> dict:
    return importlib.import_module("swarm.tool_registry").call_tool(name, caller, payload)


def handlers():
    return importlib.import_module("swarm.tools.a04_tools")


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    assert result["message"], result


def unauthorized(name: str, payload: dict) -> None:
    for caller in ("A05", "A01"):
        result = run(name, payload, caller=caller)
        assert result["state"] == "UNAUTHORIZED" and result["caller"] == caller and result["target"] == name, result


# --- ux_contrast ------------------------------------------------------------------------------------------------


def test_contrast_black_on_white_is_21_and_order_does_not_matter():
    assert run("ux_contrast", {"fg": "#000000", "bg": "#ffffff"}) == {"state": "SUCCESS", "ratio": 21.0}
    assert run("ux_contrast", {"fg": "#ffffff", "bg": "#000000"}) == {"state": "SUCCESS", "ratio": 21.0}


def test_contrast_identical_colours_is_exactly_one():
    assert run("ux_contrast", {"fg": "#336699", "bg": "#336699"})["ratio"] == 1.0


def test_contrast_known_wcag_reference_pair_is_truncated_to_two_decimals():
    # #767676 on white is the canonical smallest grey that passes AA for normal text
    assert run("ux_contrast", {"fg": "#767676", "bg": "#ffffff"})["ratio"] == 4.54
    assert run("ux_contrast", {"fg": "#777777", "bg": "#ffffff"})["ratio"] == 4.47  # 4.4778 truncates, never rounds up


def test_contrast_accepts_three_digit_and_hash_less_hex_equivalently():
    long_form = run("ux_contrast", {"fg": "#336699", "bg": "#ffffff"})
    assert run("ux_contrast", {"fg": "#369", "bg": "#fff"}) == long_form
    assert run("ux_contrast", {"fg": "336699", "bg": "ffffff"}) == long_form


def test_contrast_is_case_insensitive():
    assert run("ux_contrast", {"fg": "#ABCDEF", "bg": "#012345"}) == run("ux_contrast", {"fg": "#abcdef", "bg": "#012345"})


@pytest.mark.parametrize("fg, bg, ratio", [("#2671e5", "#060201", 4.49), ("#1c5f98", "#190102", 2.99)])
def test_contrast_just_below_a_threshold_never_reports_the_threshold(fg, bg, ratio):
    """RV-H P3-1: true ratios 4.499999999992116 and 2.9999999999994 were reported as 4.5 and 3.0 (epsilon 1e-9)."""
    assert run("ux_contrast", {"fg": fg, "bg": bg}) == {"state": "SUCCESS", "ratio": ratio}
    assert run("ux_contrast", {"fg": bg, "bg": fg})["ratio"] == ratio


def test_contrast_matches_fraction_exact_arithmetic_on_20000_seeded_pairs():
    """The float result is floor(100 x ratio) / 100 of the exact ratio of the computed luminances (Fraction arithmetic),
    except where the exact value is within float noise (1e-11 at x100) below the next hundredth."""
    import math
    import random
    from fractions import Fraction

    ux = importlib.import_module("swarm.tools.support").script("ux_tokens")
    rng = random.Random(20260619)
    thresholds = (3, 4.5, 7)
    for _ in range(20_000):
        fg, bg = (f"#{rng.randrange(1 << 24):06x}" for _ in range(2))
        l1, l2 = sorted((Fraction(ux.luminance(fg)), Fraction(ux.luminance(bg))), reverse=True)
        exact = (l1 + Fraction(1, 20)) / (l2 + Fraction(1, 20)) * 100
        got = round(ux.contrast(fg, bg) * 100)
        want = math.floor(exact)
        assert got in (want, want + 1), (fg, bg, got, float(exact))
        if got == want + 1:
            assert float(want + 1 - exact) < 1e-11, (fg, bg, got, float(exact))
        for t in thresholds:
            if exact < t * 100 - Fraction(1, 10**11):
                assert got < t * 100, (fg, bg, float(exact))


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"fg": "#000000"}, "bg"),
        ({"bg": "#ffffff"}, "fg"),
        ({"fg": "#00", "bg": "#ffffff"}, "fg"),  # below minLength 4
        ({"fg": "#000000", "bg": "#"}, "bg"),
        ({"fg": 123456, "bg": "#ffffff"}, "fg"),
        ({"fg": "#000000", "bg": "#ffffff", "extra": 1}, "extra"),
    ],
)
def test_contrast_schema_violations_name_the_field(payload, field):
    invalid(run("ux_contrast", payload), field)


def test_contrast_non_hex_digits_are_invalid_input_and_nothing_is_computed():
    result = run("ux_contrast", {"fg": "#zzzz", "bg": "#ffffff"})
    invalid(result, "fg")
    assert "ratio" not in result


def test_contrast_unauthorized_callers_are_refused():
    unauthorized("ux_contrast", {"fg": "#000000", "bg": "#ffffff"})


def test_contrast_invalid_bg_is_reported_against_bg():
    invalid(run("ux_contrast", {"fg": "#ffffff", "bg": "#zzzz"}), "bg")


def test_contrast_a_pair_below_the_3_to_1_floor_is_not_reported_as_3():
    # #959595 on white is 2.9953:1, which fails WCAG 1.4.11; the rounded result 3.0 would pass a `>= 3` check
    assert run("ux_contrast", {"fg": "#959595", "bg": "#ffffff"})["ratio"] < 3


@pytest.mark.parametrize("bad", ["#12345", "#abcdefg", "#-fffff", "#+fffff", "#ff ff", "#fffff"])
def test_contrast_malformed_hex_is_rejected(bad):
    invalid(run("ux_contrast", {"fg": bad, "bg": "#ffffff"}), "fg")
    invalid(run("ux_contrast", {"fg": "#ffffff", "bg": bad}), "bg")


# --- ux_token_name_check ----------------------------------------------------------------------------------------


def token_names(names, max_depth=4, **extra):
    return run("ux_token_name_check", {"names": names, "max_depth": max_depth, **extra})


def test_token_names_conforming_set_is_clean():
    result = token_names(["color.background.primary", "color.text.default", "space.scale-4"], roots=["color", "space"])
    assert result == {"state": "SUCCESS", "checked": 3, "invalid": [], "duplicates": [], "conforming": True}


def test_token_names_depth_boundary_equal_passes_one_over_fails():
    assert token_names(["a.b.c"], max_depth=3)["conforming"] is True
    result = token_names(["a.b.c.d"], max_depth=3)
    assert result["invalid"] == ["a.b.c.d: depth 4 exceeds max_depth 3"] and result["conforming"] is False


def test_token_names_kebab_case_rules_per_segment():
    result = token_names(["Color.bg", "color.2xl", "color.a--b", "color.tail-", "color.under_score", "color.ok-1"])
    assert result["invalid"] == [
        "Color.bg: segment 'Color' is not kebab-case",
        "color.2xl: segment '2xl' is not kebab-case",
        "color.a--b: segment 'a--b' is not kebab-case",
        "color.tail-: segment 'tail-' is not kebab-case",
        "color.under_score: segment 'under_score' is not kebab-case",
    ]


def test_token_names_empty_segments_are_invalid():
    result = token_names(["a..b", ".a", "a."], max_depth=5)
    assert [item.split(":")[0] for item in result["invalid"]] == ["a..b", ".a", "a."]
    assert all("is not kebab-case" in item for item in result["invalid"])


def test_token_names_roots_are_enforced_only_when_given():
    assert token_names(["motion.fast"])["conforming"] is True
    result = token_names(["motion.fast", "color.red"], roots=["color"])
    assert result["invalid"] == ["motion.fast: root 'motion' is not in roots"]


def test_token_names_duplicates_are_sorted_and_listed_once():
    result = token_names(["b.x", "a.y", "b.x", "a.y", "b.x", "c.z"])
    assert result["duplicates"] == ["a.y", "b.x"] and result["invalid"] == [] and result["conforming"] is False
    assert result["checked"] == 6


def test_token_names_depth_is_reported_before_kebab_and_roots():
    result = token_names(["BAD.x.y"], max_depth=2, roots=["color"])
    assert result["invalid"] == ["BAD.x.y: depth 3 exceeds max_depth 2"]


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"names": [], "max_depth": 2}, "names"),
        ({"names": ["a"] * 501, "max_depth": 2}, "names"),
        ({"names": ["a"], "max_depth": 0}, "max_depth"),
        ({"names": ["a"], "max_depth": 17}, "max_depth"),
        ({"names": ["a"], "max_depth": 2, "roots": []}, "roots"),
        ({"names": [""], "max_depth": 2}, "names"),
        ({"names": ["a" * 129], "max_depth": 2}, "names"),
        ({"names": ["a"]}, "max_depth"),
    ],
)
def test_token_names_schema_violations_name_the_field(payload, field):
    invalid(run("ux_token_name_check", payload), field)


def test_token_names_direct_handler_matches_registry():
    payload = {"names": ["a.b", "A.b"], "max_depth": 2}
    assert handlers().token_name_check(payload) == run("ux_token_name_check", payload)
    unauthorized("ux_token_name_check", payload)


# --- ux_target_size_check ---------------------------------------------------------------------------------------


def target(tid, width, height, clearance):
    return {"id": tid, "width_px": width, "height_px": height, "center_to_nearest_edge_px": clearance}


def sizes(*targets):
    return run("ux_target_size_check", {"targets": list(targets)})


def test_target_size_registry_example_buckets_each_target():
    result = sizes(target("btn-primary", 44, 44, 40), target("icon-close", 16, 16, 14), target("link-tiny", 16, 16, 6))
    assert result == {
        "state": "SUCCESS", "total": 3, "meets_minimum": 1,
        "spacing_exception": ["icon-close"], "failing": ["link-tiny"], "compliant": False,
    }


def test_target_size_exactly_24_by_24_meets_the_minimum_whatever_the_clearance():
    result = sizes(target("a", 24, 24, 0))
    assert result["meets_minimum"] == 1 and result["failing"] == [] and result["compliant"] is True


def test_target_size_one_short_dimension_is_undersized():
    result = sizes(target("wide", 200, 23.99, 0), target("tall", 23.99, 200, 12))
    assert result["meets_minimum"] == 0 and result["failing"] == ["wide"] and result["spacing_exception"] == ["tall"]


def test_target_size_clearance_boundary_is_12_px():
    result = sizes(target("on", 10, 10, 12), target("off", 10, 10, 11.99))
    assert result["spacing_exception"] == ["on"] and result["failing"] == ["off"]


def test_target_size_all_exception_or_meets_is_compliant():
    result = sizes(target("a", 30, 30, 0), target("b", 8, 8, 20))
    assert result["compliant"] is True and result["total"] == 2


def test_target_size_preserves_input_order_in_buckets():
    result = sizes(target("z", 1, 1, 0), target("a", 1, 1, 0), target("m", 1, 1, 30), target("b", 1, 1, 30))
    assert result["failing"] == ["z", "a"] and result["spacing_exception"] == ["m", "b"]


def test_target_size_zero_dimensions_are_valid_and_failing_without_clearance():
    result = sizes(target("point", 0, 0, 0))
    assert result["failing"] == ["point"] and result["compliant"] is False


def test_target_size_duplicate_ids_are_invalid_input_listing_them_sorted():
    result = sizes(target("b", 24, 24, 0), target("a", 24, 24, 0), target("b", 1, 1, 0), target("a", 1, 1, 0))
    invalid(result, "targets")
    assert result["message"] == "duplicate ids: a, b"


@pytest.mark.parametrize(
    "payload",
    [
        {"targets": []},
        {"targets": [{"id": "a", "width_px": 24}]},
        {"targets": [target("a", -1, 1, 1)]},
        {"targets": [target("a", 10001, 1, 1)]},
        {"targets": [target("", 24, 24, 0)]},
        {"targets": [{**target("a", 24, 24, 0), "extra": 1}]},
        {"targets": [target("a", "24", 24, 0)]},
        {"targets": [target(str(i), 24, 24, 0) for i in range(201)]},
    ],
)
def test_target_size_schema_violations_are_invalid_input_on_targets(payload):
    result = run("ux_target_size_check", payload)
    invalid(result, "targets")


def test_target_size_unauthorized_callers_are_refused():
    unauthorized("ux_target_size_check", {"targets": [target("a", 24, 24, 0)]})


# --- ux_scale_monotonic_check -----------------------------------------------------------------------------------


def scale(hexes, order="light_to_dark"):
    steps = [{"step": str(i), "hex": h} for i, h in enumerate(hexes)]
    return run("ux_scale_monotonic_check", {"steps": steps, "order": order})


def test_scale_registry_example_is_monotonic_with_rounded_luminance():
    steps = [{"step": "50", "hex": "#f5f7fa"}, {"step": "100", "hex": "#e4e9f2"}, {"step": "500", "hex": "#4a6fa5"}, {"step": "900", "hex": "#0d1b2a"}]
    result = run("ux_scale_monotonic_check", {"steps": steps, "order": "light_to_dark"})
    assert result["state"] == "SUCCESS" and result["checked"] == 4 and result["monotonic"] is True and result["violations"] == []
    assert result["luminance"] == sorted(result["luminance"], reverse=True)
    assert all(v == round(v, 4) for v in result["luminance"])


def test_scale_white_and_black_luminance_are_one_and_zero():
    result = scale(["#ffffff", "#000000"])
    assert result["luminance"] == [1.0, 0.0] and result["monotonic"] is True


def test_scale_wrong_direction_reports_each_adjacent_violation():
    result = scale(["#000000", "#808080", "#ffffff"], order="light_to_dark")
    assert result["monotonic"] is False and result["violations"] == ["0->1", "1->2"]
    assert scale(["#000000", "#808080", "#ffffff"], order="dark_to_light")["violations"] == []


def test_scale_one_reversal_in_the_middle_is_the_only_violation():
    result = scale(["#ffffff", "#444444", "#888888", "#000000"])
    assert result["violations"] == ["1->2"] and result["monotonic"] is False


def test_scale_equal_luminance_is_not_strictly_monotonic():
    result = scale(["#fff", "#ffffff"])
    assert result["luminance"] == [1.0, 1.0] and result["violations"] == ["0->1"]
    assert scale(["#fff", "#ffffff"], order="dark_to_light")["violations"] == ["0->1"]


def test_scale_hash_less_and_three_digit_hex_are_accepted():
    assert scale(["fff", "#000"])["monotonic"] is True


@pytest.mark.parametrize("bad", ["#ffff", "#fffff", "#zzzzzz", "#12", "gg0000", "#ffffffff"])
def test_scale_malformed_hex_names_step_and_value(bad):
    result = run("ux_scale_monotonic_check", {"steps": [{"step": "ok", "hex": "#fff"}, {"step": "bad", "hex": bad}], "order": "light_to_dark"})
    invalid(result, "steps")
    if len(bad) >= 3:
        assert "'bad'" in result["message"]


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"steps": [{"step": "a", "hex": "#fff"}], "order": "light_to_dark"}, "steps"),
        ({"steps": [{"step": "a", "hex": "#fff"}] * 25, "order": "light_to_dark"}, "steps"),
        ({"steps": [{"step": "a", "hex": "#fff"}, {"step": "b", "hex": "#000"}], "order": "sideways"}, "order"),
        ({"steps": [{"step": "a", "hex": "#fff"}, {"step": "b", "hex": "#000"}]}, "order"),
        ({"order": "light_to_dark"}, "steps"),
    ],
)
def test_scale_schema_violations_name_the_field(payload, field):
    invalid(run("ux_scale_monotonic_check", payload), field)


def test_scale_unauthorized_callers_are_refused():
    unauthorized("ux_scale_monotonic_check", {"steps": [{"step": "a", "hex": "#fff"}, {"step": "b", "hex": "#000"}], "order": "light_to_dark"})


# --- ux_spacing_scale_check -------------------------------------------------------------------------------------


def spacing(base, values):
    return run("ux_spacing_scale_check", {"base_px": base, "values_px": values})


def test_spacing_registry_example_is_on_scale_with_multiples():
    result = spacing(4, [4, 8, 12, 16, 24, 40])
    assert result == {"state": "SUCCESS", "checked": 6, "multiples": [1, 2, 3, 4, 6, 10], "off_scale": [], "on_scale": True}


def test_spacing_off_scale_values_are_listed_in_input_order():
    result = spacing(4, [4, 6, 8, 10, 12])
    assert result["multiples"] == [1, 2, 3] and result["off_scale"] == [6, 10] and result["on_scale"] is False
    assert result["checked"] == 5


def test_spacing_zero_is_on_scale_as_multiple_zero():
    result = spacing(8, [0, 8])
    assert result["multiples"] == [0, 1] and result["on_scale"] is True


def test_spacing_fractional_base_uses_tolerance_not_exact_float_equality():
    # 0.3 / 0.1 is 2.9999999999999996 in floating point
    result = spacing(0.1, [0.1, 0.3, 0.7, 4000.1])
    assert result["off_scale"] == [] and result["multiples"] == [1, 3, 7, 40001]
    assert spacing(0.1, [0.35])["off_scale"] == [0.35]


def test_spacing_half_unit_is_off_scale_and_just_over_unit_too():
    result = spacing(4, [2, 4.01, 3.99, 4.0])
    assert result["off_scale"] == [2, 4.01, 3.99] and result["multiples"] == [1]


def test_spacing_value_smaller_than_base_is_off_scale_unless_zero():
    assert spacing(8, [4])["off_scale"] == [4]


def test_spacing_duplicates_are_kept_per_value():
    result = spacing(4, [8, 8, 9, 9])
    assert result["multiples"] == [2, 2] and result["off_scale"] == [9, 9] and result["checked"] == 4


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"base_px": 0, "values_px": [1]}, "base_px"),
        ({"base_px": -4, "values_px": [1]}, "base_px"),
        ({"base_px": 65, "values_px": [1]}, "base_px"),
        ({"base_px": 4, "values_px": []}, "values_px"),
        ({"base_px": 4, "values_px": [1] * 129}, "values_px"),
        ({"base_px": 4, "values_px": [-1]}, "values_px"),
        ({"base_px": 4, "values_px": [4097]}, "values_px"),
        ({"values_px": [1]}, "base_px"),
        ({"base_px": 4}, "values_px"),
    ],
)
def test_spacing_schema_violations_name_the_field(payload, field):
    invalid(run("ux_spacing_scale_check", payload), field)


def test_spacing_direct_handler_matches_registry_and_caller_is_checked():
    payload = {"base_px": 4, "values_px": [4, 5]}
    assert handlers().spacing_scale_check(payload) == run("ux_spacing_scale_check", payload)
    unauthorized("ux_spacing_scale_check", payload)


# --- ux_state_coverage_check ------------------------------------------------------------------------------------


def coverage(required, declared):
    return run("ux_state_coverage_check", {"required_states": required, "declared_states": declared})


def test_state_coverage_registry_example_sub_states_cover_their_family():
    result = coverage(
        ["default", "loading", "error", "empty", "success"],
        ["default", "loading", "error.network", "error.validation", "empty.cart", "success"],
    )
    assert result == {"state": "SUCCESS", "required_count": 5, "covered_count": 5, "missing": [], "unexpected": [], "complete": True}


def test_state_coverage_missing_states_keep_required_order():
    result = coverage(["default", "loading", "error", "empty"], ["default"])
    assert result["missing"] == ["loading", "error", "empty"] and result["covered_count"] == 1 and result["complete"] is False


def test_state_coverage_unexpected_are_declared_states_outside_every_family():
    result = coverage(["default", "error"], ["default", "error.network", "hover", "errors"])
    assert result["unexpected"] == ["hover", "errors"] and result["missing"] == [] and result["complete"] is True


def test_state_coverage_prefix_without_dot_does_not_cover():
    result = coverage(["error"], ["errors", "error-state"])
    assert result["missing"] == ["error"] and result["unexpected"] == ["errors", "error-state"]


def test_state_coverage_a_parent_does_not_cover_a_required_sub_state():
    result = coverage(["error.network"], ["error"])
    assert result["missing"] == ["error.network"] and result["unexpected"] == ["error"]


def test_state_coverage_is_case_and_whitespace_insensitive_and_dedupes_required():
    result = coverage(["Default", "  default ", "ERROR"], ["DEFAULT", " Error.Server "])
    assert result["required_count"] == 2 and result["covered_count"] == 2 and result["complete"] is True
    assert result["unexpected"] == []


def test_state_coverage_nothing_declared_misses_everything():
    result = coverage(["a", "b"], [])
    assert result["missing"] == ["a", "b"] and result["covered_count"] == 0 and result["unexpected"] == []


def test_state_coverage_blank_required_state_is_invalid_input():
    invalid(coverage(["default", "   "], ["default"]), "required_states")


def test_state_coverage_blank_declared_state_is_invalid_input():
    invalid(coverage(["default"], ["default", "  "]), "declared_states")


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"required_states": [], "declared_states": []}, "required_states"),
        ({"required_states": [""], "declared_states": []}, "required_states"),
        ({"required_states": ["a"] * 33, "declared_states": []}, "required_states"),
        ({"required_states": ["a"], "declared_states": ["b"] * 65}, "declared_states"),
        ({"required_states": ["a"]}, "declared_states"),
        ({"declared_states": ["a"]}, "required_states"),
    ],
)
def test_state_coverage_schema_violations_name_the_field(payload, field):
    invalid(run("ux_state_coverage_check", payload), field)


def test_state_coverage_unauthorized_callers_are_refused():
    unauthorized("ux_state_coverage_check", {"required_states": ["a"], "declared_states": []})


# --- ux_focus_contrast_check ------------------------------------------------------------------------------------


def focus(focus_hex, surfaces, ratio=3):
    return run("ux_focus_contrast_check", {"focus_hex": focus_hex, "surface_hexes": surfaces, "required_ratio": ratio})


def test_focus_contrast_passes_when_every_surface_meets_the_floor():
    result = focus("#000000", ["#ffffff"], ratio=3)
    assert result == {"state": "SUCCESS", "checked": 1, "ratios": [21.0], "lowest_ratio": 21.0, "failing_surfaces": [], "meets": True}


def test_focus_contrast_fails_on_the_worst_surface_and_names_it_verbatim():
    result = focus("#000000", ["#ffffff", "#000", "#777777"], ratio=3)
    assert result["ratios"] == [21.0, 1.0, 4.69] and result["lowest_ratio"] == 1.0
    assert result["failing_surfaces"] == ["#000"] and result["meets"] is False


def test_focus_contrast_exact_floor_passes_because_comparison_is_inclusive():
    assert focus("#000000", ["#ffffff"], ratio=21)["meets"] is True
    assert focus("#ffffff", ["#ffffff"], ratio=1)["meets"] is True


def test_focus_contrast_compares_the_unrounded_ratio_with_the_floor():
    # #959595 on white is 2.9953:1: displayed as 3.0, but it still fails a 3:1 floor
    result = focus("#959595", ["#ffffff"], ratio=3)
    assert result["ratios"] == [3.0] and result["meets"] is False and result["failing_surfaces"] == ["#ffffff"]


def test_focus_contrast_registry_example_dark_blue_on_light_surfaces():
    result = focus("#1a56db", ["#ffffff", "#f3f4f6"], ratio=3)
    assert result["meets"] is True and result["checked"] == 2
    assert result["ratios"][0] > result["ratios"][1] >= result["lowest_ratio"] == result["ratios"][1]


def test_focus_contrast_is_symmetric_in_colour_order():
    assert focus("#ffffff", ["#000000"])["ratios"] == focus("#000000", ["#ffffff"])["ratios"]


def test_focus_contrast_short_and_hash_less_hex_are_accepted():
    assert focus("000", ["fff"])["ratios"] == [21.0]


def test_focus_contrast_invalid_focus_colour_names_focus_hex():
    invalid(focus("#zzz", ["#ffffff"]), "focus_hex")


def test_focus_contrast_invalid_surface_colour_names_surface_hexes():
    invalid(focus("#000", ["#ffffff", "#fffff"]), "surface_hexes")


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"focus_hex": "#000", "surface_hexes": [], "required_ratio": 3}, "surface_hexes"),
        ({"focus_hex": "#000", "surface_hexes": ["#fff"] * 9, "required_ratio": 3}, "surface_hexes"),
        ({"focus_hex": "#000", "surface_hexes": ["#fff"], "required_ratio": 0.5}, "required_ratio"),
        ({"focus_hex": "#000", "surface_hexes": ["#fff"], "required_ratio": 22}, "required_ratio"),
        ({"focus_hex": "#0", "surface_hexes": ["#fff"], "required_ratio": 3}, "focus_hex"),
        ({"surface_hexes": ["#fff"], "required_ratio": 3}, "focus_hex"),
        ({"focus_hex": "#000", "surface_hexes": ["#fff"]}, "required_ratio"),
    ],
)
def test_focus_contrast_schema_violations_name_the_field(payload, field):
    invalid(run("ux_focus_contrast_check", payload), field)


def test_focus_contrast_unauthorized_callers_are_refused():
    unauthorized("ux_focus_contrast_check", {"focus_hex": "#000", "surface_hexes": ["#fff"], "required_ratio": 3})


# --- ux_motion_duration_check -----------------------------------------------------------------------------------


def anim(name, ms, reduced=True):
    return {"name": name, "duration_ms": ms, "has_reduced_motion_variant": reduced}


def motion(limit, *animations):
    return run("ux_motion_duration_check", {"max_duration_ms": limit, "animations": list(animations)})


def test_motion_registry_example_is_compliant():
    result = motion(300, anim("menu-slide", 200), anim("toast-fade", 150))
    assert result == {
        "state": "SUCCESS", "checked": 2, "max_observed_ms": 200,
        "over_limit": [], "without_reduced_motion": [], "compliant": True,
    }


def test_motion_duration_equal_to_the_limit_is_allowed_one_over_is_not():
    assert motion(300, anim("a", 300))["compliant"] is True
    result = motion(300, anim("a", 300), anim("b", 300.5))
    assert result["over_limit"] == ["b"] and result["compliant"] is False and result["max_observed_ms"] == 300.5


def test_motion_missing_reduced_motion_variant_alone_makes_it_non_compliant():
    result = motion(500, anim("fast", 10, reduced=False), anim("ok", 10))
    assert result["without_reduced_motion"] == ["fast"] and result["over_limit"] == [] and result["compliant"] is False


def test_motion_zero_duration_still_needs_a_reduced_variant():
    result = motion(0, anim("instant", 0, reduced=False))
    assert result["over_limit"] == [] and result["without_reduced_motion"] == ["instant"] and result["compliant"] is False
    assert motion(0, anim("instant", 0))["compliant"] is True


def test_motion_reports_both_problems_in_input_order():
    result = motion(100, anim("z", 400, False), anim("a", 50), anim("m", 101, False))
    assert result["over_limit"] == ["z", "m"] and result["without_reduced_motion"] == ["z", "m"]
    assert result["max_observed_ms"] == 400 and result["checked"] == 3


def test_motion_max_observed_is_the_maximum_not_the_last():
    assert motion(60000, anim("a", 900), anim("b", 100))["max_observed_ms"] == 900


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"max_duration_ms": 300, "animations": []}, "animations"),
        ({"max_duration_ms": -1, "animations": [anim("a", 1)]}, "max_duration_ms"),
        ({"max_duration_ms": 60001, "animations": [anim("a", 1)]}, "max_duration_ms"),
        ({"max_duration_ms": 300, "animations": [anim("a", -1)]}, "animations"),
        ({"max_duration_ms": 300, "animations": [{"name": "a", "duration_ms": 1}]}, "animations"),
        ({"max_duration_ms": 300, "animations": [anim("a", 1, reduced="yes")]}, "animations"),
        ({"max_duration_ms": 300, "animations": [anim("", 1)]}, "animations"),
        ({"animations": [anim("a", 1)]}, "max_duration_ms"),
    ],
)
def test_motion_schema_violations_name_the_field(payload, field):
    invalid(run("ux_motion_duration_check", payload), field)


def test_motion_direct_handler_matches_registry_and_caller_is_checked():
    payload = {"max_duration_ms": 100, "animations": [anim("a", 200, False)]}
    assert handlers().motion_duration_check(payload) == run("ux_motion_duration_check", payload)
    unauthorized("ux_motion_duration_check", payload)


# --- ux_breakpoint_order_check ----------------------------------------------------------------------------------


def bp(name, width):
    return {"name": name, "min_width_px": width}


def breakpoints(*items):
    return run("ux_breakpoint_order_check", {"breakpoints": list(items)})


def test_breakpoints_registry_example_is_valid_and_starts_at_zero():
    result = breakpoints(bp("base", 0), bp("sm", 640), bp("md", 768), bp("lg", 1024))
    assert result == {
        "state": "SUCCESS", "count": 4, "valid": True, "starts_at_zero": True,
        "out_of_order": [], "duplicate_names": [], "sorted_names": ["base", "sm", "md", "lg"],
    }


def test_breakpoints_a_single_breakpoint_is_valid():
    result = breakpoints(bp("only", 0))
    assert result["valid"] is True and result["count"] == 1 and result["sorted_names"] == ["only"]


def test_breakpoints_equal_widths_are_out_of_order_and_name_the_later_one():
    result = breakpoints(bp("sm", 640), bp("md", 640))
    assert result["out_of_order"] == ["md"] and result["valid"] is False


def test_breakpoints_descending_input_lists_each_offending_name_and_sorts_ascending():
    result = breakpoints(bp("lg", 1024), bp("md", 768), bp("sm", 640))
    assert result["out_of_order"] == ["md", "sm"] and result["sorted_names"] == ["sm", "md", "lg"] and result["valid"] is False


def test_breakpoints_duplicate_names_are_reported_sorted_once_and_invalidate():
    result = breakpoints(bp("b", 0), bp("a", 100), bp("b", 200), bp("a", 300), bp("b", 400))
    assert result["duplicate_names"] == ["a", "b"] and result["out_of_order"] == [] and result["valid"] is False


def test_breakpoints_starts_at_zero_only_looks_at_the_first_entry():
    assert breakpoints(bp("sm", 640), bp("md", 768))["starts_at_zero"] is False
    assert breakpoints(bp("base", 0), bp("sm", 640))["starts_at_zero"] is True
    assert breakpoints(bp("sm", 640), bp("base", 0))["starts_at_zero"] is False


def test_breakpoints_ties_in_sorted_names_break_alphabetically():
    assert breakpoints(bp("b", 1), bp("a", 1))["sorted_names"] == ["a", "b"]


@pytest.mark.parametrize(
    "payload",
    [
        {"breakpoints": []},
        {"breakpoints": [bp(str(i), i) for i in range(13)]},
        {"breakpoints": [bp("a", 0.5)]},
        {"breakpoints": [bp("a", -1)]},
        {"breakpoints": [bp("a", 10001)]},
        {"breakpoints": [bp("", 0)]},
        {"breakpoints": [{"name": "a"}]},
        {"breakpoints": [{**bp("a", 0), "extra": 1}]},
    ],
)
def test_breakpoints_schema_violations_are_invalid_input_on_breakpoints(payload):
    invalid(run("ux_breakpoint_order_check", payload), "breakpoints")


def test_breakpoints_missing_list_names_the_field():
    invalid(run("ux_breakpoint_order_check", {}), "breakpoints")


def test_breakpoints_unauthorized_callers_are_refused():
    unauthorized("ux_breakpoint_order_check", {"breakpoints": [bp("a", 0)]})


# --- scripts/ux_tokens.py: invalid colours are findings, never a crash for the whole file -----------------------------


def _ux_script():
    return importlib.import_module("swarm.tools.support").script("ux_tokens")


_TOKENS = {"color": {"$type": "color", "background": {"$value": "#ffffff"}, "text": {"$value": "#767676\n"},
                     "ok": {"$value": "#000000"}}}


def test_token_file_with_trailing_newline_colour_is_a_finding_and_other_tokens_are_still_checked(tmp_path):
    """RV-H P3-2: HEX_RE accepted '#767676\\n' ('$'), hex_to_rgb rejected it, check_file raised ValueError."""
    import argparse
    import json

    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(_TOKENS), encoding="utf-8")
    findings: list = []
    ux = _ux_script()
    report = ux.check_file(path, argparse.Namespace(background=None, min_ratio=4.5), findings, lambda: f"UF-{len(findings) + 1:03d}")
    assert [(f["kind"], f["severity"]) for f in findings] == [("color", "major")]
    assert "color.text" in findings[0]["summary"] and "invalid colour" in findings[0]["summary"]
    assert [(p["fg"], p["ratio"]) for p in report["contrast"]] == [("color.ok", 21.0)]


@pytest.mark.parametrize("value", ["#767676\n", "#767676\r", "#767676 ", "#76767", "#gggggg", "767676", "#٧٦٧٦٧٦"])
def test_hex_validation_is_fullmatch_and_agrees_with_hex_to_rgb(value):
    ux = _ux_script()
    assert ux.HEX_RE.fullmatch(value) is None
    assert ux._is_hex(value) is False


def test_a_value_that_passes_hex_validation_never_raises_in_contrast(tmp_path, monkeypatch):
    """Second layer: even if the validator and hex_to_rgb disagreed, check_file reports a finding instead of raising."""
    import argparse
    import json

    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(_TOKENS), encoding="utf-8")
    ux = _ux_script()
    monkeypatch.setattr(ux, "_is_hex", lambda value: True)
    findings: list = []
    report = ux.check_file(path, argparse.Namespace(background=None, min_ratio=4.5), findings, lambda: f"UF-{len(findings) + 1:03d}")
    assert [f["kind"] for f in findings] == ["color"] and "invalid hex colour" in findings[0]["summary"]
    assert [p["fg"] for p in report["contrast"]] == ["color.ok"]
