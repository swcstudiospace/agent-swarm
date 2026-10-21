"""A04 v2.2 tools: ux_token_semver_impact, ux_spec_completeness_check, ux_ac_trace_check.

Calls go through the registry (input schema, caller check, output schema) against the loaded document plus this
unit's intake fragment, so the tests pass before and after the fragment is merged into agents.json.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm.tool_registry import call_tool, load_document, validate_registry

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a04-ux-designer.tools.json"
NEW_NAMES = ("ux_token_semver_impact", "ux_spec_completeness_check", "ux_ac_trace_check")


def _entries() -> list[dict]:
    """The new tool entries: the intake fragment until it is applied, afterwards agents.json itself (the fragment is deleted)."""
    if FRAGMENT.exists():
        return json.loads(FRAGMENT.read_text(encoding="utf-8"))
    agent = next(a for a in load_document()["agents"] if a["id"] == "A04")
    by_name = {t["name"]: t for t in agent["registered_tools"]}
    return [by_name[name] for name in NEW_NAMES]


def _merged() -> dict:
    doc = copy.deepcopy(load_document())
    agent = next(a for a in doc["agents"] if a["id"] == "A04")
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(t for t in _entries() if t["name"] not in present)
    return doc


@pytest.fixture(scope="module")
def registry() -> dict:
    return _merged()


def semver(registry, **payload):
    return call_tool("ux_token_semver_impact", "A04", payload, data=registry)


def spec_check(registry, **payload):
    return call_tool("ux_spec_completeness_check", "A04", payload, data=registry)


def trace(registry, **payload):
    return call_tool("ux_ac_trace_check", "A04", payload, data=registry)


# --- registry ---------------------------------------------------------------------------------------------------


def test_fragment_merged_into_a_copy_validates(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(_merged()), encoding="utf-8")
    assert validate_registry(path) == []


def test_new_entries_follow_the_a04_contract():
    entries = _entries()
    assert [e["name"] for e in entries] == list(NEW_NAMES)
    for entry in entries:
        assert entry["owner"] == "A04" and entry["permitted_callers"] == ["A04"]
        assert entry["side_effect_tier"] == "READ" and entry["idempotency"] == "idempotent"
        assert entry["handler"] == "swarm.tools.a04_tools:" + entry["name"][3:]
        assert "|" not in entry["purpose"]
        assert set(entry["examples"]) == {"success", "invalid_input", "unauthorized_caller", "dependency", "timeout", "partial"}


@pytest.mark.parametrize("name", NEW_NAMES)
def test_registry_examples_resolve_to_their_states(registry, name):
    tool = next(t for t in next(a for a in registry["agents"] if a["id"] == "A04")["registered_tools"] if t["name"] == name)
    ex = tool["examples"]
    assert call_tool(name, "A04", ex["success"], data=registry)["state"] == "SUCCESS"
    assert call_tool(name, "A04", ex["invalid_input"], data=registry)["state"] == "INVALID_INPUT"
    assert call_tool(name, ex["unauthorized_caller"], ex["success"], data=registry)["state"] == "UNAUTHORIZED"
    assert call_tool(name, "A04", ex["dependency"], data=registry)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call_tool(name, "A04", ex["partial"], data=registry)["state"] == "PARTIAL_SUCCESS"
    assert call_tool(name, "A04", ex["timeout"], data=registry, timeout_s=0.05)["state"] == "TIMEOUT"


# --- ux_token_semver_impact --------------------------------------------------------------------------------------

OLD = {"color.text.default": "#111111", "space.scale-4": "16px", "color.brand.legacy": "#ff0000"}


@pytest.mark.parametrize(
    ("new", "aliases", "bump", "nxt", "l3"),
    [
        (dict(OLD), None, "patch", "1.4.1", False),
        ({**OLD, "space.scale-5": "20px"}, None, "minor", "1.5.0", False),
        ({**OLD, "color.text.default": "#222222"}, None, "major", "2.0.0", True),
        ({k: v for k, v in OLD.items() if k != "color.brand.legacy"}, {"color.brand.legacy": "color.text.default"}, "major", "2.0.0", True),
    ],
)
def test_semver_bump_table(registry, new, aliases, bump, nxt, l3):
    payload = {"old": OLD, "new": new, "current_version": "1.4.0"}
    if aliases:
        payload["aliases"] = aliases
    out = semver(registry, **payload)
    assert out["state"] == "SUCCESS"
    assert (out["required_bump"], out["next_version"], out["l3_required"]) == (bump, nxt, l3)


def test_semver_empty_diff_is_patch_and_flagged_empty(registry):
    out = semver(registry, old=OLD, new=dict(OLD), current_version="1.4.0")
    assert out["diff_empty"] is True and out["breaking"] == [] and out["required_bump"] == "patch"


def test_semver_removal_without_alias_is_listed(registry):
    new = {k: v for k, v in OLD.items() if k != "color.brand.legacy"}
    out = semver(registry, old=OLD, new=new, current_version="1.4.0")
    assert out["removed"] == ["color.brand.legacy"] and out["unaliased_removals"] == ["color.brand.legacy"]


def test_semver_alias_to_a_token_that_does_not_exist_still_counts_as_unaliased(registry):
    new = {k: v for k, v in OLD.items() if k != "color.brand.legacy"}
    out = semver(registry, old=OLD, new=new, current_version="1.4.0", aliases={"color.brand.legacy": "color.nowhere"})
    assert out["unaliased_removals"] == ["color.brand.legacy"]


def test_semver_value_change_is_breaking_not_removed(registry):
    out = semver(registry, old={"a": "1"}, new={"a": "2", "b": "3"}, current_version="0.9.9")
    assert (out["changed"], out["added"], out["removed"], out["breaking"]) == (["a"], ["b"], [], ["a"])
    assert out["next_version"] == "1.0.0"


def test_semver_bool_and_int_values_differ(registry):
    out = semver(registry, old={"flag": True}, new={"flag": 1}, current_version="1.0.0")
    assert out["changed"] == ["flag"]


@pytest.mark.parametrize("version", ["1.4", "v1.4.0", "1.4.0-rc.1", "01.4.0", "1.4.0+build", "a.b.c"])
def test_semver_rejects_non_semver_current_version(registry, version):
    out = semver(registry, old=OLD, new=OLD, current_version=version)
    assert out["state"] == "INVALID_INPUT" and out["field"] == "current_version"


def test_semver_rejects_composite_values_and_blank_names(registry):
    assert semver(registry, old={"type.body": {"size": 16}}, new={}, current_version="1.0.0")["field"] == "old"
    assert semver(registry, old={}, new={"  ": "x"}, current_version="1.0.0")["field"] == "new"
    assert semver(registry, old={}, new={"a": None}, current_version="1.0.0")["field"] == "new"


def test_semver_rejects_oversized_maps_and_bad_alias_targets(registry):
    big = {f"t{i}": "x" for i in range(5001)}
    assert semver(registry, old=big, new={}, current_version="1.0.0")["field"] == "old"
    assert semver(registry, old={"a": "1"}, new={}, current_version="1.0.0", aliases={"a": ""})["field"] == "aliases"
    assert semver(registry, old={}, new={}, current_version="1.0.0", aliases={"a": 3})["field"] == "aliases"


def test_semver_missing_field_is_invalid_input_naming_it(registry):
    out = semver(registry, old=OLD, new=OLD)
    assert out["state"] == "INVALID_INPUT" and out["field"] == "current_version"


# --- ux_spec_completeness_check ----------------------------------------------------------------------------------

GOLDEN = {
    "screen_id": "SCR-Checkout",
    "flows": ["guest", "authenticated"],
    "states": ["default", "loading", "error.network", "error.validation", "empty.cart", "success"],
    "components": [{"ref": "ds/Button@2.1", "props": {"variant": "primary"}}],
    "a11y": {"target": "WCAG-2.2-AA", "focus_order": ["email", "password", "submit"], "min_contrast": 4.5},
    "tokens_version": "1.4.0",
    "fidelity": "hifi",
    "provisional": False,
    "acceptance_refs": ["AC-7", "AC-8"],
}
REQUIRED = ["default", "loading", "error", "empty", "success"]
INTERACTIVE = ["email", "password", "submit"]


def mutated(**changes):
    spec = copy.deepcopy(GOLDEN)
    for key, value in changes.items():
        if value is ...:
            spec.pop(key, None)
        else:
            spec[key] = value
    return spec


def check(registry, spec, **extra):
    return spec_check(registry, spec=spec, required_states=REQUIRED, interactive_ids=INTERACTIVE, **extra)


def test_golden_spec_is_complete(registry):
    out = check(registry, GOLDEN)
    assert out["state"] == "SUCCESS" and out["complete"] is True
    assert out["missing_fields"] == [] and out["missing_states"] == []


@pytest.mark.parametrize(
    ("spec", "field", "expected"),
    [
        (mutated(screen_id=...), "missing_fields", ["screen_id"]),
        (mutated(flows=[]), "missing_fields", ["flows"]),
        (mutated(components=[{"props": {}}]), "missing_fields", ["components[0].ref"]),
        (mutated(provisional="no"), "missing_fields", ["provisional"]),
        (mutated(fidelity="medium"), "bad_enums", ["fidelity: expected one of hifi, lofi, got 'medium'"]),
        (mutated(tokens_version="1.4"), "bad_enums", ["tokens_version: expected MAJOR.MINOR.PATCH, got '1.4'"]),
        (mutated(states=["default", "loading", "error.network", "empty.cart"]), "missing_states", ["success"]),
        (mutated(acceptance_refs=[]), "empty_acceptance_refs", True),
    ],
)
def test_each_defect_flips_exactly_its_field(registry, spec, field, expected):
    out = check(registry, spec)
    assert out[field] == expected
    assert out["complete"] is False
    for other in ("missing_fields", "bad_enums", "missing_states", "focus_order_unknown", "focus_order_missing"):
        if other != field:
            assert out[other] == []
    for flag in ("empty_acceptance_refs", "below_floor"):
        if flag != field:
            assert out[flag] is False


def test_dotted_substates_cover_their_family_and_other_substates_do_not(registry):
    spec = mutated(states=["default", "loading", "error.network", "empty.cart", "successful"])
    assert check(registry, spec)["missing_states"] == ["success"]


def test_focus_order_unknown_and_missing_are_reported_against_interactive_ids(registry):
    spec = mutated(a11y={**GOLDEN["a11y"], "focus_order": ["email", "ghost", "submit"]})
    out = check(registry, spec)
    assert out["focus_order_unknown"] == ["ghost"] and out["focus_order_missing"] == ["password"]


def test_focus_checks_are_skipped_when_interactive_ids_are_not_given(registry):
    out = spec_check(registry, spec=GOLDEN, required_states=REQUIRED)
    assert out["complete"] is True and out["focus_order_unknown"] == [] and out["focus_order_missing"] == []


def test_empty_interactive_ids_means_a_non_interactive_screen(registry):
    out = spec_check(registry, spec=GOLDEN, required_states=REQUIRED, interactive_ids=[])
    assert out["focus_order_unknown"] == ["email", "password", "submit"]


def test_contrast_floor_boundary(registry):
    assert check(registry, GOLDEN, min_contrast_floor=4.5)["below_floor"] is False
    assert check(registry, GOLDEN, min_contrast_floor=4.6)["below_floor"] is True
    below = mutated(a11y={**GOLDEN["a11y"], "min_contrast": 3.0})
    assert check(registry, below)["below_floor"] is True


def test_a11y_block_missing_or_incomplete(registry):
    assert check(registry, mutated(a11y=...))["missing_fields"] == ["a11y"]
    partial = mutated(a11y={"target": "WCAG-2.1-A"})
    out = check(registry, partial)
    assert out["missing_fields"] == ["a11y.focus_order", "a11y.min_contrast"]
    assert out["bad_enums"] == ["a11y.target: expected one of WCAG-2.2-AA, WCAG-2.2-AAA, got 'WCAG-2.1-A'"]


def test_spec_input_errors_name_the_field(registry):
    assert spec_check(registry, spec=GOLDEN, required_states=[" "])["field"] == "required_states"
    assert spec_check(registry, spec=GOLDEN, required_states=REQUIRED, interactive_ids=["a", "a"])["field"] == "interactive_ids"
    assert spec_check(registry, spec=GOLDEN, required_states=REQUIRED, interactive_ids=[" "])["field"] == "interactive_ids"
    assert spec_check(registry, spec=[], required_states=REQUIRED)["field"] == "spec"
    assert spec_check(registry, spec=GOLDEN, required_states=[])["field"] == "required_states"
    assert spec_check(registry, spec=GOLDEN, required_states=REQUIRED, min_contrast_floor=22)["field"] == "min_contrast_floor"


def test_empty_spec_reports_every_required_field_without_crashing(registry):
    out = spec_check(registry, spec={}, required_states=["default"])
    assert out["state"] == "SUCCESS" and out["complete"] is False
    assert {"screen_id", "flows", "states", "components", "a11y", "tokens_version", "fidelity", "provisional", "acceptance_refs"} <= set(
        out["missing_fields"]
    )
    assert out["missing_states"] == ["default"]


# --- ux_ac_trace_check ------------------------------------------------------------------------------------------


def test_trace_fully_covered(registry):
    out = trace(
        registry,
        acceptance_ids=["AC-7", "AC-8"],
        screens=[{"screen_id": "SCR-Checkout", "acceptance_refs": ["AC-7", "AC-8"]}],
    )
    assert out["traced"] is True and out["coverage_pct"] == 100.0
    assert out["uncovered_acs"] == [] and out["unknown_refs"] == [] and out["screens_without_refs"] == []


def test_trace_reports_uncovered_unknown_and_orphan_screens(registry):
    out = trace(
        registry,
        acceptance_ids=["AC-1", "AC-2", "AC-3"],
        screens=[
            {"screen_id": "SCR-A", "acceptance_refs": ["AC-1", "AC-99"]},
            {"screen_id": "SCR-B", "acceptance_refs": []},
        ],
    )
    assert out["uncovered_acs"] == ["AC-2", "AC-3"]
    assert out["unknown_refs"] == ["SCR-A: AC-99"]
    assert out["screens_without_refs"] == ["SCR-B"]
    assert out["coverage_pct"] == 33.33 and out["traced"] is False


def test_trace_counts_an_ac_cited_by_two_screens_once(registry):
    out = trace(
        registry,
        acceptance_ids=["AC-1", "AC-2"],
        screens=[{"screen_id": "S1", "acceptance_refs": ["AC-1", "AC-1"]}, {"screen_id": "S2", "acceptance_refs": ["AC-1"]}],
    )
    assert out["uncovered_acs"] == ["AC-2"] and out["coverage_pct"] == 50.0


def test_trace_is_case_sensitive_and_trims_whitespace(registry):
    out = trace(registry, acceptance_ids=["AC-1"], screens=[{"screen_id": "S", "acceptance_refs": [" AC-1 ", "ac-1"]}])
    assert out["unknown_refs"] == ["S: ac-1"] and out["uncovered_acs"] == []


def test_trace_rejects_duplicate_and_blank_ids(registry):
    dup_screen = trace(registry, acceptance_ids=["AC-1"], screens=[{"screen_id": "S", "acceptance_refs": ["AC-1"]}] * 2)
    assert dup_screen["state"] == "INVALID_INPUT" and dup_screen["field"] == "screens"
    assert trace(registry, acceptance_ids=["AC-1", "AC-1"], screens=[{"screen_id": "S", "acceptance_refs": []}])["field"] == "acceptance_ids"
    assert trace(registry, acceptance_ids=[" "], screens=[{"screen_id": "S", "acceptance_refs": []}])["field"] == "acceptance_ids"
    assert trace(registry, acceptance_ids=["AC-1"], screens=[{"screen_id": " ", "acceptance_refs": []}])["field"] == "screens"
    assert trace(registry, acceptance_ids=["AC-1"], screens=[{"screen_id": "S", "acceptance_refs": [" "]}])["field"] == "screens"


def test_trace_empty_and_oversized_input_is_rejected_by_schema(registry):
    assert trace(registry, acceptance_ids=[], screens=[{"screen_id": "S", "acceptance_refs": []}])["state"] == "INVALID_INPUT"
    assert trace(registry, acceptance_ids=["AC-1"], screens=[])["state"] == "INVALID_INPUT"
    many = [f"AC-{i}" for i in range(501)]
    assert trace(registry, acceptance_ids=many, screens=[{"screen_id": "S", "acceptance_refs": []}])["state"] == "INVALID_INPUT"
    assert trace(registry, acceptance_ids=["AC-1"], screens=[{"screen_id": "S"}])["state"] == "INVALID_INPUT"


def test_trace_only_a04_may_call_it(registry):
    out = call_tool("ux_ac_trace_check", "A05", {"acceptance_ids": ["AC-1"], "screens": [{"screen_id": "S", "acceptance_refs": []}]}, data=registry)
    assert out["state"] == "UNAUTHORIZED"


# --- review fix-ups (RV-C) ---------------------------------------------------------------------------------------


def test_semver_trailing_newline_is_not_a_version(registry):
    """RV-C P3: _SEMVER ended in `$`, so current_version "1.2.3\\n" passed."""
    out = semver(registry, old=OLD, new=dict(OLD), current_version="1.4.0\n")
    assert out["state"] == "INVALID_INPUT" and out["field"] == "current_version"


def test_spec_tokens_version_with_trailing_newline_is_flagged(registry):
    """RV-C P3: the same regex let tokens_version "1.4.0\\n" through ux_spec_completeness_check."""
    out = check(registry, mutated(tokens_version="1.4.0\n"))
    assert out["complete"] is False and len(out["bad_enums"]) == 1 and out["bad_enums"][0].startswith("tokens_version")
