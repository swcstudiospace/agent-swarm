"""Behaviour of the v2.1 A11 lane tools: env_tier, change_gate, drift_count, cost_overrun, image_tag_check,
freeze_window, ci_kind and build_systems. Each test asserts exact output values for specific inputs."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _registry_module():
    # conftest purges swarm* modules between tests, so resolve the registry through the current module graph.
    return importlib.import_module("swarm.tool_registry")


@pytest.fixture(scope="module")
def registry() -> dict:
    return _registry_module().load_document()


def _call(registry: dict, name: str, payload: dict, caller: str = "A11") -> dict:
    return _registry_module().call_tool(name, caller, payload, data=registry)


def _handler(module: str, name: str):
    return getattr(importlib.import_module(f"swarm.tools.{module}"), name)


def _invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result


def _kv(**pairs: str) -> list[dict]:
    return [{"key": key, "value": value} for key, value in pairs.items()]


# ---- devops_env_tier ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["prod", "production", "prd", "PROD", "Production-EU", "app-prod-eu", "prod2", "svc_prd_1"])
def test_env_tier_production_names_get_l3(registry, name):
    assert _call(registry, "devops_env_tier", {"env_name": name}) == {"state": "SUCCESS", "tier": "production", "autonomy": "L3"}


@pytest.mark.parametrize("name", ["staging", "stg", "stage", "preprod", "Staging_1", "eu-stg-3", "STAGE"])
def test_env_tier_staging_names_get_l2(registry, name):
    assert _call(registry, "devops_env_tier", {"env_name": name}) == {"state": "SUCCESS", "tier": "staging", "autonomy": "L2"}


@pytest.mark.parametrize("name", ["ephemeral", "preview", "pr-1234", "feature_pr_7", "Preview-abc", "ephemeral-ab12"])
def test_env_tier_ephemeral_names_get_l2(registry, name):
    assert _call(registry, "devops_env_tier", {"env_name": name}) == {"state": "SUCCESS", "tier": "ephemeral", "autonomy": "L2"}


@pytest.mark.parametrize(
    "name, tier",
    [("prod-staging", "production"), ("stg-prod", "production"), ("prod-pr-12", "production"), ("stg-preview", "staging"), ("pr-stage", "staging")],
)
def test_env_tier_mixed_names_resolve_to_the_stricter_tier(registry, name, tier):
    assert _call(registry, "devops_env_tier", {"env_name": name})["tier"] == tier


@pytest.mark.parametrize("name", ["product", "reproduction", "dev", "qa", "proxy", "xprod", "stagefright", "123", "---", "prefix"])
def test_env_tier_names_without_a_whole_word_marker_are_rejected(registry, name):
    result = _call(registry, "devops_env_tier", {"env_name": name})
    _invalid(result, "env_name")
    assert "marker" in result["message"]


def test_env_tier_name_length_boundary_is_128(registry):
    at_limit = "prod-" + "x" * 123
    assert len(at_limit) == 128
    assert _call(registry, "devops_env_tier", {"env_name": at_limit})["tier"] == "production"
    _invalid(_call(registry, "devops_env_tier", {"env_name": at_limit + "x"}), "env_name")


@pytest.mark.parametrize(
    "payload, field",
    [({}, "env_name"), ({"env_name": ""}, "env_name"), ({"env_name": 7}, "env_name"), ({"env_name": None}, "env_name"), ({"env_name": "prod", "tier": "x"}, "tier")],
)
def test_env_tier_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_env_tier", payload), field)


def test_env_tier_is_closed_to_other_agents_and_direct_call_matches(registry):
    assert _call(registry, "devops_env_tier", {"env_name": "prod"}, caller="A01")["state"] == "UNAUTHORIZED"
    assert _handler("a11_tools", "env_tier")({"env_name": "stg"}) == {"state": "SUCCESS", "tier": "staging", "autonomy": "L2"}


# ---- devops_change_gate ------------------------------------------------------------------------------------------

_FULL = {"change_ticket": "CHG-1", "blast_radius": "api only", "rollback_plan": "helm rollback"}


@pytest.mark.parametrize("tier", ["ephemeral", "staging"])
def test_change_gate_allows_non_production_without_any_evidence(registry, tier):
    assert _call(registry, "devops_change_gate", {"env_tier": tier}) == {"state": "SUCCESS", "decision": "allowed", "missing": ""}
    blank = {"change_ticket": "", "blast_radius": "  ", "rollback_plan": "\n"}
    assert _call(registry, "devops_change_gate", {"env_tier": tier, **blank})["decision"] == "allowed"


def test_change_gate_production_with_full_evidence_still_needs_a_human(registry):
    assert _call(registry, "devops_change_gate", {"env_tier": "production", **_FULL}) == {
        "state": "SUCCESS",
        "decision": "needs-human-approval",
        "missing": "",
    }


def test_change_gate_production_without_evidence_names_all_three_in_order(registry):
    result = _call(registry, "devops_change_gate", {"env_tier": "production"})
    assert result == {"state": "SUCCESS", "decision": "blocked", "missing": "change_ticket, blast_radius, rollback_plan"}


@pytest.mark.parametrize(
    "absent, expected",
    [
        ("change_ticket", "change_ticket"),
        ("blast_radius", "blast_radius"),
        ("rollback_plan", "rollback_plan"),
    ],
)
def test_change_gate_each_single_missing_field_blocks(registry, absent, expected):
    payload = {"env_tier": "production", **{k: v for k, v in _FULL.items() if k != absent}}
    assert _call(registry, "devops_change_gate", payload) == {"state": "SUCCESS", "decision": "blocked", "missing": expected}


@pytest.mark.parametrize("blank", ["", " ", "\t", "\n", "  \r\n\t "])
def test_change_gate_whitespace_only_evidence_counts_as_missing(registry, blank):
    payload = {"env_tier": "production", "change_ticket": blank, "blast_radius": "x", "rollback_plan": "y"}
    assert _call(registry, "devops_change_gate", payload)["missing"] == "change_ticket"


def test_change_gate_two_missing_fields_keep_the_fixed_order(registry):
    payload = {"env_tier": "production", "change_ticket": "CHG-1", "blast_radius": " ", "rollback_plan": ""}
    assert _call(registry, "devops_change_gate", payload)["missing"] == "blast_radius, rollback_plan"


def test_change_gate_evidence_length_boundaries(registry):
    ok = {"env_tier": "production", "change_ticket": "c" * 64, "blast_radius": "b" * 2000, "rollback_plan": "r" * 2000}
    assert _call(registry, "devops_change_gate", ok)["decision"] == "needs-human-approval"
    _invalid(_call(registry, "devops_change_gate", {**ok, "change_ticket": "c" * 65}), "change_ticket")
    _invalid(_call(registry, "devops_change_gate", {**ok, "blast_radius": "b" * 2001}), "blast_radius")
    _invalid(_call(registry, "devops_change_gate", {**ok, "rollback_plan": "r" * 2001}), "rollback_plan")


@pytest.mark.parametrize(
    "payload, field",
    [
        ({}, "env_tier"),
        ({"env_tier": "prod"}, "env_tier"),
        ({"env_tier": "Production"}, "env_tier"),
        ({"env_tier": 1}, "env_tier"),
        ({"env_tier": "production", "blast_radius": None}, "blast_radius"),
        ({"env_tier": "production", "change_ticket": 5}, "change_ticket"),
        ({"env_tier": "production", "bogus": 1}, "bogus"),
    ],
)
def test_change_gate_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_change_gate", payload), field)


def test_change_gate_is_closed_to_other_agents(registry):
    assert _call(registry, "devops_change_gate", {"env_tier": "staging"}, caller="A12")["state"] == "UNAUTHORIZED"


# ---- devops_drift_count ------------------------------------------------------------------------------------------


def test_drift_count_identical_state_is_no_drift_whatever_the_streak(registry):
    state = _kv(a="1", b="2")
    for streak in (0, 2, 1000):
        result = _call(registry, "devops_drift_count", {"desired": state, "actual": list(reversed(state)), "drift_streak": streak})
        assert result == {"state": "SUCCESS", "drift_count": 0, "drifted_keys": "", "action": "none"}


def test_drift_count_reports_changed_missing_and_unexpected_keys_sorted(registry):
    payload = {"desired": _kv(a="1", b="2", c="3"), "actual": _kv(a="1", b="9", z="3"), "drift_streak": 0}
    assert _call(registry, "devops_drift_count", payload) == {
        "state": "SUCCESS",
        "drift_count": 3,
        "drifted_keys": "b, c, z",
        "action": "auto-reconcile",
    }


def test_drift_count_each_drift_kind_alone(registry):
    base = _kv(a="1")
    missing = _call(registry, "devops_drift_count", {"desired": base, "actual": [], "drift_streak": 0})
    unexpected = _call(registry, "devops_drift_count", {"desired": [], "actual": base, "drift_streak": 0})
    changed = _call(registry, "devops_drift_count", {"desired": base, "actual": _kv(a="2"), "drift_streak": 0})
    for result in (missing, unexpected, changed):
        assert (result["drift_count"], result["drifted_keys"]) == (1, "a")


@pytest.mark.parametrize("streak, action", [(0, "auto-reconcile"), (1, "auto-reconcile"), (2, "freeze-and-escalate"), (3, "freeze-and-escalate"), (1000, "freeze-and-escalate")])
def test_drift_count_third_consecutive_drift_freezes_and_escalates(registry, streak, action):
    payload = {"desired": _kv(a="1"), "actual": [], "drift_streak": streak}
    assert _call(registry, "devops_drift_count", payload)["action"] == action


def test_drift_count_values_compare_exactly_and_keys_are_case_sensitive(registry):
    exact = _call(registry, "devops_drift_count", {"desired": _kv(a=""), "actual": _kv(a=" "), "drift_streak": 0})
    assert (exact["drift_count"], exact["drifted_keys"]) == (1, "a")
    cased = _call(registry, "devops_drift_count", {"desired": _kv(A="1"), "actual": _kv(a="1"), "drift_streak": 0})
    assert (cased["drift_count"], cased["drifted_keys"]) == (2, "A, a")


def test_drift_count_list_order_does_not_matter(registry):
    forward = {"desired": _kv(b="1", a="1"), "actual": _kv(c="1"), "drift_streak": 0}
    backward = {"desired": _kv(a="1", b="1"), "actual": _kv(c="1"), "drift_streak": 0}
    assert _call(registry, "devops_drift_count", forward) == _call(registry, "devops_drift_count", backward)
    assert _call(registry, "devops_drift_count", forward)["drifted_keys"] == "a, b, c"


def test_drift_count_duplicate_keys_are_rejected_naming_the_side(registry):
    dup = [{"key": "a", "value": "1"}, {"key": "a", "value": "1"}]
    desired = _call(registry, "devops_drift_count", {"desired": dup, "actual": [], "drift_streak": 0})
    actual = _call(registry, "devops_drift_count", {"desired": [], "actual": dup, "drift_streak": 0})
    _invalid(desired, "desired")
    _invalid(actual, "actual")
    assert "unique" in desired["message"] and "unique" in actual["message"]
    both = _call(registry, "devops_drift_count", {"desired": dup, "actual": dup, "drift_streak": 0})
    _invalid(both, "desired")


def test_drift_count_size_boundaries(registry):
    many = [{"key": f"k{i}", "value": "v"} for i in range(200)]
    assert _call(registry, "devops_drift_count", {"desired": many, "actual": many, "drift_streak": 1000})["drift_count"] == 0
    over = many + [{"key": "extra", "value": "v"}]
    _invalid(_call(registry, "devops_drift_count", {"desired": over, "actual": [], "drift_streak": 0}), "desired")
    _invalid(_call(registry, "devops_drift_count", {"desired": [], "actual": over, "drift_streak": 0}), "actual")
    _invalid(_call(registry, "devops_drift_count", {"desired": [], "actual": [], "drift_streak": 1001}), "drift_streak")
    _invalid(_call(registry, "devops_drift_count", {"desired": [], "actual": [], "drift_streak": -1}), "drift_streak")


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"actual": [], "drift_streak": 0}, "desired"),
        ({"desired": [], "drift_streak": 0}, "actual"),
        ({"desired": [], "actual": []}, "drift_streak"),
        ({"desired": [{"key": "", "value": "1"}], "actual": [], "drift_streak": 0}, "desired"),
        ({"desired": [{"key": "a"}], "actual": [], "drift_streak": 0}, "desired"),
        ({"desired": [], "actual": [{"key": "a", "value": 1}], "drift_streak": 0}, "actual"),
        ({"desired": [], "actual": [], "drift_streak": True}, "drift_streak"),
        ({"desired": [], "actual": [], "drift_streak": "1"}, "drift_streak"),
        ({"desired": "a=1", "actual": [], "drift_streak": 0}, "desired"),
    ],
)
def test_drift_count_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_drift_count", payload), field)


def test_drift_count_is_closed_to_other_agents(registry):
    payload = {"desired": [], "actual": [], "drift_streak": 0}
    assert _call(registry, "devops_drift_count", payload, caller="A03")["state"] == "UNAUTHORIZED"


# ---- devops_cost_overrun -----------------------------------------------------------------------------------------


def test_cost_overrun_exactly_120_percent_is_not_an_overrun(registry):
    assert _call(registry, "devops_cost_overrun", {"modeled_cost": 100, "actual_cost": 120}) == {
        "state": "SUCCESS",
        "ratio": 1.2,
        "overrun": False,
        "action": "none",
    }


def test_cost_overrun_just_above_120_percent_downscales(registry):
    result = _call(registry, "devops_cost_overrun", {"modeled_cost": 100, "actual_cost": 120.01})
    assert result == {"state": "SUCCESS", "ratio": 1.2001, "overrun": True, "action": "auto-downscale"}
    result = _call(registry, "devops_cost_overrun", {"modeled_cost": 100, "actual_cost": 120.0000001})
    assert result["overrun"] is True and result["action"] == "auto-downscale"


@pytest.mark.parametrize("modeled, actual", [(0.1, 0.12), (0.7, 0.84), (0.3, 0.36), (1.1, 1.32), (2.5, 3.0)])
def test_cost_overrun_decimal_boundary_has_no_binary_float_residue(registry, modeled, actual):
    result = _call(registry, "devops_cost_overrun", {"modeled_cost": modeled, "actual_cost": actual})
    assert result["overrun"] is False and result["action"] == "none"


@pytest.mark.parametrize(
    "modeled, actual, ratio",
    [(3, 1, 0.3333), (2, 1, 0.5), (3, 4, 1.3333), (1, 0, 0.0), (10, 10, 1.0), (7, 2, 0.2857), (1, 1.23456, 1.2346)],
)
def test_cost_overrun_ratio_is_rounded_to_four_places(registry, modeled, actual, ratio):
    assert _call(registry, "devops_cost_overrun", {"modeled_cost": modeled, "actual_cost": actual})["ratio"] == ratio


def test_cost_overrun_under_budget_and_zero_spend_take_no_action(registry):
    for actual in (0, 0.5, 99.99):
        result = _call(registry, "devops_cost_overrun", {"modeled_cost": 100, "actual_cost": actual})
        assert result["overrun"] is False and result["action"] == "none"


def test_cost_overrun_large_integers_compare_exactly_at_the_threshold(registry):
    modeled = 10**14  # the schema caps costs at 1e15, so exactness is exercised at the largest realistic magnitude
    assert _call(registry, "devops_cost_overrun", {"modeled_cost": modeled, "actual_cost": modeled * 12 // 10})["overrun"] is False
    assert _call(registry, "devops_cost_overrun", {"modeled_cost": modeled, "actual_cost": modeled * 12 // 10 + 1})["overrun"] is True


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"modeled_cost": 0, "actual_cost": 1}, "modeled_cost"),
        ({"modeled_cost": -5, "actual_cost": 1}, "modeled_cost"),
        ({"modeled_cost": 1, "actual_cost": -0.01}, "actual_cost"),
        ({"modeled_cost": "100", "actual_cost": 1}, "modeled_cost"),
        ({"modeled_cost": True, "actual_cost": 1}, "modeled_cost"),
        ({"modeled_cost": 1, "actual_cost": "1"}, "actual_cost"),
        ({"modeled_cost": 1, "actual_cost": None}, "actual_cost"),
        ({"actual_cost": 1}, "modeled_cost"),
        ({"modeled_cost": 1}, "actual_cost"),
        ({"modeled_cost": 1, "actual_cost": 1, "currency": "usd"}, "currency"),
    ],
)
def test_cost_overrun_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_cost_overrun", payload), field)


def test_cost_overrun_handler_guards_a_zero_baseline_itself():
    result = _handler("a11_tools", "cost_overrun")({"modeled_cost": 0, "actual_cost": 1})
    assert result == {"state": "INVALID_INPUT", "field": "modeled_cost", "message": "must be above zero"}


def test_cost_overrun_is_closed_to_other_agents(registry):
    assert _call(registry, "devops_cost_overrun", {"modeled_cost": 1, "actual_cost": 1}, caller="A13")["state"] == "UNAUTHORIZED"


def test_cost_overrun_bounds_inputs_and_refuses_a_non_finite_ratio(registry):
    # schema maximum 1e15 refuses the oversized actual cost; the denormal modeled cost overflows the ratio to inf
    _invalid(_call(registry, "devops_cost_overrun", {"modeled_cost": 5e-324, "actual_cost": 1e300}), "actual_cost")
    _invalid(_call(registry, "devops_cost_overrun", {"modeled_cost": 1e300, "actual_cost": 1}), "modeled_cost")
    result = _call(registry, "devops_cost_overrun", {"modeled_cost": 5e-324, "actual_cost": 1e15})
    _invalid(result, "modeled_cost")
    json.dumps(result, allow_nan=False)
    assert _call(registry, "devops_cost_overrun", {"modeled_cost": 1e15, "actual_cost": 1e15})["ratio"] == 1.0


# ---- devops_image_tag_check --------------------------------------------------------------------------------------

_DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    "ref, verdict, reason, tag",
    [
        ("app:1.2.3", "pass", "version-tag", "1.2.3"),
        ("app:v1.2.3", "pass", "version-tag", "v1.2.3"),
        ("app:v1.2.3-rc.1", "pass", "version-tag", "v1.2.3-rc.1"),
        ("app:1.2.3+build5", "pass", "version-tag", "1.2.3+build5"),
        ("reg.io/team/app:2.0.0", "pass", "version-tag", "2.0.0"),
        ("localhost:5000/app:1.2.3", "pass", "version-tag", "1.2.3"),
        ("oci://reg.io/app:1.2.3", "pass", "version-tag", "1.2.3"),
        ("app:abc1234", "pass", "commit-sha-tag", "abc1234"),
        ("app:" + "0123456789abcdef0123456789abcdef01234567", "pass", "commit-sha-tag", "0123456789abcdef0123456789abcdef01234567"),
    ],
)
def test_image_tag_check_version_and_commit_tags_pass(registry, ref, verdict, reason, tag):
    assert _call(registry, "devops_image_tag_check", {"image_ref": ref}) == {"state": "SUCCESS", "verdict": verdict, "reason": reason, "tag": tag}


@pytest.mark.parametrize("tag", ["latest", "LATEST", "main", "master", "dev", "develop", "edge", "nightly", "stable", "head", "canary", "staging", "prod", "production", "Current"])
def test_image_tag_check_mutable_tags_fail_case_insensitively(registry, tag):
    assert _call(registry, "devops_image_tag_check", {"image_ref": f"app:{tag}"}) == {
        "state": "SUCCESS",
        "verdict": "fail",
        "reason": "mutable-tag",
        "tag": tag,
    }


@pytest.mark.parametrize("ref", ["nginx", "localhost:5000/app", "reg.io/team/app", "app:", "oci://reg.io/app"])
def test_image_tag_check_missing_tag_fails_with_an_empty_tag(registry, ref):
    assert _call(registry, "devops_image_tag_check", {"image_ref": ref}) == {"state": "SUCCESS", "verdict": "fail", "reason": "no-tag", "tag": ""}


@pytest.mark.parametrize(
    "ref, tag",
    [
        ("app:1.2", "1.2"),
        ("app:3", "3"),
        ("app:feature-x", "feature-x"),
        ("app:abcdef", "abcdef"),  # six hex characters is shorter than a commit abbreviation
        ("app:" + "a" * 41, "a" * 41),  # longer than a full sha
        ("app:ABC1234", "ABC1234"),  # commit tags are lowercase
        ("localhost:5000", "5000"),  # a bare host:port reads as repo:tag
    ],
)
def test_image_tag_check_unrecognized_tags_fail(registry, ref, tag):
    assert _call(registry, "devops_image_tag_check", {"image_ref": ref}) == {
        "state": "SUCCESS",
        "verdict": "fail",
        "reason": "unrecognized-tag",
        "tag": tag,
    }


def test_image_tag_check_digest_pins_pass_and_keep_the_tag(registry):
    assert _call(registry, "devops_image_tag_check", {"image_ref": f"app@{_DIGEST}"}) == {
        "state": "SUCCESS", "verdict": "pass", "reason": "digest-pinned", "tag": "",
    }
    assert _call(registry, "devops_image_tag_check", {"image_ref": f"reg.io/app:latest@{_DIGEST}"}) == {
        "state": "SUCCESS", "verdict": "pass", "reason": "digest-pinned", "tag": "latest",
    }
    assert _call(registry, "devops_image_tag_check", {"image_ref": f"oci://app:1.0.0@{_DIGEST}"})["tag"] == "1.0.0"


@pytest.mark.parametrize(
    "digest",
    ["sha256:" + "A" * 64, "sha256:" + "a" * 63, "sha256:" + "a" * 65, "sha512:" + "a" * 128, "sha256:" + "g" * 64, "", "sha256:"],
)
def test_image_tag_check_malformed_digest_is_invalid_input(registry, digest):
    result = _call(registry, "devops_image_tag_check", {"image_ref": f"app@{digest}"})
    _invalid(result, "image_ref")
    assert "digest" in result["message"]


@pytest.mark.parametrize("ref", ["@" + _DIGEST, ":latest", "oci://", "oci://@" + _DIGEST])
def test_image_tag_check_empty_repository_is_invalid_input(registry, ref):
    result = _call(registry, "devops_image_tag_check", {"image_ref": ref})
    _invalid(result, "image_ref")
    assert "repository" in result["message"] or "digest" in result["message"]


@pytest.mark.parametrize("ref", ["app 1.0", "app:1.0.0\n", " app:1.0.0", "app:1.0.0\t", "app:\u00a01.0.0"])
def test_image_tag_check_whitespace_anywhere_is_invalid_input(registry, ref):
    result = _call(registry, "devops_image_tag_check", {"image_ref": ref})
    _invalid(result, "image_ref")
    assert result["message"] == "must not contain whitespace"


def test_image_tag_check_reference_length_boundary_is_512(registry):
    ref = "a" * 506 + ":1.2.3"
    assert len(ref) == 512
    assert _call(registry, "devops_image_tag_check", {"image_ref": ref})["reason"] == "version-tag"
    _invalid(_call(registry, "devops_image_tag_check", {"image_ref": "a" + ref}), "image_ref")


@pytest.mark.parametrize("payload, field", [({}, "image_ref"), ({"image_ref": ""}, "image_ref"), ({"image_ref": None}, "image_ref"), ({"image_ref": ["app:1.0.0"]}, "image_ref"), ({"image_ref": "app:1.0.0", "x": 1}, "x")])
def test_image_tag_check_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_image_tag_check", payload), field)


def test_image_tag_check_is_closed_to_other_agents(registry):
    assert _call(registry, "devops_image_tag_check", {"image_ref": "app:1.0.0"}, caller="A05")["state"] == "UNAUTHORIZED"


def test_image_tag_check_rejects_non_ascii_digit_version_tags(registry):
    result = _call(registry, "devops_image_tag_check", {"image_ref": "app:\u0661.\u0662.\u0663"})
    assert result["verdict"] == "fail"


# ---- devops_freeze_window ----------------------------------------------------------------------------------------

_WIN = {"start": "2026-03-01T10:00:00Z", "end": "2026-03-01T12:00:00Z"}


def _freeze(registry: dict, now: str, windows: list[dict]) -> dict:
    return _call(registry, "devops_freeze_window", {"now": now, "windows": windows})


def test_freeze_window_inside_a_window_reports_index_and_remaining_seconds(registry):
    assert _freeze(registry, "2026-03-01T11:00:00Z", [_WIN]) == {"state": "SUCCESS", "frozen": True, "window_index": 0, "remaining_s": 3600}


def test_freeze_window_start_is_inclusive_and_end_is_exclusive(registry):
    assert _freeze(registry, "2026-03-01T10:00:00Z", [_WIN]) == {"state": "SUCCESS", "frozen": True, "window_index": 0, "remaining_s": 7200}
    assert _freeze(registry, "2026-03-01T11:59:59Z", [_WIN])["remaining_s"] == 1
    assert _freeze(registry, "2026-03-01T12:00:00Z", [_WIN]) == {"state": "SUCCESS", "frozen": False, "window_index": -1, "remaining_s": 0}
    assert _freeze(registry, "2026-03-01T09:59:59Z", [_WIN])["frozen"] is False


def test_freeze_window_no_windows_is_not_frozen(registry):
    assert _freeze(registry, "2026-03-01T11:00:00Z", []) == {"state": "SUCCESS", "frozen": False, "window_index": -1, "remaining_s": 0}


def test_freeze_window_picks_the_matching_window_by_position(registry):
    windows = [
        {"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
        {"start": "2026-03-01T00:00:00Z", "end": "2026-03-01T01:00:00Z"},
        {"start": "2026-05-01T00:00:00Z", "end": "2026-05-02T00:00:00Z"},
    ]
    assert _freeze(registry, "2026-03-01T00:30:00Z", windows) == {"state": "SUCCESS", "frozen": True, "window_index": 1, "remaining_s": 1800}
    assert _freeze(registry, "2026-04-01T00:00:00Z", windows)["window_index"] == -1


def test_freeze_window_overlapping_windows_report_the_first_match(registry):
    windows = [
        {"start": "2026-03-01T10:00:00Z", "end": "2026-03-01T11:00:00Z"},
        {"start": "2026-03-01T10:30:00Z", "end": "2026-03-01T13:00:00Z"},
    ]
    assert _freeze(registry, "2026-03-01T10:45:00Z", windows) == {"state": "SUCCESS", "frozen": True, "window_index": 0, "remaining_s": 900}
    assert _freeze(registry, "2026-03-01T11:00:00Z", windows)["window_index"] == 1


def test_freeze_window_compares_instants_across_utc_offsets(registry):
    # 15:30+05:30 and 12:00+02:00 are both 10:00Z.
    assert _freeze(registry, "2026-03-01T15:30:00+05:30", [_WIN])["remaining_s"] == 7200
    offset_window = [{"start": "2026-03-01T12:00:00+02:00", "end": "2026-03-01T14:00:00+02:00"}]
    assert _freeze(registry, "2026-03-01T10:00:00Z", offset_window) == {"state": "SUCCESS", "frozen": True, "window_index": 0, "remaining_s": 7200}
    assert _freeze(registry, "2026-03-01T09:59:59Z", offset_window)["frozen"] is False
    # Local-looking midday in a far offset is a different instant: 11:00-05:00 is 16:00Z.
    assert _freeze(registry, "2026-03-01T11:00:00-05:00", [_WIN])["frozen"] is False


def test_freeze_window_accepts_lowercase_z_and_fractional_seconds_truncate_remaining(registry):
    assert _freeze(registry, "2026-03-01T11:00:00z", [_WIN])["frozen"] is True
    two_seconds = [{"start": "2026-03-01T10:00:00Z", "end": "2026-03-01T10:00:02Z"}]
    assert _freeze(registry, "2026-03-01T10:00:00.9Z", two_seconds)["remaining_s"] == 1


@pytest.mark.parametrize("now", ["2026-03-01T11:00:00", "2026-03-01", "not a time", "2026-13-01T00:00:00Z", "2026-03-01T24:00:00Z", "11:00:00Z"])
def test_freeze_window_now_without_an_offset_or_unparsable_is_invalid(registry, now):
    result = _freeze(registry, now, [_WIN])
    _invalid(result, "now")
    assert "UTC offset" in result["message"]


@pytest.mark.parametrize(
    "window",
    [
        {"start": "2026-03-01T10:00:00", "end": "2026-03-01T12:00:00Z"},
        {"start": "2026-03-01T10:00:00Z", "end": "2026-03-01T12:00:00"},
        {"start": "garbage", "end": "2026-03-01T12:00:00Z"},
        {"start": "2026-03-01T12:00:00Z", "end": "2026-03-01T12:00:00Z"},
        {"start": "2026-03-01T12:00:00Z", "end": "2026-03-01T10:00:00Z"},
        {"start": "2026-03-01T14:00:00+02:00", "end": "2026-03-01T11:00:00Z"},
    ],
)
def test_freeze_window_bad_or_empty_or_reversed_window_is_invalid_with_its_position(registry, window):
    result = _freeze(registry, "2026-03-01T11:00:00Z", [_WIN, window])
    _invalid(result, "windows")
    assert result["message"].startswith("window 1 ")


def test_freeze_window_a_bad_window_after_a_match_still_rejects_the_whole_call(registry):
    bad = {"start": "2026-03-01T12:00:00Z", "end": "2026-03-01T10:00:00Z"}
    _invalid(_freeze(registry, "2026-03-01T11:00:00Z", [_WIN, bad]), "windows")


def test_freeze_window_window_count_boundary_is_50(registry):
    windows = [{"start": f"2026-01-{day:02d}T00:00:00Z", "end": f"2026-01-{day:02d}T01:00:00Z"} for day in range(1, 29)] * 2
    assert _freeze(registry, "2026-01-05T00:30:00Z", windows[:50])["window_index"] == 4
    _invalid(_freeze(registry, "2026-01-05T00:30:00Z", windows[:50] + [windows[0]]), "windows")


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"windows": []}, "now"),
        ({"now": "2026-03-01T11:00:00Z"}, "windows"),
        ({"now": "", "windows": []}, "now"),
        ({"now": "2026-03-01T11:00:00Z" + " " * 30, "windows": []}, "now"),
        ({"now": "2026-03-01T11:00:00Z", "windows": [{"start": "2026-03-01T10:00:00Z"}]}, "windows"),
        ({"now": "2026-03-01T11:00:00Z", "windows": [{"start": "", "end": "x"}]}, "windows"),
        ({"now": "2026-03-01T11:00:00Z", "windows": "none"}, "windows"),
        ({"now": 5, "windows": []}, "now"),
    ],
)
def test_freeze_window_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_freeze_window", payload), field)


def test_freeze_window_is_closed_to_other_agents_and_direct_call_matches(registry):
    assert _call(registry, "devops_freeze_window", {"now": "2026-03-01T11:00:00Z", "windows": []}, caller="A12")["state"] == "UNAUTHORIZED"
    assert _handler("a11_tools", "freeze_window")({"now": "2026-03-01T11:00:00Z", "windows": [_WIN]})["remaining_s"] == 3600


# ---- devops_ci_kind ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel, kind",
    [
        (".github/workflows/ci.yml", "gha"),
        (".github/workflows/release.yaml", "gha"),
        ("sub/.github/workflows/x.yml", "gha"),
        (".gitlab-ci.yml", "gitlab"),
        ("sub/.gitlab-ci.yml", "gitlab"),
        ("Jenkinsfile", "jenkins"),
        ("Jenkinsfile.release", "jenkins"),
        ("Dockerfile", "dockerfile"),
        ("Dockerfile.dev", "dockerfile"),
        ("services/api/Dockerfile", "dockerfile"),
        ("app.Dockerfile", "dockerfile"),
        ("docker-compose.yml", "compose"),
        ("docker-compose.prod.yaml", "compose"),
        ("compose.yaml", "compose"),
        ("compose.override.yml", "compose"),
        ("docker-compose.dev.local.yml", "compose"),
        ("docker-compose.ci.override.yml", "compose"),
        ("docker-compose-dev.yml", "compose"),  # dash variant: the module docstring promises docker-compose*.yml
        ("compose.ci.override.yaml", "compose"),
        ("main.tf", "terraform"),
        ("infra/prod.tfvars", "terraform"),
    ],
)
def test_ci_kind_classifies_by_name_and_location(registry, tmp_path, monkeypatch, rel, kind):
    monkeypatch.chdir(tmp_path)  # none of these files exist: name rules never read the file
    assert _call(registry, "devops_ci_kind", {"rel": rel}) == {"state": "SUCCESS", "kind": kind}


@pytest.mark.parametrize(
    "rel",
    [
        ".github/workflows/readme.md",
        "github/workflows/ci.yml",
        ".github/ci.yml",
        "workflows/ci.yml",
        "README.md",
        "Makefile",
        "dockerfile",
        "docker-compose.json",
        "main.py",
        "missing.yaml",
        ".",
        "..",
    ],
)
def test_ci_kind_non_manifests_are_invalid_input(registry, tmp_path, monkeypatch, rel):
    monkeypatch.chdir(tmp_path)
    result = _call(registry, "devops_ci_kind", {"rel": rel})
    _invalid(result, "rel")
    assert result["message"] == "path is not a CI or IaC manifest"


def test_ci_kind_yaml_is_k8s_only_when_the_head_has_apiversion_and_kind(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pod.yaml").write_text("apiVersion: v1\nkind: Pod\nmetadata:\n  name: x\n", encoding="utf-8")
    (tmp_path / "only-version.yaml").write_text("apiVersion: v1\nspec: {}\n", encoding="utf-8")
    (tmp_path / "only-kind.yml").write_text("kind: Pod\n", encoding="utf-8")
    (tmp_path / "indented.yaml").write_text("  apiVersion: v1\n  kind: Pod\n", encoding="utf-8")
    (tmp_path / "plain.yaml").write_text("a: 1\n", encoding="utf-8")
    assert _call(registry, "devops_ci_kind", {"rel": "pod.yaml"}) == {"state": "SUCCESS", "kind": "k8s"}
    for rel in ("only-version.yaml", "only-kind.yml", "indented.yaml", "plain.yaml"):
        _invalid(_call(registry, "devops_ci_kind", {"rel": rel}), "rel")


def test_ci_kind_k8s_markers_past_the_first_4000_characters_are_ignored(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "late.yaml").write_text("# " + "x" * 4000 + "\napiVersion: v1\nkind: Pod\n", encoding="utf-8")
    (tmp_path / "edge.yaml").write_text("apiVersion: v1\n" + "#" * 3975 + "\nkind: Pod\n", encoding="utf-8")
    _invalid(_call(registry, "devops_ci_kind", {"rel": "late.yaml"}), "rel")
    assert _call(registry, "devops_ci_kind", {"rel": "edge.yaml"})["kind"] == "k8s"


def test_ci_kind_name_rules_win_over_yaml_content(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docker-compose.yml").write_text("apiVersion: v1\nkind: Pod\n", encoding="utf-8")
    assert _call(registry, "devops_ci_kind", {"rel": "docker-compose.yml"})["kind"] == "compose"


@pytest.mark.parametrize("payload, field", [({}, "rel"), ({"rel": ""}, "rel"), ({"rel": 3}, "rel"), ({"rel": None}, "rel"), ({"rel": "main.tf", "x": 1}, "x")])
def test_ci_kind_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_ci_kind", payload), field)


def test_ci_kind_is_closed_to_other_agents(registry):
    assert _call(registry, "devops_ci_kind", {"rel": "main.tf"}, caller="A10")["state"] == "UNAUTHORIZED"


@pytest.mark.parametrize("name", ["composer.yml", "composer.yaml", "compose-extra.yml", "docker-composer.yml"])
def test_ci_kind_does_not_treat_composer_yml_as_a_compose_file(registry, tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    result = _call(registry, "devops_ci_kind", {"rel": name})
    assert result.get("kind") != "compose"


@pytest.mark.parametrize("name", ["composed.yml", "compose_prod.yml", "compose-prod.yaml", "docker-compose_prod.yml",
                                  "docker-compose.yml.bak", "compose.yml.j2", "mycompose.yml"])
def test_ci_kind_compose_rule_needs_dots_unless_docker_prefixed_dash(registry, tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    assert _call(registry, "devops_ci_kind", {"rel": name}).get("kind") != "compose"


def test_ci_check_scans_multi_segment_and_dash_compose_files_for_secrets(tmp_path):
    import json
    import subprocess
    import sys

    names = ["docker-compose-dev.yml", "docker-compose.ci.override.yml", "docker-compose.dev.local.yml"]
    for name in names:
        (tmp_path / name).write_text('services:\n  db:\n    password: "Sup3rSecretValue123"\n', encoding="utf-8")
    (tmp_path / "compose-extra.yml").write_text('x:\n  password: "Sup3rSecretValue123"\n', encoding="utf-8")
    script = Path(__file__).resolve().parent.parent / "scripts" / "devops_ci_check.py"
    done = subprocess.run([sys.executable, str(script), "--root", str(tmp_path), "--task-id", "T-1",
                           "--correlation-id", "C-1", "--json"], capture_output=True, text=True, check=False)
    report = json.loads(done.stdout)
    assert report["status"] == "fail"
    secrets = sorted(f["location"] for f in report["findings"] if f["kind"] == "secret")
    assert [Path(s.split(":")[0]).name for s in secrets] == sorted(names)


# ---- devops_build_systems ----------------------------------------------------------------------------------------


def _build_systems(registry: dict) -> dict:
    return _call(registry, "devops_build_systems", {"scan": True})


def test_build_systems_empty_directory_finds_nothing(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _build_systems(registry) == {"state": "SUCCESS", "systems": ""}


@pytest.mark.parametrize(
    "marker, system",
    [
        ("Dockerfile", "docker"),
        ("pyproject.toml", "python-pyproject"),
        ("setup.py", "python-setuptools"),
        ("package.json", "node"),
        ("go.mod", "go"),
        ("Cargo.toml", "cargo"),
        ("pom.xml", "maven"),
        ("build.gradle", "gradle"),
        ("build.gradle.kts", "gradle"),
        ("Makefile", "make"),
        ("CMakeLists.txt", "cmake"),
        ("requirements.txt", "python-requirements"),
    ],
)
def test_build_systems_each_marker_maps_to_its_system(registry, tmp_path, monkeypatch, marker, system):
    monkeypatch.chdir(tmp_path)
    (tmp_path / marker).write_text("", encoding="utf-8")
    assert _build_systems(registry) == {"state": "SUCCESS", "systems": system}


def test_build_systems_lists_are_sorted_and_deduplicated(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("package.json", "build.gradle", "build.gradle.kts", "Dockerfile", "pyproject.toml", "README.md"):
        (tmp_path / name).write_text("", encoding="utf-8")
    assert _build_systems(registry)["systems"] == "docker,gradle,node,python-pyproject"


def test_build_systems_only_scans_the_workspace_root(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "dockerfile.txt").write_text("", encoding="utf-8")
    assert _build_systems(registry)["systems"] == ""
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert _build_systems(registry)["systems"] == "go"


def test_build_systems_markers_are_case_sensitive_names(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "makefile.bak").write_text("", encoding="utf-8")
    (tmp_path / "Cargo.toml.orig").write_text("", encoding="utf-8")
    assert _build_systems(registry)["systems"] == ""


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"scan": False}, "scan"),
        ({"scan": "yes"}, "scan"),
        ({"scan": 1}, "scan"),
        ({"scan": None}, "scan"),
        ({}, "scan"),
        ({"scan": True, "root": "/"}, "root"),
    ],
)
def test_build_systems_malformed_input_names_the_field(registry, payload, field):
    _invalid(_call(registry, "devops_build_systems", payload), field)


def test_build_systems_false_scan_message_and_direct_call(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Makefile").write_text("", encoding="utf-8")
    assert _handler("batch_11_15", "build_systems")({"scan": False}) == {
        "state": "INVALID_INPUT",
        "field": "scan",
        "message": "scan must be true",
    }
    assert _handler("batch_11_15", "build_systems")({"scan": True}) == {"state": "SUCCESS", "systems": "make"}


def test_build_systems_is_closed_to_other_agents(registry):
    assert _call(registry, "devops_build_systems", {"scan": True}, caller="A05")["state"] == "UNAUTHORIZED"
