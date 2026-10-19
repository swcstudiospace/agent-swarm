"""Behaviour tests for the v2.1 A12 release-manager lane tools.

Covers rel_canary_steps, rel_canary_verdict, rel_rollback_trigger, rel_release_record_check,
rel_change_failure_rate, rel_deploy_frequency, rel_lead_time_p95 and rel_gate_order_check.
Every test asserts exact output values for specific inputs, through the registry call path
(`call_tool` as A12) and, for the pure handlers, by calling the handler function directly.
"""
import importlib
import json
import math
import time

import pytest

from swarm.tool_registry import call_tool

CALLER = "A12"
TOOLS = (
    "rel_canary_steps",
    "rel_canary_verdict",
    "rel_rollback_trigger",
    "rel_release_record_check",
    "rel_change_failure_rate",
    "rel_deploy_frequency",
    "rel_lead_time_p95",
    "rel_gate_order_check",
)


def run(name: str, payload: dict, caller: str = CALLER, **kwargs) -> dict:
    return call_tool(name, caller, payload, **kwargs)


def handlers():
    """The handler module resolved from the current module graph (conftest evicts swarm* between tests)."""
    return importlib.import_module("swarm.tools.a12_tools")


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    assert result["message"]


@pytest.mark.parametrize("name", TOOLS)
def test_only_the_release_manager_may_call_each_tool(name):
    result = run(name, {}, caller="A10")
    assert result["state"] == "UNAUTHORIZED"
    assert result["caller"] == "A10" and result["target"] == name


@pytest.mark.parametrize("name", TOOLS)
def test_empty_payload_names_the_first_required_field(name):
    result = run(name, {})
    assert result["state"] == "INVALID_INPUT" and result["field"]


# --- rel_canary_steps --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "risk, approval",
    [("low", "L2:auto"), ("medium", "L2:auto"), ("high", "L4:human-four-eyes")],
)
def test_canary_steps_are_the_fixed_ladder_and_only_high_risk_needs_four_eyes(risk, approval):
    assert run("rel_canary_steps", {"release_id": "REL-1", "risk": risk}) == {
        "state": "SUCCESS",
        "steps_pct": "5,25,50,100",
        "approval": approval,
    }


def test_canary_steps_do_not_depend_on_the_release_id():
    first = run("rel_canary_steps", {"release_id": "REL-1", "risk": "low"})
    second = run("rel_canary_steps", {"release_id": "a very different id", "risk": "low"})
    assert first == second


def test_canary_steps_reject_an_unknown_risk_class():
    result = run("rel_canary_steps", {"release_id": "REL-1", "risk": "critical"})
    invalid(result, "risk")
    assert "low" in result["message"] and "high" in result["message"]


def test_canary_steps_reject_an_empty_release_id():
    invalid(run("rel_canary_steps", {"release_id": "", "risk": "low"}), "release_id")


def test_canary_steps_reject_a_non_string_release_id_and_a_missing_risk():
    invalid(run("rel_canary_steps", {"release_id": 118, "risk": "low"}), "release_id")
    invalid(run("rel_canary_steps", {"release_id": "REL-1"}), "risk")


def test_canary_steps_reject_unknown_fields():
    invalid(run("rel_canary_steps", {"release_id": "REL-1", "risk": "low", "steps": [10]}), "steps")


def test_canary_steps_handler_called_directly_matches_the_registry_result():
    module = importlib.import_module("swarm.tools.batch_11_15")
    direct = module.canary_steps({"release_id": "REL-9", "risk": "high"})
    assert direct == run("rel_canary_steps", {"release_id": "REL-9", "risk": "high"})


# --- rel_canary_verdict ------------------------------------------------------------------------------------


def _verdict(**over) -> dict:
    payload = {
        "canary_error_rate": 0.003,
        "baseline_error_rate": 0.002,
        "error_rate_max": 0.005,
        "max_regression": 0.002,
        "soak_observed_min": 15,
        "soak_required_min": 15,
    }
    payload.update(over)
    return payload


def test_canary_verdict_promotes_the_documented_example_with_exact_regression():
    assert run("rel_canary_verdict", _verdict()) == {
        "state": "SUCCESS",
        "decision": "promote",
        "reason": "within_guardrails",
        "regression": 0.001,
    }


def test_canary_error_rate_over_the_ceiling_rolls_back_first():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.006, baseline_error_rate=0.006))
    assert (result["decision"], result["reason"]) == ("rollback", "error_rate_over_ceiling")
    assert result["regression"] == 0


def test_canary_error_rate_equal_to_the_ceiling_is_not_over_it():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.005, baseline_error_rate=0.005))
    assert (result["decision"], result["reason"]) == ("promote", "within_guardrails")


def test_regression_over_the_allowance_rolls_back():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.004, baseline_error_rate=0.001))
    assert (result["decision"], result["reason"], result["regression"]) == ("rollback", "regressed_over_baseline", 0.003)


def test_regression_exactly_equal_to_the_allowance_promotes_despite_float_noise():
    # 0.005 - 0.003 is 0.0020000000000000005 in binary floating point; the tool rounds to 6 places first
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.005, baseline_error_rate=0.003, max_regression=0.002))
    assert (result["decision"], result["regression"]) == ("promote", 0.002)


def test_canary_better_than_baseline_reports_a_negative_regression_and_promotes():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.001, baseline_error_rate=0.004))
    assert (result["decision"], result["regression"]) == ("promote", -0.003)


def test_short_soak_holds_when_the_error_checks_pass():
    result = run("rel_canary_verdict", _verdict(soak_observed_min=14.99))
    assert (result["decision"], result["reason"]) == ("hold", "soak_incomplete")


def test_soak_longer_than_required_promotes():
    assert run("rel_canary_verdict", _verdict(soak_observed_min=240))["decision"] == "promote"


def test_error_ceiling_wins_over_regression_and_soak():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.5, baseline_error_rate=0.0, soak_observed_min=0))
    assert result["reason"] == "error_rate_over_ceiling"


def test_regression_wins_over_an_incomplete_soak():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.004, baseline_error_rate=0.001, soak_observed_min=0))
    assert (result["decision"], result["reason"]) == ("rollback", "regressed_over_baseline")


def test_zero_required_soak_promotes_immediately():
    assert run("rel_canary_verdict", _verdict(soak_observed_min=0, soak_required_min=0))["decision"] == "promote"


def test_zero_ceiling_rolls_back_on_any_error():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.000001, baseline_error_rate=0, error_rate_max=0, max_regression=1))
    assert result["reason"] == "error_rate_over_ceiling"


def test_regression_is_rounded_to_six_decimals():
    result = run("rel_canary_verdict", _verdict(canary_error_rate=0.0012345678, baseline_error_rate=0.0, error_rate_max=1, max_regression=1))
    assert result["regression"] == 0.001235


@pytest.mark.parametrize(
    "field, value",
    [
        ("canary_error_rate", 1.01),
        ("canary_error_rate", -0.1),
        ("baseline_error_rate", 2),
        ("error_rate_max", "0.005"),
        ("max_regression", True),
        ("soak_observed_min", -1),
        ("soak_required_min", None),
    ],
)
def test_canary_verdict_rejects_out_of_range_or_mistyped_numbers(field, value):
    invalid(run("rel_canary_verdict", _verdict(**{field: value})), field)


def test_canary_verdict_rejects_each_missing_field():
    for field in _verdict():
        payload = _verdict()
        del payload[field]
        invalid(run("rel_canary_verdict", payload), field)


def test_canary_verdict_handler_called_directly():
    assert handlers().canary_verdict(_verdict(soak_observed_min=1)) == {
        "state": "SUCCESS",
        "decision": "hold",
        "reason": "soak_incomplete",
        "regression": 0.001,
    }


def test_a_nan_canary_error_rate_is_invalid_input():
    # the registry refuses NaN/Infinity for every number field, so the comparison never sees it (no fail-open promote)
    result = run("rel_canary_verdict", _verdict(canary_error_rate=math.nan))
    invalid(result, "canary_error_rate")
    assert "finite" in result["message"]


def test_a_nan_soak_reading_is_invalid_input():
    result = run("rel_canary_verdict", _verdict(soak_observed_min=math.nan))
    invalid(result, "soak_observed_min")
    assert "finite" in result["message"]


# --- rel_rollback_trigger ----------------------------------------------------------------------------------


def _trigger(rates, limit=2.0, needed=2) -> dict:
    return {"burn_rates": rates, "slo_burn_max": limit, "consecutive_required": needed}


def test_rollback_trigger_documented_example():
    result = run("rel_rollback_trigger", _trigger([0.5, 2.5, 3.0, 0.7, 2.2]))
    assert result == {"state": "SUCCESS", "triggered": True, "breach_index": 2, "max_burn": 3.0}


def test_a_single_breach_below_the_required_run_does_not_trigger():
    result = run("rel_rollback_trigger", _trigger([0.5, 2.5, 0.7, 2.2, 0.1]))
    assert result == {"state": "SUCCESS", "triggered": False, "breach_index": -1, "max_burn": 2.5}


def test_a_value_equal_to_the_limit_is_not_a_breach():
    result = run("rel_rollback_trigger", _trigger([2.0, 2.0, 2.0], needed=1))
    assert result["triggered"] is False and result["breach_index"] == -1


def test_one_required_sample_triggers_on_the_first_exceedance():
    result = run("rel_rollback_trigger", _trigger([1.0, 2.0001, 9.0], needed=1))
    assert (result["triggered"], result["breach_index"], result["max_burn"]) == (True, 1, 9.0)


def test_a_non_breaching_sample_resets_the_run():
    result = run("rel_rollback_trigger", _trigger([3, 3, 1, 3, 3, 3], needed=3))
    assert (result["triggered"], result["breach_index"]) == (True, 5)


def test_the_breach_index_is_where_the_first_complete_run_ends():
    result = run("rel_rollback_trigger", _trigger([3, 3, 3, 3], needed=2))
    assert result["breach_index"] == 1


def test_a_run_longer_than_the_series_never_triggers():
    result = run("rel_rollback_trigger", _trigger([5, 5], needed=3))
    assert (result["triggered"], result["breach_index"]) == (False, -1)


def test_a_single_sample_series_is_accepted():
    assert run("rel_rollback_trigger", _trigger([4.0], needed=1)) == {
        "state": "SUCCESS", "triggered": True, "breach_index": 0, "max_burn": 4.0,
    }


def test_max_burn_is_reported_even_when_it_is_the_last_sample_after_the_trigger():
    result = run("rel_rollback_trigger", _trigger([3, 3, 99]))
    assert (result["breach_index"], result["max_burn"]) == (1, 99)


def test_rollback_trigger_scans_a_ten_thousand_sample_series_quickly():
    rates = [0.1] * 9999 + [5.0]
    started = time.monotonic()
    result = run("rel_rollback_trigger", _trigger(rates, needed=1))
    assert (result["triggered"], result["breach_index"]) == (True, 9999)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "payload, field",
    [
        (_trigger([]), "burn_rates"),
        (_trigger([1.0, -0.5]), "burn_rates"),
        (_trigger([1.0, "high"]), "burn_rates"),
        (_trigger([True]), "burn_rates"),
        (_trigger([1.0], limit=0), "slo_burn_max"),
        (_trigger([1.0], limit=-2), "slo_burn_max"),
        (_trigger([1.0], needed=0), "consecutive_required"),
        (_trigger([1.0], needed=2.5), "consecutive_required"),
        (_trigger([1.0], needed=10001), "consecutive_required"),
        ({"slo_burn_max": 2.0, "consecutive_required": 1}, "burn_rates"),
    ],
)
def test_rollback_trigger_rejects_malformed_input_and_names_the_field(payload, field):
    invalid(run("rel_rollback_trigger", payload), field)


def test_rollback_trigger_rejects_more_than_ten_thousand_samples():
    invalid(run("rel_rollback_trigger", _trigger([0.0] * 10001)), "burn_rates")


def test_rollback_trigger_handler_called_directly():
    assert handlers().rollback_trigger(_trigger([2.5, 2.5])) == {
        "state": "SUCCESS", "triggered": True, "breach_index": 1, "max_burn": 2.5,
    }


def test_nan_burn_samples_are_invalid_input_not_healthy():
    result = run("rel_rollback_trigger", _trigger([math.nan, math.nan, math.nan], needed=1))
    invalid(result, "burn_rates")
    assert "finite" in result["message"]


# --- rel_release_record_check ------------------------------------------------------------------------------

FULL = {
    "release_id": "REL-118",
    "promoted_at": "2026-10-14T09:41Z",
    "artifacts": ["oci://registry.example/app@sha256:abc123"],
    "verdicts": {"review": "pass", "quality": "pass", "security": "pass"},
}


def _record(record, fields) -> dict:
    return {"record": record, "required_fields": fields}


def test_a_complete_record_passes_the_documented_example():
    result = run("rel_release_record_check", _record(FULL, ["release_id", "promoted_at", "artifacts", "verdicts"]))
    assert result == {"state": "SUCCESS", "complete": True, "missing": "", "checked": 4}


def test_an_absent_field_is_listed_as_missing():
    record = {k: v for k, v in FULL.items() if k != "promoted_at"}
    result = run("rel_release_record_check", _record(record, ["release_id", "promoted_at"]))
    assert (result["complete"], result["missing"], result["checked"]) == (False, "promoted_at", 2)


@pytest.mark.parametrize("empty", [None, "", "   \n\t", [], {}])
def test_null_blank_and_empty_containers_count_as_missing(empty):
    result = run("rel_release_record_check", _record({"release_id": "R", "x": empty}, ["release_id", "x"]))
    assert (result["complete"], result["missing"]) == (False, "x")


@pytest.mark.parametrize("present", [0, 0.0, False, "0", ["x"], {"k": 1}, " a "])
def test_zero_false_and_non_empty_values_are_present(present):
    result = run("rel_release_record_check", _record({"x": present}, ["x"]))
    assert (result["complete"], result["missing"]) == (True, "")


def test_missing_fields_are_reported_in_the_required_order():
    result = run("rel_release_record_check", _record({"b": "x"}, ["z", "b", "a", "m"]))
    assert result["missing"] == "z,a,m" and result["checked"] == 4


def test_duplicate_required_fields_are_checked_and_reported_once():
    result = run("rel_release_record_check", _record({}, ["a", "b", "a", "b", "a"]))
    assert (result["missing"], result["checked"]) == ("a,b", 2)


def test_extra_record_fields_are_ignored():
    result = run("rel_release_record_check", _record({"a": 1, "junk": None}, ["a"]))
    assert result["complete"] is True and result["checked"] == 1


def test_required_field_names_match_case_sensitively():
    result = run("rel_release_record_check", _record({"Release_Id": "R"}, ["release_id"]))
    assert result["missing"] == "release_id"


def test_nested_empty_values_are_not_inspected():
    # only the top-level value is judged: a dict holding an empty string is still non-empty
    result = run("rel_release_record_check", _record({"verdicts": {"review": ""}}, ["verdicts"]))
    assert result["complete"] is True


@pytest.mark.parametrize(
    "payload, field",
    [
        (_record([], ["a"]), "record"),
        (_record("release", ["a"]), "record"),
        (_record({}, []), "required_fields"),
        (_record({}, [""]), "required_fields"),
        (_record({}, ["x" * 65]), "required_fields"),
        (_record({}, [1]), "required_fields"),
        ({"record": {}}, "required_fields"),
        ({"required_fields": ["a"]}, "record"),
    ],
)
def test_record_check_rejects_malformed_input_and_names_the_field(payload, field):
    invalid(run("rel_release_record_check", payload), field)


def test_record_check_rejects_more_than_a_hundred_required_fields():
    invalid(run("rel_release_record_check", _record({}, [f"f{n}" for n in range(101)])), "required_fields")


def test_record_check_accepts_exactly_a_hundred_required_fields():
    result = run("rel_release_record_check", _record({f"f{n}": n for n in range(100)}, [f"f{n}" for n in range(100)]))
    assert (result["complete"], result["checked"]) == (True, 100)


def test_record_check_handler_called_directly():
    assert handlers().release_record_check(_record({"a": ""}, ["a", "b"])) == {
        "state": "SUCCESS", "complete": False, "missing": "a,b", "checked": 2,
    }


# --- rel_change_failure_rate -------------------------------------------------------------------------------


def test_change_failure_rate_documented_example():
    result = run("rel_change_failure_rate", {"outcomes": [False, False, True, False]})
    assert result == {"state": "SUCCESS", "total": 4, "failed": 1, "rate": 0.25}


def test_all_successful_changes_have_a_zero_rate():
    assert run("rel_change_failure_rate", {"outcomes": [False] * 7}) == {"state": "SUCCESS", "total": 7, "failed": 0, "rate": 0.0}


def test_all_failed_changes_have_a_rate_of_one():
    assert run("rel_change_failure_rate", {"outcomes": [True, True]}) == {"state": "SUCCESS", "total": 2, "failed": 2, "rate": 1.0}


@pytest.mark.parametrize("failed, total, rate", [(1, 3, 0.3333), (2, 3, 0.6667), (1, 7, 0.1429), (1, 8, 0.125)])
def test_rate_is_rounded_to_four_decimals(failed, total, rate):
    outcomes = [True] * failed + [False] * (total - failed)
    assert run("rel_change_failure_rate", {"outcomes": outcomes})["rate"] == rate


def test_a_single_outcome_is_accepted():
    assert run("rel_change_failure_rate", {"outcomes": [True]})["rate"] == 1.0


def test_order_of_outcomes_does_not_matter():
    first = run("rel_change_failure_rate", {"outcomes": [True, False, False]})
    second = run("rel_change_failure_rate", {"outcomes": [False, False, True]})
    assert first == second


def test_the_ten_percent_target_boundary_is_reported_exactly():
    result = run("rel_change_failure_rate", {"outcomes": [True] + [False] * 9})
    assert result["rate"] == 0.1 and result["failed"] == 1 and result["total"] == 10


@pytest.mark.parametrize(
    "outcomes",
    [[], [1, 0], ["true"], [None], [True, 0], "yes"],
)
def test_change_failure_rate_rejects_empty_or_non_boolean_outcomes(outcomes):
    invalid(run("rel_change_failure_rate", {"outcomes": outcomes}), "outcomes")


def test_change_failure_rate_rejects_more_than_ten_thousand_outcomes():
    invalid(run("rel_change_failure_rate", {"outcomes": [False] * 10001}), "outcomes")


def test_change_failure_rate_handles_ten_thousand_outcomes():
    result = run("rel_change_failure_rate", {"outcomes": [True] * 2500 + [False] * 7500})
    assert (result["total"], result["failed"], result["rate"]) == (10000, 2500, 0.25)


def test_change_failure_rate_handler_called_directly():
    assert handlers().change_failure_rate({"outcomes": [True, False, False]}) == {
        "state": "SUCCESS", "total": 3, "failed": 1, "rate": 0.3333,
    }


# --- rel_deploy_frequency ----------------------------------------------------------------------------------


def _days(*days) -> dict:
    return {"deploy_days": list(days)}


def test_deploy_frequency_documented_example():
    result = run("rel_deploy_frequency", _days("2026-10-01", "2026-10-03", "2026-10-03", "2026-10-08"))
    assert result == {"state": "SUCCESS", "deploys": 4, "active_days": 3, "span_days": 8, "per_week": 3.5}


def test_one_deploy_spans_one_day_and_counts_as_seven_a_week():
    assert run("rel_deploy_frequency", _days("2026-10-01")) == {
        "state": "SUCCESS", "deploys": 1, "active_days": 1, "span_days": 1, "per_week": 7.0,
    }


def test_input_order_does_not_change_the_span():
    ordered = run("rel_deploy_frequency", _days("2026-10-01", "2026-10-04", "2026-10-08"))
    shuffled = run("rel_deploy_frequency", _days("2026-10-08", "2026-10-01", "2026-10-04"))
    assert ordered == shuffled and ordered["span_days"] == 8


def test_repeated_dates_count_as_separate_deploys_on_a_single_day():
    result = run("rel_deploy_frequency", _days("2026-10-01", "2026-10-01", "2026-10-01"))
    assert (result["deploys"], result["active_days"], result["span_days"], result["per_week"]) == (3, 1, 1, 21.0)


def test_span_counts_both_end_days_across_a_month_boundary():
    result = run("rel_deploy_frequency", _days("2026-09-28", "2026-10-04"))
    assert (result["span_days"], result["per_week"]) == (7, 2.0)


def test_span_crosses_a_leap_day_in_a_leap_year():
    result = run("rel_deploy_frequency", _days("2028-02-28", "2028-03-01"))
    assert result["span_days"] == 3  # 28, 29 and 1


def test_span_crosses_a_year_boundary():
    result = run("rel_deploy_frequency", _days("2025-12-31", "2026-01-01"))
    assert result["span_days"] == 2 and result["per_week"] == 7.0


def test_one_deploy_a_day_for_a_week_is_seven_a_week():
    week = [f"2026-10-{day:02d}" for day in range(1, 8)]
    result = run("rel_deploy_frequency", _days(*week))
    assert (result["deploys"], result["span_days"], result["per_week"]) == (7, 7, 7.0)


def test_per_week_is_rounded_to_two_decimals():
    result = run("rel_deploy_frequency", _days("2026-10-01", "2026-10-09"))  # 2 deploys over 9 days
    assert result["per_week"] == round(2 / 9 * 7, 2) == 1.56


@pytest.mark.parametrize(
    "bad",
    ["2026-02-30", "2026-13-01", "2026-00-10", "not-a-date", "2026/10/01", "2026-10-1x", "2026-1-001"],
)
def test_a_malformed_or_impossible_date_is_invalid_input_naming_deploy_days(bad):
    result = run("rel_deploy_frequency", _days("2026-10-01", bad))
    invalid(result, "deploy_days")


@pytest.mark.parametrize("bad", ["20261001", "2026-10-1", "2026-10-011", ""])
def test_dates_not_ten_characters_long_are_rejected_by_the_schema(bad):
    invalid(run("rel_deploy_frequency", _days(bad)), "deploy_days")


def test_deploy_frequency_rejects_empty_list_and_non_strings():
    invalid(run("rel_deploy_frequency", {"deploy_days": []}), "deploy_days")
    invalid(run("rel_deploy_frequency", {"deploy_days": [20261001]}), "deploy_days")
    invalid(run("rel_deploy_frequency", {}), "deploy_days")


def test_deploy_frequency_rejects_more_than_ten_thousand_dates():
    invalid(run("rel_deploy_frequency", _days(*["2026-10-01"] * 10001)), "deploy_days")


def test_deploy_frequency_handler_called_directly():
    assert handlers().deploy_frequency(_days("2026-10-01", "2026-10-08")) == {
        "state": "SUCCESS", "deploys": 2, "active_days": 2, "span_days": 8, "per_week": 1.75,
    }


def test_deploy_frequency_handler_reports_a_bad_date_with_the_parse_reason():
    result = handlers().deploy_frequency(_days("2026-02-30"))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "deploy_days"
    assert "YYYY-MM-DD" in result["message"] and "out of range" in result["message"]


# --- rel_lead_time_p95 -------------------------------------------------------------------------------------


def _lead(*hours) -> dict:
    return {"durations_hours": list(hours)}


def test_lead_time_documented_example():
    result = run("rel_lead_time_p95", _lead(2, 4, 8, 16, 24, 30, 48, 72, 96, 120))
    assert result == {"state": "SUCCESS", "count": 10, "median_hours": 24, "p95_hours": 120}


def test_a_single_duration_is_its_own_median_and_p95():
    assert run("rel_lead_time_p95", _lead(3.5)) == {"state": "SUCCESS", "count": 1, "median_hours": 3.5, "p95_hours": 3.5}


def test_unsorted_input_is_sorted_before_ranking():
    result = run("rel_lead_time_p95", _lead(120, 2, 48, 4, 8))
    assert (result["median_hours"], result["p95_hours"]) == (8, 120)


@pytest.mark.parametrize(
    "count, median_rank, p95_rank",
    [(1, 1, 1), (2, 1, 2), (3, 2, 3), (4, 2, 4), (19, 10, 19), (20, 10, 19), (21, 11, 20), (40, 20, 38), (100, 50, 95), (101, 51, 96)],
)
def test_nearest_rank_percentiles_at_each_rank_boundary(count, median_rank, p95_rank):
    values = list(range(1, count + 1))  # value == its 1-based rank
    result = run("rel_lead_time_p95", {"durations_hours": values})
    assert (result["count"], result["median_hours"], result["p95_hours"]) == (count, median_rank, p95_rank)


def test_even_count_median_is_the_lower_middle_value_not_the_average():
    assert run("rel_lead_time_p95", _lead(1, 100))["median_hours"] == 1


def test_duplicates_and_zero_durations_are_kept():
    result = run("rel_lead_time_p95", _lead(0, 0, 0, 5))
    assert (result["median_hours"], result["p95_hours"], result["count"]) == (0, 5, 4)


def test_fractional_hours_are_returned_unrounded():
    result = run("rel_lead_time_p95", _lead(0.25, 1.75, 0.5))
    assert (result["median_hours"], result["p95_hours"]) == (0.5, 1.75)


def test_ten_thousand_durations_are_ranked():
    result = run("rel_lead_time_p95", {"durations_hours": list(range(10000, 0, -1))})
    assert (result["count"], result["median_hours"], result["p95_hours"]) == (10000, 5000, 9500)


@pytest.mark.parametrize("durations", [[], [-1], [1, -0.5], ["2"], [None], [True]])
def test_lead_time_rejects_empty_negative_or_non_numeric_durations(durations):
    invalid(run("rel_lead_time_p95", {"durations_hours": durations}), "durations_hours")


def test_lead_time_rejects_more_than_ten_thousand_durations_and_missing_field():
    invalid(run("rel_lead_time_p95", {"durations_hours": [1] * 10001}), "durations_hours")
    invalid(run("rel_lead_time_p95", {}), "durations_hours")


def test_lead_time_handler_called_directly():
    assert handlers().lead_time_p95(_lead(3, 1, 2)) == {"state": "SUCCESS", "count": 3, "median_hours": 2, "p95_hours": 3}


def test_nan_durations_are_not_ranked():
    result = run("rel_lead_time_p95", _lead(math.nan, 1, 2, 3))
    invalid(result, "durations_hours")
    assert "finite" in result["message"]


# --- rel_gate_order_check ----------------------------------------------------------------------------------

REQUIRED = ["review", "quality", "security", "release"]


def _order(steps, required=REQUIRED) -> dict:
    return {"steps": steps, "required_order": required}


def test_gate_order_documented_example_allows_extra_trailing_steps():
    result = run("rel_gate_order_check", _order(["review", "quality", "security", "release", "deploy"]))
    assert result == {"state": "SUCCESS", "ordered": True, "missing": "", "out_of_order": ""}


def test_unrelated_steps_between_gates_are_ignored():
    result = run("rel_gate_order_check", _order(["build", "review", "lint", "quality", "scan", "security", "x", "release"]))
    assert result["ordered"] is True


def test_missing_gates_are_listed_in_required_order_and_fail_the_check():
    result = run("rel_gate_order_check", _order(["review", "release"]))
    assert result == {"state": "SUCCESS", "ordered": False, "missing": "quality,security", "out_of_order": ""}


def test_a_gate_run_before_its_predecessor_is_out_of_order():
    result = run("rel_gate_order_check", _order(["quality", "review", "security", "release"]))
    assert result == {"state": "SUCCESS", "ordered": False, "missing": "", "out_of_order": "quality"}


def test_promotion_before_security_flags_the_release_gate():
    # security ran at index 3, after the release step at index 2: release is the gate that jumped the queue
    result = run("rel_gate_order_check", _order(["review", "quality", "release", "security"]))
    assert result["ordered"] is False and result["out_of_order"] == "release"


def test_several_gates_can_be_out_of_order_at_once():
    result = run("rel_gate_order_check", _order(["release", "security", "quality", "review"]))
    assert result["out_of_order"] == "quality,security,release"


def test_only_the_first_occurrence_of_a_repeated_step_counts():
    # a late second "review" does not rescue a first review that came after quality
    result = run("rel_gate_order_check", _order(["quality", "review", "quality", "security", "release"]))
    assert result["out_of_order"] == "quality"
    ok = run("rel_gate_order_check", _order(["review", "quality", "review", "security", "release"]))
    assert ok["ordered"] is True


def test_missing_gates_are_skipped_when_judging_order():
    result = run("rel_gate_order_check", _order(["release", "review"], required=["review", "quality", "release"]))
    assert result == {"state": "SUCCESS", "ordered": False, "missing": "quality", "out_of_order": "release"}


def test_a_single_required_gate_that_is_present_is_ordered():
    assert run("rel_gate_order_check", _order(["review"], required=["review"]))["ordered"] is True


def test_nothing_present_reports_every_gate_missing():
    result = run("rel_gate_order_check", _order(["deploy"]))
    assert result["missing"] == "review,quality,security,release" and result["ordered"] is False


def test_gate_names_compare_case_sensitively():
    result = run("rel_gate_order_check", _order(["Review", "quality", "security", "release"]))
    assert result["missing"] == "review"


def test_duplicate_required_gates_are_invalid_input():
    result = run("rel_gate_order_check", _order(["a", "b"], required=["a", "b", "a"]))
    invalid(result, "required_order")
    assert "unique" in result["message"]


def test_a_comma_in_a_step_name_is_invalid_input():
    result = run("rel_gate_order_check", _order(["review,quality"], required=["review"]))
    invalid(result, "steps")
    assert "comma" in result["message"]


def test_a_comma_in_a_required_gate_names_the_required_order_field():
    result = run("rel_gate_order_check", _order(["review"], required=["review,quality"]))
    invalid(result, "required_order")
    assert "comma" in result["message"]


@pytest.mark.parametrize(
    "payload, field",
    [
        (_order([]), "steps"),
        (_order([""]), "steps"),
        (_order(["x" * 65]), "steps"),
        (_order([7]), "steps"),
        (_order(["a"], required=[]), "required_order"),
        (_order(["a"], required=[""]), "required_order"),
        ({"required_order": ["a"]}, "steps"),
        ({"steps": ["a"]}, "required_order"),
    ],
)
def test_gate_order_rejects_malformed_input_and_names_the_field(payload, field):
    invalid(run("rel_gate_order_check", payload), field)


def test_gate_order_rejects_more_than_a_hundred_steps_or_gates():
    invalid(run("rel_gate_order_check", _order([f"s{n}" for n in range(101)], required=["s0"])), "steps")
    invalid(run("rel_gate_order_check", _order(["s0"], required=[f"s{n}" for n in range(101)])), "required_order")


def test_gate_order_accepts_exactly_a_hundred_gates_in_order():
    gates = [f"g{n}" for n in range(100)]
    assert run("rel_gate_order_check", _order(gates, required=gates))["ordered"] is True


def test_gate_order_handler_called_directly():
    assert handlers().gate_order_check(_order(["b", "a"], required=["a", "b"])) == {
        "state": "SUCCESS", "ordered": False, "missing": "", "out_of_order": "b",
    }


# --- shared behaviour of the registry call path ------------------------------------------------------------


@pytest.mark.parametrize(
    "name, payload",
    [
        ("rel_canary_steps", {"release_id": "R", "risk": "low"}),
        ("rel_canary_verdict", _verdict()),
        ("rel_rollback_trigger", _trigger([1.0])),
        ("rel_release_record_check", _record({"a": 1}, ["a"])),
        ("rel_change_failure_rate", {"outcomes": [True]}),
        ("rel_deploy_frequency", _days("2026-10-01")),
        ("rel_lead_time_p95", _lead(1)),
        ("rel_gate_order_check", _order(["a"], ["a"])),
    ],
)
def test_dependency_probe_reports_dependency_unavailable_for_every_tool(name, payload):
    assert run(name, {**payload, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


def test_results_are_json_serialisable_and_stable_across_calls():
    first = run("rel_lead_time_p95", _lead(3, 1, 2))
    second = run("rel_lead_time_p95", _lead(3, 1, 2))
    assert first == second and json.loads(json.dumps(first)) == first
