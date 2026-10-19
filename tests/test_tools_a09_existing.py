"""Behaviour of the pre-v2.2 A09 reviewer tools (output values for specific inputs, every branch the handlers take).

Tools: rev_diff_size, rev_sensitive_paths, rev_auto_approve_check, rev_review_latency_p95, rev_severity_histogram,
rev_false_approve_rate, rev_comment_density, rev_test_pairing, rev_unresolved_threads. (rev_risk_tier, rev_verdict_shape and
rev_rule_dispute_flag are covered by tests/test_tools_a09.py.)

Calls go through the registry as A09 and, for parity, straight into the handler. conftest evicts swarm* modules between
tests, so the registry and handler modules are resolved lazily from the current module graph.
"""
from __future__ import annotations

import importlib

import pytest

CALLER = "A09"


def _registry():
    return importlib.import_module("swarm.tool_registry")


def _handlers():
    return importlib.import_module("swarm.tools.a09_tools")


def call(name: str, payload: dict, caller: str = CALLER) -> dict:
    return _registry().call_tool(name, caller, payload)


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    assert result["message"]


# --- rev_diff_size ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("added,deleted,max_lines,over", [
    (10, 2, 400, False),
    (200, 200, 400, False),  # exactly at the budget is not over it
    (200, 201, 400, True),   # one line past the budget
    (401, 0, 400, True),
    (0, 0, 0, False),
    (1, 0, 0, True),
    (0, 401, 400, True),     # deletions count as much as additions
])
def test_diff_size_compares_added_plus_deleted_with_budget(added, deleted, max_lines, over):
    assert call("rev_diff_size", {"added": added, "deleted": deleted, "max_lines": max_lines}) == {
        "state": "SUCCESS", "over_budget": over}


def test_diff_size_handler_matches_the_registry_result():
    payload = {"added": 300, "deleted": 150, "max_lines": 400}
    direct = importlib.import_module("swarm.tools.batch_06_10").diff_size(payload)
    assert direct == call("rev_diff_size", payload) == {"state": "SUCCESS", "over_budget": True}


@pytest.mark.parametrize("payload,field", [
    ({"deleted": 0, "max_lines": 400}, "added"),
    ({"added": 1, "max_lines": 400}, "deleted"),
    ({"added": 1, "deleted": 0}, "max_lines"),
    ({"added": "10", "deleted": 0, "max_lines": 400}, "added"),
    ({"added": True, "deleted": 0, "max_lines": 400}, "added"),   # a bool is not a line count
    ({"added": 1.5, "deleted": 0, "max_lines": 400}, "added"),
    ({"added": 1, "deleted": None, "max_lines": 400}, "deleted"),
    ({"added": 1, "deleted": 0, "max_lines": 400, "extra": 1}, "extra"),
])
def test_diff_size_rejects_malformed_input_naming_the_field(payload, field):
    invalid(call("rev_diff_size", payload), field)


def test_diff_size_only_a09_may_call_it():
    result = call("rev_diff_size", {"added": 1, "deleted": 0, "max_lines": 400}, caller="A01")
    assert result["state"] == "UNAUTHORIZED" and result["target"] == "rev_diff_size"


@pytest.mark.parametrize("probe,state", [("dependency", "DEPENDENCY_UNAVAILABLE"), ("partial", "PARTIAL_SUCCESS"),
                                         ("sleep", "TIMEOUT")])
def test_diff_size_probe_branches(probe, state):
    result = call("rev_diff_size", {"added": 1, "deleted": 0, "max_lines": 400, "probe": probe})
    assert result["state"] == state


@pytest.mark.parametrize("payload", [
    {"added": 1000, "deleted": -700, "max_lines": 400},   # net 300 hides a 1,700-line diff
    {"added": -5, "deleted": 0, "max_lines": 400},
    {"added": 1, "deleted": 0, "max_lines": -1},
])
def test_diff_size_rejects_negative_counts(payload):
    # agents.json input_schema declares minimum 0 (added/deleted) and 1 (max_lines); the handler repeats the check
    assert call("rev_diff_size", payload)["state"] == "INVALID_INPUT"


# --- rev_sensitive_paths ----------------------------------------------------------------------------------------


def test_sensitive_paths_prompt_example():
    result = call("rev_sensitive_paths", {"paths": ["services/auth/login.py", "docs/readme.md",
                                                    "db/migrations/0002_add.sql"]})
    assert result == {
        "state": "SUCCESS", "sensitive_count": 2, "categories": ["auth", "migrations"],
        "flagged_paths": ["services/auth/login.py", "db/migrations/0002_add.sql"], "co_sign_required": True,
    }


def test_sensitive_paths_clean_and_empty_lists_need_no_cosign():
    clean = call("rev_sensitive_paths", {"paths": ["src/utils/format.py", "docs/readme.md"]})
    assert clean == {"state": "SUCCESS", "sensitive_count": 0, "categories": [], "flagged_paths": [],
                     "co_sign_required": False}
    empty = call("rev_sensitive_paths", {"paths": []})
    assert empty["sensitive_count"] == 0 and empty["co_sign_required"] is False and empty["flagged_paths"] == []


def test_sensitive_paths_every_category_is_detected_and_cosign_follows_the_high_risk_set():
    cases = {
        "api/openapi.yaml": ("contracts", False),
        "db/migrations/1_init.sql": ("migrations", False),
        "svc/auth/token.py": ("auth", True),
        "svc/payments/charge.py": ("payments", True),
        "legal/privacy/policy.md": ("pii", True),
        "infra/main.tf": ("iac", True),
    }
    for path, (category, cosign) in cases.items():
        result = call("rev_sensitive_paths", {"paths": [path]})
        assert result["categories"] == [category], path
        assert result["flagged_paths"] == [path]
        assert result["co_sign_required"] is cosign, path


def test_sensitive_paths_cosign_only_when_a_high_risk_category_is_among_many():
    only_low = call("rev_sensitive_paths", {"paths": ["api/openapi.yaml", "db/migrate/1.sql"]})
    assert only_low["categories"] == ["contracts", "migrations"] and only_low["co_sign_required"] is False
    mixed = call("rev_sensitive_paths", {"paths": ["api/openapi.yaml", "src/stripe_client.py"]})
    assert mixed["categories"] == ["contracts", "payments"] and mixed["co_sign_required"] is True


def test_sensitive_paths_matches_whole_word_tokens_case_insensitively_with_backslashes():
    result = call("rev_sensitive_paths", {"paths": ["api\\Payments\\Refund.cs", "Dockerfile", "src/authors.py",
                                                    "src/authenticate.py", "src/preauth/x.py"]})
    assert result["flagged_paths"] == ["api\\Payments\\Refund.cs", "Dockerfile"]  # authors/authenticate/preauth are not tokens
    assert result["categories"] == ["iac", "payments"]


def test_sensitive_paths_duplicates_are_reported_once_in_first_seen_order():
    result = call("rev_sensitive_paths", {"paths": ["b/auth.py", "a/openapi.json", "b/auth.py", "a/openapi.json"]})
    assert result["flagged_paths"] == ["b/auth.py", "a/openapi.json"]
    assert result["sensitive_count"] == 2
    assert result["categories"] == ["auth", "contracts"]  # sorted


def test_sensitive_paths_handler_matches_the_registry_result():
    payload = {"paths": ["x/terraform/main.tf", "y/z.py"]}
    assert _handlers().sensitive_paths(payload) == call("rev_sensitive_paths", payload)


@pytest.mark.parametrize("payload,field", [
    ({}, "paths"),
    ({"paths": "services/auth.py"}, "paths"),
    ({"paths": [""]}, "paths"),
    ({"paths": [1]}, "paths"),
    ({"paths": ["a"] * 5001}, "paths"),
    ({"paths": ["a" * 4097]}, "paths"),
    ({"paths": [], "other": 1}, "other"),
])
def test_sensitive_paths_rejects_malformed_and_oversized_input(payload, field):
    invalid(call("rev_sensitive_paths", payload), field)


def test_sensitive_paths_accepts_the_documented_maximum():
    result = call("rev_sensitive_paths", {"paths": [f"src/m{i}.py" for i in range(5000)]})
    assert result["state"] == "SUCCESS" and result["sensitive_count"] == 0


# --- rev_auto_approve_check -------------------------------------------------------------------------------------


def _auto(**over) -> dict:
    body = {"added": 20, "deleted": 5, "changed_paths": ["src/utils/format.py"], "sast_findings": 0,
            "tests_present": True, "first_pass_pct": 95}
    body.update(over)
    return call("rev_auto_approve_check", body)


def test_auto_approve_all_criteria_met():
    assert _auto() == {"state": "SUCCESS", "auto_approve": True, "route": "auto_approve", "failed_criteria": [],
                       "diff_lines": 25}


@pytest.mark.parametrize("added,deleted,ok", [(99, 0, True), (50, 49, True), (100, 0, False), (60, 40, False),
                                              (0, 100, False)])
def test_auto_approve_diff_threshold_is_strictly_below_100(added, deleted, ok):
    result = _auto(added=added, deleted=deleted)
    assert result["diff_lines"] == added + deleted
    assert result["auto_approve"] is ok
    assert result["failed_criteria"] == ([] if ok else ["diff_size"])


@pytest.mark.parametrize("pct,ok", [(90.01, True), (91, True), (100, True), (90, False), (89.99, False), (0, False)])
def test_auto_approve_first_pass_rate_must_exceed_90(pct, ok):
    result = _auto(first_pass_pct=pct)
    assert result["auto_approve"] is ok
    assert result["failed_criteria"] == ([] if ok else ["first_pass_rate"])


def test_auto_approve_single_failures_name_exactly_one_criterion_and_route_to_full_review():
    for over, name in [({"sast_findings": 1}, "sast"), ({"tests_present": False}, "tests"),
                       ({"changed_paths": ["svc/auth/login.py"]}, "restricted_paths")]:
        result = _auto(**over)
        assert result["failed_criteria"] == [name]
        assert result["auto_approve"] is False and result["route"] == "full_semantic_review"


@pytest.mark.parametrize("path", ["api/openapi.yaml", "svc/auth/x.py", "svc/payments/x.py", "db/migrations/1.sql",
                                  "privacy/x.md", "infra/main.tf"])
def test_auto_approve_every_restricted_category_blocks(path):
    assert _auto(changed_paths=["src/ok.py", path])["failed_criteria"] == ["restricted_paths"]


def test_auto_approve_reports_all_failed_criteria_in_fixed_order():
    result = _auto(added=50, deleted=50, changed_paths=["auth/a.py"], sast_findings=2, tests_present=False,
                   first_pass_pct=90)
    assert result["failed_criteria"] == ["diff_size", "restricted_paths", "sast", "tests", "first_pass_rate"]
    assert result["diff_lines"] == 100 and result["route"] == "full_semantic_review"


def test_auto_approve_empty_changed_paths_do_not_block():
    assert _auto(changed_paths=[])["auto_approve"] is True


def test_auto_approve_handler_matches_the_registry_result():
    body = {"added": 1, "deleted": 2, "changed_paths": ["a/b.py"], "sast_findings": 0, "tests_present": True,
            "first_pass_pct": 99.5}
    assert _handlers().auto_approve_check(body) == call("rev_auto_approve_check", body)


@pytest.mark.parametrize("over,field", [
    ({"added": -1}, "added"),
    ({"deleted": -1}, "deleted"),
    ({"sast_findings": -1}, "sast_findings"),
    ({"first_pass_pct": 100.5}, "first_pass_pct"),
    ({"first_pass_pct": -0.1}, "first_pass_pct"),
    ({"tests_present": "yes"}, "tests_present"),
    ({"tests_present": 1}, "tests_present"),
    ({"changed_paths": [""]}, "changed_paths"),
    ({"changed_paths": "a.py"}, "changed_paths"),
    ({"added": 1.5}, "added"),
])
def test_auto_approve_rejects_out_of_range_and_wrong_typed_fields(over, field):
    invalid(_auto(**over), field)


def test_auto_approve_requires_every_field():
    body = {"added": 1, "deleted": 1, "changed_paths": [], "sast_findings": 0, "tests_present": True,
            "first_pass_pct": 95}
    for key in body:
        trimmed = {k: v for k, v in body.items() if k != key}
        invalid(call("rev_auto_approve_check", trimmed), key)


# --- rev_review_latency_p95 -------------------------------------------------------------------------------------


def _p95(durations, target):
    return call("rev_review_latency_p95", {"durations_min": durations, "target_min": target})


def test_latency_p95_prompt_example_uses_nearest_rank():
    # 10 samples: rank ceil(9.5) = 10 -> the maximum
    assert _p95([5, 10, 12, 20, 25, 40, 8, 9, 11, 15], 30) == {
        "state": "SUCCESS", "sample_count": 10, "p95_min": 40.0, "meets_target": False}


@pytest.mark.parametrize("n,expected_rank", [(1, 1), (2, 2), (19, 19), (20, 19), (21, 20), (40, 38), (100, 95), (101, 96)])
def test_latency_p95_nearest_rank_index(n, expected_rank):
    values = list(range(1, n + 1))  # value == rank
    assert _p95(values, 1000)["p95_min"] == float(expected_rank)


def test_latency_p95_sorts_unordered_input():
    assert _p95([9, 1, 5, 3, 7, 2, 8, 4, 6, 10, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11], 30)["p95_min"] == 19.0


def test_latency_p95_target_comparison_is_strict():
    assert _p95([10, 20, 30], 30)["meets_target"] is False   # p95 == target does not meet it
    assert _p95([10, 20, 30], 30.01)["meets_target"] is True
    assert _p95([10, 20, 29.99], 30)["meets_target"] is True


def test_latency_p95_rounds_output_but_compares_the_raw_value():
    result = _p95([7.123], 7.123)
    assert result["p95_min"] == 7.12 and result["meets_target"] is False
    assert _p95([7.123], 7.124)["meets_target"] is True
    assert _p95([7.12], 7.123)["meets_target"] is True


def test_latency_p95_single_sample_and_zero_target():
    assert _p95([0], 0) == {"state": "SUCCESS", "sample_count": 1, "p95_min": 0.0, "meets_target": False}
    assert _p95([0], 1)["meets_target"] is True


def test_latency_p95_outlier_below_the_5_percent_tail_is_ignored():
    durations = [10] * 19 + [500]  # one slow review in 20 sits above rank 19
    assert _p95(durations, 30) == {"state": "SUCCESS", "sample_count": 20, "p95_min": 10.0, "meets_target": True}


def test_latency_p95_handler_matches_the_registry_result():
    payload = {"durations_min": [3, 4, 5, 6], "target_min": 10}
    assert _handlers().review_latency_p95(payload) == call("rev_review_latency_p95", payload)


@pytest.mark.parametrize("payload,field", [
    ({"durations_min": [], "target_min": 30}, "durations_min"),
    ({"durations_min": [-1], "target_min": 30}, "durations_min"),
    ({"durations_min": [1, "x"], "target_min": 30}, "durations_min"),
    ({"durations_min": "5", "target_min": 30}, "durations_min"),
    ({"durations_min": [1] * 10001, "target_min": 30}, "durations_min"),
    ({"durations_min": [1]}, "target_min"),
    ({"durations_min": [1], "target_min": -1}, "target_min"),
    ({"durations_min": [1], "target_min": "30"}, "target_min"),
])
def test_latency_p95_rejects_malformed_and_oversized_input(payload, field):
    invalid(call("rev_review_latency_p95", payload), field)


def test_latency_p95_accepts_the_documented_maximum_sample_count():
    result = _p95([1.0] * 10000, 2)
    assert result["sample_count"] == 10000 and result["meets_target"] is True


# --- rev_severity_histogram -------------------------------------------------------------------------------------


def _hist(severities):
    return call("rev_severity_histogram", {"severities": severities})


def test_severity_histogram_prompt_example():
    assert _hist(["info", "minor", "major", "info"]) == {
        "state": "SUCCESS", "total": 4, "info": 2, "minor": 1, "major": 1, "critical": 0, "blocker": 0,
        "blocking_count": 1, "max_severity": "major"}


def test_severity_histogram_empty_input_is_none_with_no_blocking():
    assert _hist([]) == {"state": "SUCCESS", "total": 0, "info": 0, "minor": 0, "major": 0, "critical": 0, "blocker": 0,
                         "blocking_count": 0, "max_severity": "none"}


@pytest.mark.parametrize("severities,blocking,top", [
    (["info"], 0, "info"),
    (["info", "minor", "minor"], 0, "minor"),      # minors and infos pass the gate
    (["minor", "major"], 1, "major"),               # major is the first blocking severity
    (["major", "critical"], 2, "critical"),
    (["blocker", "critical", "minor"], 2, "blocker"),
    (["blocker"] * 3, 3, "blocker"),
])
def test_severity_histogram_blocking_threshold_and_max(severities, blocking, top):
    result = _hist(severities)
    assert result["blocking_count"] == blocking and result["max_severity"] == top


def test_severity_histogram_max_does_not_depend_on_order():
    forward = _hist(["blocker", "info", "major"])
    backward = _hist(["major", "info", "blocker"])
    assert forward == backward and forward["max_severity"] == "blocker"


def test_severity_histogram_counts_sum_to_total():
    severities = ["info"] * 3 + ["minor"] * 2 + ["major"] + ["critical"] * 4 + ["blocker"] * 5
    result = _hist(severities)
    assert result["total"] == 15
    assert sum(result[s] for s in ("info", "minor", "major", "critical", "blocker")) == 15
    assert (result["info"], result["minor"], result["major"], result["critical"], result["blocker"]) == (3, 2, 1, 4, 5)


def test_severity_histogram_handler_matches_the_registry_result():
    payload = {"severities": ["minor", "critical"]}
    assert _handlers().severity_histogram(payload) == call("rev_severity_histogram", payload)


@pytest.mark.parametrize("payload,field", [
    ({}, "severities"),
    ({"severities": "major"}, "severities"),
    ({"severities": ["bad"]}, "severities"),
    ({"severities": ["MAJOR"]}, "severities"),     # the gate scale is lowercase and exact
    ({"severities": ["nit"]}, "severities"),
    ({"severities": [3]}, "severities"),
    ({"severities": ["info"] * 10001}, "severities"),
    ({"severities": [], "x": 1}, "x"),
])
def test_severity_histogram_rejects_malformed_and_oversized_input(payload, field):
    invalid(call("rev_severity_histogram", payload), field)


def test_severity_histogram_matches_the_gate_blocking_threshold():
    gates = importlib.import_module("swarm.gates")
    blocking = [s for s in ("info", "minor", "major", "critical", "blocker")
                if _hist([s])["blocking_count"] == 1]
    assert blocking and blocking[0] == gates.BLOCKING_SEVERITY


# --- rev_false_approve_rate -------------------------------------------------------------------------------------


def _far(approved, defects, target):
    return call("rev_false_approve_rate", {"approved_count": approved, "defects_found_later": defects,
                                           "target_pct": target})


def test_false_approve_rate_prompt_example():
    assert _far(200, 3, 2) == {"state": "SUCCESS", "rate_pct": 1.5, "within_target": True}


@pytest.mark.parametrize("approved,defects,target,rate,ok", [
    (100, 0, 2, 0.0, True),
    (100, 1, 2, 1.0, True),
    (100, 2, 2, 2.0, False),     # equal to the target is not within it (target is "< 2 %")
    (200, 4, 2, 2.0, False),
    (50, 50, 100, 100.0, False),
    (1, 1, 99.99, 100.0, False),
    (3, 1, 33.34, 33.33, True),
    (3, 1, 33.33, 33.33, False),  # compared on the unrounded 33.3333...
    (7, 0, 0, 0.0, False),        # a zero target can never be met
])
def test_false_approve_rate_values_and_strict_target(approved, defects, target, rate, ok):
    assert _far(approved, defects, target) == {"state": "SUCCESS", "rate_pct": rate, "within_target": ok}


def test_false_approve_rate_rounds_to_two_places():
    assert _far(7, 1, 50)["rate_pct"] == 14.29
    assert _far(6, 1, 50)["rate_pct"] == 16.67


def test_false_approve_rate_defects_above_approvals_is_invalid_and_names_the_field():
    result = _far(3, 4, 50)
    invalid(result, "defects_found_later")
    assert "approved_count" in result["message"]
    assert _far(3, 3, 100)["state"] == "SUCCESS"  # equal is allowed


def test_false_approve_rate_handler_returns_the_same_invalid_result_directly():
    direct = _handlers().false_approve_rate({"approved_count": 1, "defects_found_later": 2, "target_pct": 5})
    assert direct["state"] == "INVALID_INPUT" and direct["field"] == "defects_found_later"
    ok = {"approved_count": 40, "defects_found_later": 1, "target_pct": 5}
    assert _handlers().false_approve_rate(ok) == call("rev_false_approve_rate", ok)


@pytest.mark.parametrize("payload,field", [
    ({"approved_count": 0, "defects_found_later": 0, "target_pct": 2}, "approved_count"),   # no division by zero
    ({"approved_count": -3, "defects_found_later": 0, "target_pct": 2}, "approved_count"),
    ({"approved_count": 3, "defects_found_later": -1, "target_pct": 2}, "defects_found_later"),
    ({"approved_count": 3, "defects_found_later": 0, "target_pct": 100.5}, "target_pct"),
    ({"approved_count": 3, "defects_found_later": 0, "target_pct": -1}, "target_pct"),
    ({"approved_count": 3.5, "defects_found_later": 0, "target_pct": 2}, "approved_count"),
    ({"approved_count": "3", "defects_found_later": 0, "target_pct": 2}, "approved_count"),
    ({"defects_found_later": 0, "target_pct": 2}, "approved_count"),
    ({"approved_count": 3, "target_pct": 2}, "defects_found_later"),
    ({"approved_count": 3, "defects_found_later": 0}, "target_pct"),
])
def test_false_approve_rate_rejects_malformed_input(payload, field):
    invalid(call("rev_false_approve_rate", payload), field)


# --- rev_comment_density ----------------------------------------------------------------------------------------


def _density(lines):
    return call("rev_comment_density", {"added_lines": lines})


def test_comment_density_counts_prefixes_after_stripping_and_skips_blank_lines():
    lines = ["# a", "def f():", "    x = 1  # trailing", "  // c", "/* b */", " * doc", "*", "", "   ", "*/",
             "-- sql", "<!-- h -->", "*p = 1", "return x"]
    assert _density(lines) == {"state": "SUCCESS", "nonblank_lines": 12, "comment_lines": 8, "density": 0.6667}


def test_comment_density_excludes_preprocessor_directives_and_shebang():
    lines = ["#include <stdio.h>", "#define X 1", "#if DEBUG", "#elif A", "#else", "#endif", "#pragma once", "#undef X",
             "#error no", "#import <a>", "#!/usr/bin/env python3", "# real comment"]
    result = _density(lines)
    assert result["nonblank_lines"] == 12 and result["comment_lines"] == 1 and result["density"] == 0.0833


def test_comment_density_trailing_comments_and_mid_line_markers_do_not_count():
    result = _density(["x = 1  # trailing", "print('# not a comment')", "a // b"])
    assert result == {"state": "SUCCESS", "nonblank_lines": 3, "comment_lines": 0, "density": 0.0}


def test_comment_density_all_comments_is_one_and_empty_inputs_are_zero():
    assert _density(["# a", "// b"]) == {"state": "SUCCESS", "nonblank_lines": 2, "comment_lines": 2, "density": 1.0}
    zero = {"state": "SUCCESS", "nonblank_lines": 0, "comment_lines": 0, "density": 0.0}
    assert _density([]) == zero
    assert _density(["", "   ", "\t"]) == zero   # whitespace-only lines never divide by zero


def test_comment_density_rounds_to_four_places():
    assert _density(["# c", "x", "y"])["density"] == 0.3333
    assert _density(["# c"] + ["x"] * 5)["density"] == 0.1667
    assert _density(["# c"] * 2 + ["x"] * 5)["density"] == 0.2857


def test_comment_density_star_marker_needs_a_space_or_be_alone():
    result = _density(["* item", "*", "**bold**", "*ptr = 0;"])
    assert result["comment_lines"] == 2 and result["nonblank_lines"] == 4


def test_comment_density_handles_crlf_and_indented_lines():
    assert _density(["    # a\r", "\t// b\r", "code\r"])["comment_lines"] == 2


def test_comment_density_handler_matches_the_registry_result():
    payload = {"added_lines": ["# a", "b"]}
    assert _handlers().comment_density(payload) == call("rev_comment_density", payload)


@pytest.mark.parametrize("payload,field", [
    ({}, "added_lines"),
    ({"added_lines": "# a"}, "added_lines"),
    ({"added_lines": [1]}, "added_lines"),
    ({"added_lines": [None]}, "added_lines"),
    ({"added_lines": ["x" * 10001]}, "added_lines"),
    ({"added_lines": [""] * 20001}, "added_lines"),
    ({"added_lines": [], "extra": 1}, "extra"),
])
def test_comment_density_rejects_malformed_and_oversized_input(payload, field):
    invalid(call("rev_comment_density", payload), field)


def test_comment_density_accepts_the_documented_maximum():
    result = _density(["# c"] * 20000)
    assert result["nonblank_lines"] == 20000 and result["density"] == 1.0


# --- rev_test_pairing -------------------------------------------------------------------------------------------


def _pair(paths):
    return call("rev_test_pairing", {"changed_paths": paths})


def test_test_pairing_python_source_with_its_test():
    assert _pair(["src/foo.py", "tests/test_foo.py"]) == {
        "state": "SUCCESS", "source_files": 1, "test_files": 1, "test_to_source_ratio": 1.0,
        "untested_sources": [], "all_sources_paired": True}


def test_test_pairing_reports_untested_sources_in_input_order_and_the_ratio():
    result = _pair(["src/foo.py", "tests/test_foo.py", "src/bar.py", "src/baz.go", "src/baz_test.go", "lib/zed.py"])
    assert result["source_files"] == 4 and result["test_files"] == 2
    assert result["test_to_source_ratio"] == 0.5
    assert result["untested_sources"] == ["src/bar.py", "lib/zed.py"]
    assert result["all_sources_paired"] is False


@pytest.mark.parametrize("source,test", [
    ("src/foo.py", "tests/test_foo.py"),
    ("src/foo.py", "tests/foo_test.py"),
    ("src/foo.py", "src/foo_test.py"),
    ("pkg/baz.go", "pkg/baz_test.go"),
    ("web/app.ts", "web/app.test.ts"),
    ("web/app.ts", "web/app.spec.ts"),
    ("web/app.js", "web/__tests__/app.js"),
    ("src/foo.rb", "spec/foo_spec.rb"),
    ("src/foo.py", "tests/unit/foo.py"),       # anything in a test directory covers its stem
])
def test_test_pairing_recognized_test_conventions(source, test):
    result = _pair([source, test])
    assert result["all_sources_paired"] is True and result["untested_sources"] == []
    assert (result["source_files"], result["test_files"]) == (1, 1)


def test_test_pairing_non_code_files_are_ignored_entirely():
    assert _pair(["README.md", "img/x.png", "config.yaml", "Makefile"]) == {
        "state": "SUCCESS", "source_files": 0, "test_files": 0, "test_to_source_ratio": 0.0,
        "untested_sources": [], "all_sources_paired": True}


def test_test_pairing_only_tests_changed_has_zero_ratio_and_is_paired():
    result = _pair(["tests/test_only.py", "tests/test_other.py"])
    assert (result["source_files"], result["test_files"], result["test_to_source_ratio"]) == (0, 2, 0.0)
    assert result["all_sources_paired"] is True


def test_test_pairing_empty_list():
    assert _pair([]) == {"state": "SUCCESS", "source_files": 0, "test_files": 0, "test_to_source_ratio": 0.0,
                         "untested_sources": [], "all_sources_paired": True}


def test_test_pairing_duplicate_paths_are_counted_once():
    result = _pair(["src/foo.py", "src/foo.py", "tests/test_foo.py", "tests/test_foo.py"])
    assert (result["source_files"], result["test_files"]) == (1, 1)


def test_test_pairing_matching_is_case_insensitive_but_output_keeps_original_case_and_separators():
    result = _pair(["Src\\Qux.PY", "src/Other.py", "tests/test_other.py"])
    assert result["source_files"] == 2 and result["test_files"] == 1
    assert result["untested_sources"] == ["Src\\Qux.PY"]


def test_test_pairing_matches_by_stem_across_directories():
    # documented behaviour of the stem match: one test named for the stem covers every changed source with that stem
    result = _pair(["a/foo.py", "b/foo.py", "tests/test_foo.py"])
    assert result["untested_sources"] == [] and result["test_to_source_ratio"] == 0.5


def test_test_pairing_does_not_treat_lookalike_names_as_tests():
    result = _pair(["src/latest.py", "src/contest.py", "src/attestation.py"])
    assert result["source_files"] == 3 and result["test_files"] == 0
    assert result["untested_sources"] == ["src/latest.py", "src/contest.py", "src/attestation.py"]


def test_test_pairing_ratio_rounds_to_four_places():
    result = _pair(["a.py", "b.py", "c.py", "tests/test_a.py"])
    assert result["test_to_source_ratio"] == 0.3333 and result["untested_sources"] == ["b.py", "c.py"]


def test_test_pairing_handler_matches_the_registry_result():
    payload = {"changed_paths": ["x/a.py", "tests/test_b.py"]}
    assert _handlers().test_pairing(payload) == call("rev_test_pairing", payload)


@pytest.mark.parametrize("payload,field", [
    ({}, "changed_paths"),
    ({"changed_paths": "a.py"}, "changed_paths"),
    ({"changed_paths": [""]}, "changed_paths"),
    ({"changed_paths": [7]}, "changed_paths"),
    ({"changed_paths": ["a.py"] * 5001}, "changed_paths"),
    ({"changed_paths": ["a" * 4097]}, "changed_paths"),
    ({"changed_paths": [], "x": 1}, "x"),
])
def test_test_pairing_rejects_malformed_and_oversized_input(payload, field):
    invalid(call("rev_test_pairing", payload), field)


def test_test_pairing_pairs_java_style_test_names():
    result = _pair(["src/main/java/Foo.java", "src/test/java/FooTest.java"])
    assert result["untested_sources"] == [] and result["all_sources_paired"] is True


@pytest.mark.parametrize("source,test", [
    ("src/Foo.java", "src/FooTests.java"),
    ("src/Foo.cs", "src/FooTests.cs"),
    ("src/Foo.kt", "src/FooSpec.kt"),
    ("src/Foo.scala", "src/FooSpec.scala"),
    ("src/Foo.java", "src/TestFoo.java"),
    ("web/Foo.ts", "web/Foo.test.ts"),
    ("pkg/foo.go", "pkg/foo_test.go"),
    ("src/foo.py", "tests/test_foo.py"),
    ("src/FooBar.php", "src/FooBarTest.php"),
])
def test_test_pairing_camelcase_and_jvm_conventions(source, test):
    result = _pair([source, test])
    assert result["all_sources_paired"] is True and result["test_files"] == 1


def test_test_pairing_camelcase_lookalikes_stay_sources():
    result = _pair(["src/Latest.java", "src/Contest.java", "src/Testing.java", "src/Spec.kt"])
    assert result["source_files"] == 4 and result["test_files"] == 0


# --- rev_unresolved_threads -------------------------------------------------------------------------------------


def _thread(tid, resolved, blocking):
    return {"id": tid, "resolved": resolved, "blocking": blocking}


def _threads(*items):
    return call("rev_unresolved_threads", {"threads": list(items)})


def test_unresolved_threads_mixed_set():
    result = _threads(_thread("a", False, True), _thread("b", True, True), _thread("c", False, False))
    assert result == {"state": "SUCCESS", "total_threads": 3, "unresolved_count": 2, "unresolved_blocking_count": 1,
                      "unresolved_ids": ["a", "c"], "blocking_clear": False}


def test_unresolved_threads_empty_list_is_clear():
    assert _threads() == {"state": "SUCCESS", "total_threads": 0, "unresolved_count": 0,
                          "unresolved_blocking_count": 0, "unresolved_ids": [], "blocking_clear": True}


def test_unresolved_threads_open_non_blocking_threads_keep_blocking_clear():
    result = _threads(_thread("n1", False, False), _thread("n2", False, False))
    assert result["unresolved_count"] == 2 and result["unresolved_blocking_count"] == 0
    assert result["blocking_clear"] is True and result["unresolved_ids"] == ["n1", "n2"]


def test_unresolved_threads_resolved_blocking_threads_do_not_block():
    result = _threads(_thread("r", True, True))
    assert result["unresolved_count"] == 0 and result["blocking_clear"] is True and result["unresolved_ids"] == []


def test_unresolved_threads_all_open_and_blocking():
    result = _threads(_thread("x", False, True), _thread("y", False, True))
    assert result["unresolved_blocking_count"] == 2 and result["blocking_clear"] is False


def test_unresolved_threads_ids_keep_input_order():
    result = _threads(_thread("z", False, False), _thread("a", False, False), _thread("m", False, True))
    assert result["unresolved_ids"] == ["z", "a", "m"]


def test_unresolved_threads_duplicate_ids_are_rejected_naming_the_field():
    result = _threads(_thread("a", False, True), _thread("a", True, False))
    invalid(result, "threads")
    assert "unique" in result["message"]


def test_unresolved_threads_ids_are_case_sensitive():
    assert _threads(_thread("a", False, False), _thread("A", False, False))["unresolved_count"] == 2


def test_unresolved_threads_handler_matches_the_registry_result_and_rejects_duplicates_directly():
    payload = {"threads": [_thread("a", False, True)]}
    assert _handlers().unresolved_threads(payload) == call("rev_unresolved_threads", payload)
    dup = {"threads": [_thread("q", False, True), _thread("q", False, True)]}
    assert _handlers().unresolved_threads(dup)["field"] == "threads"


@pytest.mark.parametrize("payload", [
    {},
    {"threads": "a"},
    {"threads": ["a"]},
    {"threads": [{"id": "a", "resolved": False}]},                         # blocking missing
    {"threads": [{"id": "a", "blocking": False}]},                         # resolved missing
    {"threads": [{"resolved": False, "blocking": False}]},                 # id missing
    {"threads": [{"id": "", "resolved": False, "blocking": False}]},
    {"threads": [{"id": "a" * 201, "resolved": False, "blocking": False}]},
    {"threads": [{"id": "a", "resolved": "no", "blocking": False}]},
    {"threads": [{"id": "a", "resolved": False, "blocking": 1}]},
    {"threads": [{"id": 1, "resolved": False, "blocking": False}]},
    {"threads": [{"id": "a", "resolved": False, "blocking": False, "note": "x"}]},
    {"threads": [_thread(f"t{i}", True, False) for i in range(5001)]},
])
def test_unresolved_threads_rejects_malformed_and_oversized_input(payload):
    invalid(call("rev_unresolved_threads", payload), "threads")


def test_unresolved_threads_accepts_the_documented_maximum():
    result = _threads(*[_thread(f"t{i}", i % 2 == 0, i % 3 == 0) for i in range(5000)])
    assert result["total_threads"] == 5000 and result["unresolved_count"] == 2500


# --- cross-tool: the review-gate flow the prompt describes -------------------------------------------------------


def test_sensitive_paths_feed_auto_approve_consistently():
    paths = ["src/ok.py", "svc/payments/charge.py"]
    sensitive = call("rev_sensitive_paths", {"paths": paths})
    auto = _auto(changed_paths=paths)
    assert sensitive["sensitive_count"] == 1 and sensitive["co_sign_required"] is True
    assert auto["failed_criteria"] == ["restricted_paths"] and auto["route"] == "full_semantic_review"


def test_other_agents_cannot_call_any_a09_tool():
    probes = {
        "rev_sensitive_paths": {"paths": []},
        "rev_review_latency_p95": {"durations_min": [1], "target_min": 1},
        "rev_severity_histogram": {"severities": []},
        "rev_false_approve_rate": {"approved_count": 1, "defects_found_later": 0, "target_pct": 1},
        "rev_comment_density": {"added_lines": []},
        "rev_test_pairing": {"changed_paths": []},
        "rev_unresolved_threads": {"threads": []},
    }
    for name, payload in probes.items():
        assert call(name, payload, caller="A05")["state"] == "UNAUTHORIZED", name
        assert call(name, payload)["state"] == "SUCCESS", name
