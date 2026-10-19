"""Behaviour tests for the pre-v2.2 A08 lane tools (qa_ prefix) that had only generated contract probes.

Tools: qa_runner_detect, qa_flaky_classify, qa_criteria_coverage, qa_mutation_gate, qa_runtime_p95,
qa_skip_ratio, qa_defect_escape_rate, qa_retry_budget, qa_name_convention.

Rules come from swarm/tools/a08_tools.py, swarm/tools/batch_06_10.py and the A08 prompt (prompts/A08-qa.md).
conftest's swarm_dir fixture evicts swarm* modules between tests, so the registry and the handler modules are
resolved lazily through importlib, never bound at import time.
"""
from __future__ import annotations

import importlib
import json

import pytest

NAN = float("nan")


def _live(module: str):
    return importlib.import_module(module)


def call(name: str, payload, caller: str = "A08") -> dict:
    return _live("swarm.tool_registry").call_tool(name, caller, payload)


def ok(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


def bad(name: str, payload, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result
    assert result["message"]
    return result


def handler(fn: str):
    return getattr(_live("swarm.tools.a08_tools"), fn)


def without(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "state"}


TOOLS = {
    "qa_runner_detect": {"scan": True},
    "qa_flaky_classify": {"histories": [{"test": "t", "outcomes": ["pass"]}]},
    "qa_criteria_coverage": {"criteria": ["AC-1"], "tests": []},
    "qa_mutation_gate": {"killed": 1, "survived": 0},
    "qa_runtime_p95": {"durations_s": [1]},
    "qa_skip_ratio": {"tests": [{"name": "t", "status": "passed"}], "max_skip_pct": 5},
    "qa_defect_escape_rate": {"defects": [{"id": "d", "stage": "pre_prod"}]},
    "qa_retry_budget": {"runs": [{"test": "t", "attempts": ["pass"]}], "max_reruns": 1},
    "qa_name_convention": {"names": ["test_a"], "prefix": "test_"},
}


# ---- ownership and probes ------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_only_a08_may_call_and_a_non_object_payload_names_payload(name):
    assert call(name, TOOLS[name], caller="A05")["state"] == "UNAUTHORIZED"
    assert call(name, TOOLS[name], caller="A09")["state"] == "UNAUTHORIZED"
    assert call(name, TOOLS[name])["state"] == "SUCCESS"
    bad(name, [TOOLS[name]], "payload")
    bad(name, {**TOOLS[name], "surprise": 1}, "surprise")


HANDLER_TOOL = {
    "flaky_classify": "qa_flaky_classify", "criteria_coverage": "qa_criteria_coverage", "mutation_gate": "qa_mutation_gate",
    "runtime_p95": "qa_runtime_p95", "skip_ratio": "qa_skip_ratio", "defect_escape_rate": "qa_defect_escape_rate",
    "retry_budget": "qa_retry_budget", "name_convention": "qa_name_convention",
}


@pytest.mark.parametrize("fn", sorted(HANDLER_TOOL))
def test_handler_probes_never_reach_the_lane_function(fn):
    payload = TOOLS[HANDLER_TOOL[fn]]
    assert handler(fn)({**payload, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    with pytest.raises(OSError):
        handler(fn)({**payload, "probe": "dependency"})
    assert handler(fn)(payload)["state"] == "SUCCESS"


# ---- qa_flaky_classify ---------------------------------------------------------------------------


def _hist(test, outcomes):
    return {"test": test, "outcomes": list(outcomes)}


def test_flaky_classify_buckets_every_state_with_exact_output():
    result = ok("qa_flaky_classify", {"histories": [
        _hist("q", ["fail", "pass", "fail", "pass", "pass", "fail", "pass", "pass", "pass", "pass"]),
        _hist("w", ["pass", "fail", "pass", "fail", "pass"]),
        _hist("cf", ["fail"] * 4),
        _hist("u", ["skip", "skip"]),
        _hist("s", ["pass"] * 3),
        _hist("q2", ["pass", "fail", "fail", "fail"]),
    ]})
    assert result == {
        "state": "SUCCESS", "quarantine": ["q", "q2"], "watch": ["w"], "consistent_fail": ["cf"],
        "unobserved": ["u"], "stable_count": 1, "evaluated": 6,
    }


def test_flaky_classify_threshold_is_three_failures_with_a_pass_present():
    two = ok("qa_flaky_classify", {"histories": [_hist("t", ["fail", "fail", "pass"])]})
    three = ok("qa_flaky_classify", {"histories": [_hist("t", ["fail", "fail", "fail", "pass"])]})
    assert two["watch"] == ["t"] and two["quarantine"] == []
    assert three["quarantine"] == ["t"] and three["watch"] == []


def test_flaky_classify_only_the_last_ten_runs_count():
    old_failures = ["fail"] * 3 + ["pass"] * 10  # the three failures fell out of the window
    one_in_window = ["fail"] * 3 + ["pass"] * 9  # window is outcomes[2:], one failure left
    three_in_window = ["fail"] * 3 + ["pass"] * 7  # exactly ten runs, three failures
    result = ok("qa_flaky_classify", {"histories": [
        _hist("old", old_failures), _hist("one", one_in_window), _hist("three", three_in_window),
    ]})
    assert result["stable_count"] == 1
    assert result["watch"] == ["one"]
    assert result["quarantine"] == ["three"]


def test_flaky_classify_skips_do_not_occupy_the_window_or_count_as_runs():
    result = ok("qa_flaky_classify", {"histories": [
        _hist("skipped_between", ["fail", "fail", "fail"] + ["skip"] * 10 + ["pass"]),
        _hist("only_skips", ["skip"]),
    ]})
    assert result["quarantine"] == ["skipped_between"]
    assert result["unobserved"] == ["only_skips"]
    assert result["stable_count"] == 0


def test_flaky_classify_failing_everywhere_is_broken_not_flaky_whatever_the_count():
    result = ok("qa_flaky_classify", {"histories": [_hist("one", ["fail"]), _hist("many", ["fail"] * 50)]})
    assert result["consistent_fail"] == ["one", "many"]
    assert result["quarantine"] == [] and result["watch"] == []


def test_flaky_classify_keeps_input_order_inside_each_bucket():
    names = ["z", "a", "m"]
    result = ok("qa_flaky_classify", {"histories": [_hist(n, ["pass", "fail", "fail", "fail"]) for n in names]})
    assert result["quarantine"] == names


def test_flaky_classify_duplicate_test_is_invalid_and_names_it():
    result = bad("qa_flaky_classify", {"histories": [_hist("dup", ["pass"]), _hist("dup", ["fail"])]}, "histories")
    assert "dup" in result["message"]


def test_flaky_classify_rejects_malformed_and_oversized_input():
    bad("qa_flaky_classify", {}, "histories")
    bad("qa_flaky_classify", {"histories": []}, "histories")
    bad("qa_flaky_classify", {"histories": [_hist("t", [])]}, "histories")
    bad("qa_flaky_classify", {"histories": [_hist("t", ["pass", "error"])]}, "histories")
    bad("qa_flaky_classify", {"histories": [_hist("", ["pass"])]}, "histories")
    bad("qa_flaky_classify", {"histories": [{"test": "t"}]}, "histories")
    bad("qa_flaky_classify", {"histories": [_hist("t", ["pass"] * 201)]}, "histories")
    bad("qa_flaky_classify", {"histories": [_hist(f"t{i}", ["pass"]) for i in range(1001)]}, "histories")
    bad("qa_flaky_classify", {"histories": "t"}, "histories")


def test_flaky_classify_accepts_the_declared_maximum():
    result = ok("qa_flaky_classify", {"histories": [_hist(f"t{i}", ["pass"] * 200) for i in range(1000)]})
    assert result["evaluated"] == 1000 and result["stable_count"] == 1000


# ---- qa_criteria_coverage ------------------------------------------------------------------------


def test_criteria_coverage_reports_exact_gaps_and_orphans():
    result = ok("qa_criteria_coverage", {
        "criteria": ["AC-1", "AC-2", "AC-3"],
        "tests": [
            {"name": "t1", "ac_refs": ["AC-1"]},
            {"name": "t2", "ac_refs": ["AC-1", "AC-3"]},
            {"name": "t3", "ac_refs": []},
            {"name": "t4", "ac_refs": ["AC-9"]},
        ],
    })
    assert result == {
        "state": "SUCCESS", "criteria_count": 3, "covered_count": 2, "coverage_pct": 66.67,
        "uncovered": ["AC-2"], "orphan_tests": ["t3", "t4"],
    }


def test_criteria_coverage_full_and_empty_boundaries():
    full = ok("qa_criteria_coverage", {"criteria": ["A", "B"], "tests": [{"name": "t", "ac_refs": ["B", "A"]}]})
    assert (full["coverage_pct"], full["covered_count"], full["uncovered"], full["orphan_tests"]) == (100.0, 2, [], [])
    none = ok("qa_criteria_coverage", {"criteria": ["A", "B"], "tests": []})
    assert none == {"state": "SUCCESS", "criteria_count": 2, "covered_count": 0, "coverage_pct": 0.0,
                    "uncovered": ["A", "B"], "orphan_tests": []}


def test_criteria_coverage_unknown_refs_do_not_count_and_a_mixed_test_is_not_an_orphan():
    result = ok("qa_criteria_coverage", {
        "criteria": ["AC-1"],
        "tests": [{"name": "mixed", "ac_refs": ["AC-404", "AC-1"]}, {"name": "ghost", "ac_refs": ["AC-404"]}],
    })
    assert result["covered_count"] == 1 and result["orphan_tests"] == ["ghost"]


def test_criteria_coverage_is_case_sensitive_and_deduplicates_repeated_refs():
    result = ok("qa_criteria_coverage", {
        "criteria": ["AC-1", "AC-2"],
        "tests": [{"name": "t", "ac_refs": ["ac-1", "AC-2", "AC-2", "AC-2"]}],
    })
    assert result["covered_count"] == 1 and result["uncovered"] == ["AC-1"] and result["coverage_pct"] == 50.0


def test_criteria_coverage_rounds_to_two_places_and_keeps_criteria_order():
    result = ok("qa_criteria_coverage", {
        "criteria": ["C", "A", "B"], "tests": [{"name": "t", "ac_refs": ["A"]}],
    })
    assert result["coverage_pct"] == 33.33 and result["uncovered"] == ["C", "B"]


def test_criteria_coverage_duplicates_are_invalid_and_named():
    dup_criterion = bad("qa_criteria_coverage", {"criteria": ["A", "A"], "tests": []}, "criteria")
    assert "A" in dup_criterion["message"]
    dup_test = bad("qa_criteria_coverage", {"criteria": ["A"], "tests": [
        {"name": "same", "ac_refs": ["A"]}, {"name": "same", "ac_refs": []}]}, "tests")
    assert "same" in dup_test["message"]


def test_criteria_coverage_rejects_malformed_and_oversized_input():
    bad("qa_criteria_coverage", {"tests": []}, "criteria")
    bad("qa_criteria_coverage", {"criteria": ["A"]}, "tests")
    bad("qa_criteria_coverage", {"criteria": [], "tests": []}, "criteria")
    bad("qa_criteria_coverage", {"criteria": [""], "tests": []}, "criteria")
    bad("qa_criteria_coverage", {"criteria": ["A" * 33], "tests": []}, "criteria")
    bad("qa_criteria_coverage", {"criteria": [7], "tests": []}, "criteria")
    bad("qa_criteria_coverage", {"criteria": ["A"], "tests": [{"name": "t"}]}, "tests")
    bad("qa_criteria_coverage", {"criteria": ["A"], "tests": [{"name": "t", "ac_refs": ["A" * 33]}]}, "tests")
    bad("qa_criteria_coverage", {"criteria": ["A"], "tests": [{"name": "t", "ac_refs": ["A"] * 51}]}, "tests")
    bad("qa_criteria_coverage", {"criteria": [f"C{i}" for i in range(501)], "tests": []}, "criteria")


# ---- qa_mutation_gate ----------------------------------------------------------------------------


def test_mutation_gate_default_threshold_is_seventy_and_inclusive():
    at = ok("qa_mutation_gate", {"killed": 7, "survived": 3})
    below = ok("qa_mutation_gate", {"killed": 69, "survived": 31})
    assert at == {"state": "SUCCESS", "scored_mutants": 10, "score_pct": 70.0, "threshold_pct": 70.0,
                  "meets_threshold": True}
    assert below["score_pct"] == 69.0 and below["meets_threshold"] is False


def test_mutation_gate_timeouts_count_as_detected():
    result = ok("qa_mutation_gate", {"killed": 6, "timeout": 1, "survived": 3})
    assert result["scored_mutants"] == 10 and result["score_pct"] == 70.0 and result["meets_threshold"] is True
    only_timeouts = ok("qa_mutation_gate", {"killed": 0, "timeout": 4, "survived": 0})
    assert only_timeouts["score_pct"] == 100.0 and only_timeouts["scored_mutants"] == 4


def test_mutation_gate_custom_threshold_is_echoed_as_float_and_applied():
    strict = ok("qa_mutation_gate", {"killed": 8, "survived": 2, "threshold_pct": 90})
    lenient = ok("qa_mutation_gate", {"killed": 8, "survived": 2, "threshold_pct": 80})
    assert strict["threshold_pct"] == 90.0 and isinstance(strict["threshold_pct"], float)
    assert strict["meets_threshold"] is False and lenient["meets_threshold"] is True


def test_mutation_gate_extremes():
    assert ok("qa_mutation_gate", {"killed": 5, "survived": 0})["score_pct"] == 100.0
    nothing = ok("qa_mutation_gate", {"killed": 0, "survived": 5})
    assert nothing["score_pct"] == 0.0 and nothing["meets_threshold"] is False
    assert ok("qa_mutation_gate", {"killed": 0, "survived": 5, "threshold_pct": 0})["meets_threshold"] is True
    assert ok("qa_mutation_gate", {"killed": 5, "survived": 0, "threshold_pct": 100})["meets_threshold"] is True


def test_mutation_gate_truncates_to_two_places():
    result = ok("qa_mutation_gate", {"killed": 2, "survived": 1})
    assert result["score_pct"] == 66.66 and result["scored_mutants"] == 3 and result["meets_threshold"] is False


def test_mutation_gate_no_scored_mutants_is_invalid_input_naming_killed():
    assert bad("qa_mutation_gate", {"killed": 0, "survived": 0}, "killed")["message"] == "no scored mutants"
    bad("qa_mutation_gate", {"killed": 0, "survived": 0, "timeout": 0}, "killed")


def test_mutation_gate_rejects_malformed_input():
    bad("qa_mutation_gate", {"survived": 1}, "killed")
    bad("qa_mutation_gate", {"killed": 1}, "survived")
    bad("qa_mutation_gate", {"killed": -1, "survived": 0}, "killed")
    bad("qa_mutation_gate", {"killed": 1, "survived": -1}, "survived")
    bad("qa_mutation_gate", {"killed": 1, "survived": 0, "timeout": -1}, "timeout")
    bad("qa_mutation_gate", {"killed": 1.5, "survived": 0}, "killed")
    bad("qa_mutation_gate", {"killed": True, "survived": 0}, "killed")
    bad("qa_mutation_gate", {"killed": "1", "survived": 0}, "killed")
    bad("qa_mutation_gate", {"killed": 1, "survived": 0, "threshold_pct": 100.5}, "threshold_pct")
    bad("qa_mutation_gate", {"killed": 1, "survived": 0, "threshold_pct": -1}, "threshold_pct")


def test_mutation_gate_truncates_the_score_so_it_fails_closed():
    # 17499 of 25000 detected = 69.996 %: truncated to 69.99, strictly below the 70 % threshold.
    result = ok("qa_mutation_gate", {"killed": 17499, "survived": 7501})
    assert result["score_pct"] == 69.99 and result["scored_mutants"] == 25000 and result["meets_threshold"] is False


def test_mutation_gate_meets_threshold_agrees_with_the_reported_score_pct():
    # prompts/A08-qa.md qa_mutation_gate OUTPUTS: meets_threshold is true when the reported score_pct (truncated to 2 places) >= threshold_pct.
    result = ok("qa_mutation_gate", {"killed": 17499, "survived": 7501})
    assert result["meets_threshold"] is (result["score_pct"] >= result["threshold_pct"])


# ---- qa_runtime_p95 ------------------------------------------------------------------------------


def test_runtime_p95_nearest_rank_exact_output():
    result = ok("qa_runtime_p95", {"durations_s": list(range(1, 21))})
    assert result == {"state": "SUCCESS", "sample_count": 20, "p95_s": 19.0, "mean_s": 10.5, "max_s": 20.0,
                      "budget_s": 2700.0, "within_budget": True}


def test_runtime_p95_sorts_unsorted_input_and_leaves_the_payload_alone():
    payload = {"durations_s": [30, 10, 20]}
    result = ok("qa_runtime_p95", payload)
    assert result["p95_s"] == 30.0 and result["max_s"] == 30.0 and result["mean_s"] == 20.0
    assert payload == {"durations_s": [30, 10, 20]}


@pytest.mark.parametrize("count,expected_rank", [(1, 1), (2, 2), (20, 19), (21, 20), (100, 95), (101, 96), (200, 190)])
def test_runtime_p95_rank_is_ceil_of_ninety_five_percent(count, expected_rank):
    result = ok("qa_runtime_p95", {"durations_s": list(range(1, count + 1))})
    assert result["p95_s"] == float(expected_rank) and result["sample_count"] == count


def test_runtime_p95_single_sample_and_mean_rounding():
    single = ok("qa_runtime_p95", {"durations_s": [5.5]})
    assert (single["p95_s"], single["mean_s"], single["max_s"]) == (5.5, 5.5, 5.5)
    assert ok("qa_runtime_p95", {"durations_s": [1, 1, 2]})["mean_s"] == 1.333


def test_runtime_p95_budget_boundary_is_inclusive_and_default_is_forty_five_minutes():
    at_default = ok("qa_runtime_p95", {"durations_s": [2700]})
    over_default = ok("qa_runtime_p95", {"durations_s": [2700.5]})
    assert at_default["within_budget"] is True and at_default["budget_s"] == 2700.0
    assert over_default["within_budget"] is False
    custom = ok("qa_runtime_p95", {"durations_s": [10, 20], "budget_s": 20})
    tighter = ok("qa_runtime_p95", {"durations_s": [10, 20], "budget_s": 19.99})
    assert custom["within_budget"] is True and custom["budget_s"] == 20.0 and isinstance(custom["budget_s"], float)
    assert tighter["within_budget"] is False


def test_runtime_p95_one_outlier_below_the_rank_does_not_trip_the_budget():
    # 20 samples: rank 19, so the single slowest run is excluded from p95 but visible in max_s.
    result = ok("qa_runtime_p95", {"durations_s": [60] * 19 + [9000]})
    assert result["p95_s"] == 60.0 and result["max_s"] == 9000.0 and result["within_budget"] is True


def test_runtime_p95_rejects_malformed_and_oversized_input():
    bad("qa_runtime_p95", {}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": []}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [1, -0.5]}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [1, "2"]}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [True]}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [None]}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [1], "budget_s": 0}, "budget_s")
    bad("qa_runtime_p95", {"durations_s": [1], "budget_s": -5}, "budget_s")
    bad("qa_runtime_p95", {"durations_s": [1] * 5001}, "durations_s")
    assert ok("qa_runtime_p95", {"durations_s": [1] * 5000})["sample_count"] == 5000


def test_runtime_p95_and_other_numeric_inputs_reject_non_finite_values():
    # json.loads accepts the NaN/Infinity tokens, so a payload can carry them; the output must stay valid JSON.
    inf = float("inf")
    bad("qa_runtime_p95", {"durations_s": [1.0, NAN, 3.0]}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [1.0, inf]}, "durations_s")
    bad("qa_runtime_p95", {"durations_s": [1.0], "budget_s": NAN}, "budget_s")
    bad("qa_runtime_p95", {"durations_s": [1.0], "budget_s": inf}, "budget_s")
    bad("qa_mutation_gate", {"killed": 1, "survived": 1, "threshold_pct": NAN}, "threshold_pct")
    bad("qa_skip_ratio", {"tests": [_test("a", "passed")], "max_skip_pct": NAN}, "max_skip_pct")
    bad("qa_defect_escape_rate", {"defects": _defects(1, 0), "escape_max_pct": NAN}, "escape_max_pct")
    json.dumps(ok("qa_runtime_p95", {"durations_s": [1.0, 3.0]}), allow_nan=False)


# ---- qa_skip_ratio -------------------------------------------------------------------------------


def _test(name, status, reason=None):
    item = {"name": name, "status": status}
    if reason is not None:
        item["reason"] = reason
    return item


def test_skip_ratio_exact_output_with_unexplained_skips():
    result = ok("qa_skip_ratio", {"tests": [
        _test("p", "passed"), _test("f", "failed"), _test("s1", "skipped", "needs docker"), _test("s2", "skipped"),
    ], "max_skip_pct": 50})
    assert result == {"state": "SUCCESS", "total": 4, "skipped": 2, "skip_pct": 50.0, "unexplained": ["s2"],
                      "within_limit": True}


def test_skip_ratio_limit_is_inclusive_and_strict_above():
    tests = [_test("s", "skipped", "r"), _test("p", "passed")]
    assert ok("qa_skip_ratio", {"tests": tests, "max_skip_pct": 50})["within_limit"] is True
    assert ok("qa_skip_ratio", {"tests": tests, "max_skip_pct": 49.99})["within_limit"] is False


def test_skip_ratio_blank_and_empty_reasons_are_unexplained_but_reasons_on_other_statuses_are_ignored():
    result = ok("qa_skip_ratio", {"tests": [
        _test("blank", "skipped", "   \t"), _test("empty", "skipped", ""), _test("good", "skipped", " why "),
        _test("passed_with_reason", "passed", "irrelevant"), _test("failed_without", "failed"),
    ], "max_skip_pct": 100})
    assert result["unexplained"] == ["blank", "empty"]
    assert result["skipped"] == 3 and result["skip_pct"] == 60.0


def test_skip_ratio_no_skips_and_all_skips():
    none = ok("qa_skip_ratio", {"tests": [_test("p", "passed"), _test("f", "failed")], "max_skip_pct": 0})
    assert (none["skipped"], none["skip_pct"], none["unexplained"], none["within_limit"]) == (0, 0.0, [], True)
    every = ok("qa_skip_ratio", {"tests": [_test("a", "skipped", "r"), _test("b", "skipped", "r")], "max_skip_pct": 100})
    assert every["skip_pct"] == 100.0 and every["within_limit"] is True
    assert ok("qa_skip_ratio", {"tests": [_test("a", "skipped", "r")], "max_skip_pct": 99.9})["within_limit"] is False


def test_skip_ratio_limit_uses_the_exact_share_while_the_report_is_rounded():
    tests = [_test("a", "skipped", "r"), _test("b", "passed"), _test("c", "passed")]  # 33.333...%
    exact = ok("qa_skip_ratio", {"tests": tests, "max_skip_pct": 33.33})
    assert exact["skip_pct"] == 33.33 and exact["within_limit"] is False
    assert ok("qa_skip_ratio", {"tests": tests, "max_skip_pct": 34})["within_limit"] is True


def test_skip_ratio_duplicate_name_is_invalid_and_named():
    result = bad("qa_skip_ratio", {"tests": [_test("same", "passed"), _test("same", "skipped")], "max_skip_pct": 5}, "tests")
    assert "same" in result["message"]


def test_skip_ratio_rejects_malformed_and_oversized_input():
    bad("qa_skip_ratio", {"tests": [_test("t", "passed")]}, "max_skip_pct")
    bad("qa_skip_ratio", {"max_skip_pct": 5}, "tests")
    bad("qa_skip_ratio", {"tests": [], "max_skip_pct": 5}, "tests")
    bad("qa_skip_ratio", {"tests": [_test("t", "error")], "max_skip_pct": 5}, "tests")
    bad("qa_skip_ratio", {"tests": [_test("", "passed")], "max_skip_pct": 5}, "tests")
    bad("qa_skip_ratio", {"tests": [{"name": "t", "status": "skipped", "reason": None}], "max_skip_pct": 5}, "tests")
    bad("qa_skip_ratio", {"tests": [_test("t", "skipped", "r" * 301)], "max_skip_pct": 5}, "tests")
    bad("qa_skip_ratio", {"tests": [_test("t", "passed")], "max_skip_pct": 100.1}, "max_skip_pct")
    bad("qa_skip_ratio", {"tests": [_test("t", "passed")], "max_skip_pct": -1}, "max_skip_pct")
    bad("qa_skip_ratio", {"tests": [_test(f"t{i}", "passed") for i in range(10001)], "max_skip_pct": 5}, "tests")


# ---- qa_defect_escape_rate -----------------------------------------------------------------------


def _defects(pre, prod):
    return [{"id": f"P{i}", "stage": "pre_prod"} for i in range(pre)] + [{"id": f"X{i}", "stage": "production"} for i in range(prod)]


def test_defect_escape_rate_exact_output_at_the_five_percent_edge():
    result = ok("qa_defect_escape_rate", {"defects": _defects(19, 1)})
    assert result == {
        "state": "SUCCESS", "total": 20, "pre_prod": 19, "escaped": 1, "detection_pct": 95.0, "escape_pct": 5.0,
        "escaped_ids": ["X0"], "meets_detection": True, "meets_escape": False,
    }


def test_defect_escape_rate_passes_both_targets_below_the_limits():
    result = ok("qa_defect_escape_rate", {"defects": _defects(96, 4)})
    assert (result["detection_pct"], result["escape_pct"]) == (96.0, 4.0)
    assert result["meets_detection"] is True and result["meets_escape"] is True


def test_defect_escape_rate_detection_is_inclusive_at_ninety():
    result = ok("qa_defect_escape_rate", {"defects": _defects(9, 1)})
    assert result["detection_pct"] == 90.0 and result["meets_detection"] is True
    assert result["escape_pct"] == 10.0 and result["meets_escape"] is False
    just_below = ok("qa_defect_escape_rate", {"defects": _defects(89, 11)})
    assert just_below["meets_detection"] is False


def test_defect_escape_rate_custom_targets():
    payload = {"defects": _defects(95, 5), "detection_min_pct": 99, "escape_max_pct": 10}
    result = ok("qa_defect_escape_rate", payload)
    assert result["meets_detection"] is False and result["meets_escape"] is True
    loose = ok("qa_defect_escape_rate", {**payload, "detection_min_pct": 95})
    assert loose["meets_detection"] is True


def test_defect_escape_rate_all_pre_prod_and_all_production():
    clean = ok("qa_defect_escape_rate", {"defects": _defects(3, 0)})
    assert (clean["escaped"], clean["escaped_ids"], clean["detection_pct"], clean["escape_pct"]) == (0, [], 100.0, 0.0)
    assert clean["meets_detection"] is True and clean["meets_escape"] is True
    disaster = ok("qa_defect_escape_rate", {"defects": _defects(0, 3)})
    assert (disaster["pre_prod"], disaster["detection_pct"], disaster["escape_pct"]) == (0, 0.0, 100.0)
    assert disaster["escaped_ids"] == ["X0", "X1", "X2"]
    assert disaster["meets_detection"] is False and disaster["meets_escape"] is False


def test_defect_escape_rate_escape_limit_is_strictly_below_even_at_zero():
    # prompt: "escape share must be strictly below escape_max_pct"; so a limit of 0 can never be met.
    result = ok("qa_defect_escape_rate", {"defects": _defects(3, 0), "escape_max_pct": 0})
    assert result["escape_pct"] == 0.0 and result["meets_escape"] is False
    assert ok("qa_defect_escape_rate", {"defects": _defects(3, 0), "escape_max_pct": 0.01})["meets_escape"] is True


def test_defect_escape_rate_rounds_and_keeps_escaped_id_order():
    result = ok("qa_defect_escape_rate", {"defects": [
        {"id": "z", "stage": "production"}, {"id": "a", "stage": "pre_prod"}, {"id": "m", "stage": "production"},
    ]})
    assert result["detection_pct"] == 33.33 and result["escape_pct"] == 66.67
    assert result["escaped_ids"] == ["z", "m"]


def test_defect_escape_rate_duplicate_id_is_invalid_and_named():
    result = bad("qa_defect_escape_rate", {"defects": [{"id": "D1", "stage": "pre_prod"}, {"id": "D1", "stage": "production"}]}, "defects")
    assert "D1" in result["message"]


def test_defect_escape_rate_rejects_malformed_and_oversized_input():
    bad("qa_defect_escape_rate", {}, "defects")
    bad("qa_defect_escape_rate", {"defects": []}, "defects")
    bad("qa_defect_escape_rate", {"defects": [{"id": "d", "stage": "staging"}]}, "defects")
    bad("qa_defect_escape_rate", {"defects": [{"id": "d"}]}, "defects")
    bad("qa_defect_escape_rate", {"defects": [{"id": "", "stage": "pre_prod"}]}, "defects")
    bad("qa_defect_escape_rate", {"defects": [{"id": "d" * 65, "stage": "pre_prod"}]}, "defects")
    bad("qa_defect_escape_rate", {"defects": _defects(1, 0), "detection_min_pct": 101}, "detection_min_pct")
    bad("qa_defect_escape_rate", {"defects": _defects(1, 0), "escape_max_pct": -1}, "escape_max_pct")
    bad("qa_defect_escape_rate", {"defects": _defects(10001, 0)}, "defects")


# ---- qa_retry_budget -----------------------------------------------------------------------------


def _run(test, *attempts):
    return {"test": test, "attempts": list(attempts)}


def test_retry_budget_classifies_reruns_rescues_and_unresolved_exactly():
    result = ok("qa_retry_budget", {"max_reruns": 1, "runs": [
        _run("clean", "pass"),
        _run("rescued_once", "fail", "pass"),
        _run("rescued_twice", "fail", "fail", "pass"),
        _run("never_green", "fail", "fail"),
        _run("flipped", "pass", "fail", "pass"),
    ]})
    assert result == {
        "state": "SUCCESS", "evaluated": 5, "total_reruns": 6, "over_budget": ["rescued_twice", "flipped"],
        "rescued": ["rescued_once", "rescued_twice"], "unresolved": ["never_green"],
    }


def test_retry_budget_limit_is_inclusive_and_zero_budget_flags_any_rerun():
    at_limit = ok("qa_retry_budget", {"max_reruns": 2, "runs": [_run("t", "fail", "fail", "pass")]})
    assert at_limit["over_budget"] == [] and at_limit["total_reruns"] == 2
    zero = ok("qa_retry_budget", {"max_reruns": 0, "runs": [_run("once", "pass"), _run("twice", "fail", "pass")]})
    assert zero["over_budget"] == ["twice"] and zero["total_reruns"] == 1


def test_retry_budget_a_first_attempt_pass_is_never_rescued_or_unresolved():
    result = ok("qa_retry_budget", {"max_reruns": 3, "runs": [_run("a", "pass", "fail"), _run("b", "pass")]})
    assert result["rescued"] == [] and result["unresolved"] == [] and result["total_reruns"] == 1


def test_retry_budget_single_failed_attempt_is_unresolved_with_zero_reruns():
    result = ok("qa_retry_budget", {"max_reruns": 0, "runs": [_run("t", "fail")]})
    assert result["unresolved"] == ["t"] and result["total_reruns"] == 0 and result["over_budget"] == []
    assert result["rescued"] == []


def test_retry_budget_boundaries_on_attempt_count_and_budget():
    result = ok("qa_retry_budget", {"max_reruns": 10, "runs": [_run("t", *(["fail"] * 20 + ["pass"]))]})
    assert result["total_reruns"] == 20 and result["over_budget"] == ["t"] and result["rescued"] == ["t"]
    bad("qa_retry_budget", {"max_reruns": 1, "runs": [_run("t", *(["fail"] * 22))]}, "runs")


def test_retry_budget_duplicate_test_is_invalid_and_named():
    result = bad("qa_retry_budget", {"max_reruns": 1, "runs": [_run("dup", "pass"), _run("dup", "fail")]}, "runs")
    assert "dup" in result["message"]


def test_retry_budget_rejects_malformed_input():
    bad("qa_retry_budget", {"max_reruns": 1}, "runs")
    bad("qa_retry_budget", {"runs": [_run("t", "pass")]}, "max_reruns")
    bad("qa_retry_budget", {"runs": [], "max_reruns": 1}, "runs")
    bad("qa_retry_budget", {"runs": [_run("t")], "max_reruns": 1}, "runs")
    bad("qa_retry_budget", {"runs": [_run("t", "skip")], "max_reruns": 1}, "runs")
    bad("qa_retry_budget", {"runs": [_run("", "pass")], "max_reruns": 1}, "runs")
    bad("qa_retry_budget", {"runs": [_run("t", "pass")], "max_reruns": -1}, "max_reruns")
    bad("qa_retry_budget", {"runs": [_run("t", "pass")], "max_reruns": 11}, "max_reruns")
    bad("qa_retry_budget", {"runs": [_run("t", "pass")], "max_reruns": 1.5}, "max_reruns")
    bad("qa_retry_budget", {"runs": [_run(f"t{i}", "pass") for i in range(5001)], "max_reruns": 1}, "runs")


# ---- qa_name_convention --------------------------------------------------------------------------


def test_name_convention_reports_each_violation_in_input_order():
    result = ok("qa_name_convention", {"prefix": "test_", "names": ["test_login", "test_Logout", "login", "test_a-b"]})
    assert result == {
        "state": "SUCCESS", "checked": 4, "conforming": False,
        "violations": ["test_Logout: not snake_case", "login: missing prefix test_", "test_a-b: not snake_case"],
    }


def test_name_convention_conforming_names_pass_including_digits_and_bare_prefix():
    result = ok("qa_name_convention", {"prefix": "test_", "names": ["test_", "test_1", "test_a_b_2", "test_ok"]})
    assert result == {"state": "SUCCESS", "checked": 4, "violations": [], "conforming": True}


def test_name_convention_length_boundary_default_eighty_and_custom():
    exact = "test_" + "a" * 75
    over = "test_" + "a" * 76
    result = ok("qa_name_convention", {"prefix": "test_", "names": [exact, over]})
    assert result["violations"] == [f"{over}: longer than 80"]
    short = ok("qa_name_convention", {"prefix": "test_", "names": ["test_abcde", "test_abcdef"], "max_length": 10})
    assert short["violations"] == ["test_abcdef: longer than 10"]


def test_name_convention_duplicates_are_reported_once_per_extra_occurrence():
    result = ok("qa_name_convention", {"prefix": "test_", "names": ["test_a", "test_a", "test_b", "test_a"]})
    assert result["violations"] == ["test_a: duplicate name", "test_a: duplicate name"]
    assert result["checked"] == 4 and result["conforming"] is False


def test_name_convention_one_name_can_break_every_rule_in_a_fixed_order():
    result = ok("qa_name_convention", {"prefix": "test_", "names": ["Bad"], "max_length": 2})
    assert result["violations"] == ["Bad: missing prefix test_", "Bad: not snake_case", "Bad: longer than 2"]


def test_name_convention_prefix_is_literal_and_case_sensitive():
    result = ok("qa_name_convention", {"prefix": "test_", "names": ["Test_a", "tests_a", "atest_a"]})
    assert result["violations"] == [
        "Test_a: missing prefix test_", "Test_a: not snake_case", "tests_a: missing prefix test_",
        "atest_a: missing prefix test_",
    ]


def test_name_convention_rejects_dots_spaces_and_trailing_newline_as_not_snake_case():
    result = ok("qa_name_convention", {"prefix": "test_", "names": ["test_a.b", "test_a b", "test_a\n", "test_é"]})
    assert result["violations"] == [f"{n}: not snake_case" for n in ["test_a.b", "test_a b", "test_a\n", "test_é"]]


def test_name_convention_rejects_malformed_and_oversized_input():
    bad("qa_name_convention", {"prefix": "test_"}, "names")
    bad("qa_name_convention", {"names": ["test_a"]}, "prefix")
    bad("qa_name_convention", {"names": [], "prefix": "test_"}, "names")
    bad("qa_name_convention", {"names": ["test_a"], "prefix": ""}, "prefix")
    bad("qa_name_convention", {"names": ["test_a"], "prefix": "p" * 41}, "prefix")
    bad("qa_name_convention", {"names": [""], "prefix": "test_"}, "names")
    bad("qa_name_convention", {"names": ["test_" + "a" * 296], "prefix": "test_"}, "names")
    bad("qa_name_convention", {"names": [5], "prefix": "test_"}, "names")
    bad("qa_name_convention", {"names": ["test_a"], "prefix": "test_", "max_length": 0}, "max_length")
    bad("qa_name_convention", {"names": ["test_a"], "prefix": "test_", "max_length": 301}, "max_length")
    bad("qa_name_convention", {"names": [f"test_{i}" for i in range(5001)], "prefix": "test_"}, "names")


# ---- qa_runner_detect ----------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """An empty target workspace; qa_runner_detect scans the current directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _which(monkeypatch, *available):
    gate = _live("swarm.tools.support").script("qa_gate")
    monkeypatch.setattr(gate, "which", lambda name: f"/usr/bin/{name}" if name in available else None)


def test_runner_detect_empty_workspace_has_no_runners(workspace, monkeypatch):
    _which(monkeypatch, "npm", "go", "cargo")
    assert ok("qa_runner_detect", {"scan": True}) == {"state": "SUCCESS", "runner_count": 0, "has_pytest": False}


@pytest.mark.parametrize("marker", ["pyproject.toml", "pytest.ini", "tests/test_sample.py"])
def test_runner_detect_pytest_markers(workspace, monkeypatch, marker):
    _which(monkeypatch)
    path = workspace / marker
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    assert ok("qa_runner_detect", {"scan": True}) == {"state": "SUCCESS", "runner_count": 1, "has_pytest": True}


def test_runner_detect_ignores_test_files_that_do_not_match_the_pytest_glob(workspace, monkeypatch):
    _which(monkeypatch)
    (workspace / "tests").mkdir()
    (workspace / "tests" / "helper.py").write_text("", encoding="utf-8")
    (workspace / "test_top.py").write_text("", encoding="utf-8")
    assert ok("qa_runner_detect", {"scan": True})["runner_count"] == 0


def test_runner_detect_npm_needs_a_test_script_and_the_binary(workspace, monkeypatch):
    package = workspace / "package.json"
    package.write_text(json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    _which(monkeypatch, "npm")
    assert ok("qa_runner_detect", {"scan": True}) == {"state": "SUCCESS", "runner_count": 1, "has_pytest": False}
    _which(monkeypatch)
    assert ok("qa_runner_detect", {"scan": True})["runner_count"] == 0
    _which(monkeypatch, "npm")
    package.write_text(json.dumps({"scripts": {"build": "tsc"}}), encoding="utf-8")
    assert ok("qa_runner_detect", {"scan": True})["runner_count"] == 0
    package.write_text("{not json", encoding="utf-8")
    assert ok("qa_runner_detect", {"scan": True})["runner_count"] == 0


def test_runner_detect_counts_every_runner_and_flags_pytest_among_them(workspace, monkeypatch):
    _which(monkeypatch, "npm", "go", "cargo")
    (workspace / "pyproject.toml").write_text("", encoding="utf-8")
    (workspace / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    (workspace / "go.mod").write_text("module x\n", encoding="utf-8")
    (workspace / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert ok("qa_runner_detect", {"scan": True}) == {"state": "SUCCESS", "runner_count": 4, "has_pytest": True}


def test_runner_detect_go_and_cargo_need_their_binaries(workspace, monkeypatch):
    (workspace / "go.mod").write_text("module x\n", encoding="utf-8")
    (workspace / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    _which(monkeypatch, "go")
    assert ok("qa_runner_detect", {"scan": True}) == {"state": "SUCCESS", "runner_count": 1, "has_pytest": False}
    _which(monkeypatch, "cargo")
    assert ok("qa_runner_detect", {"scan": True})["runner_count"] == 1
    _which(monkeypatch)
    assert ok("qa_runner_detect", {"scan": True})["runner_count"] == 0


def test_runner_detect_rejects_a_false_or_non_boolean_scan(workspace):
    assert bad("qa_runner_detect", {"scan": False}, "scan")["message"] == "scan must be true"
    bad("qa_runner_detect", {"scan": "true"}, "scan")
    bad("qa_runner_detect", {"scan": 1}, "scan")
    bad("qa_runner_detect", {}, "scan")
    direct = _live("swarm.tools.batch_06_10").runner_detect({"scan": False})
    assert direct == {"state": "INVALID_INPUT", "field": "scan", "message": "scan must be true"}
