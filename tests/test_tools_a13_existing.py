"""Behaviour tests for the v2.1 A13 (observability) lane tools that had only generated contract probes.

Covers obs_error_burn, obs_multiwindow_alert, obs_budget_remaining, obs_alert_fatigue, obs_incident_mttd_mttr,
obs_ingestion_lag_p95, obs_dashboard_staleness, obs_runbook_link_coverage, obs_cardinality_estimate and
obs_probe_availability. Every assertion is on output values for specific inputs. Pure computation only: no
network, no shell, no repo writes.

tests/conftest.py evicts swarm* modules between tests, so the registry caller and the handler functions are
resolved through the current module graph at call time.
"""
from __future__ import annotations

import importlib
import json

import pytest


def _registry():
    return importlib.import_module("swarm.tool_registry")


def _a13():
    return importlib.import_module("swarm.tools.a13_tools")


def _batch():
    return importlib.import_module("swarm.tools.batch_11_15")


def call(name: str, payload, caller: str = "A13") -> dict:
    return _registry().call_tool(name, caller, payload)


def ok(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


def invalid(name: str, payload, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    return result


def T(minute: int, second: int = 0) -> str:
    return f"2026-10-15T10:{minute:02d}:{second:02d}Z"


# --- obs_error_burn ----------------------------------------------------------------------------------


def burn(**over) -> dict:
    payload = {"good": 90, "total": 100, "objective": 0.99}
    payload.update(over)
    return call("obs_error_burn", payload)


def test_error_burn_success_values():
    result = burn()
    assert result["state"] == "SUCCESS"
    assert result["sli"] == 0.9
    assert result["burn_rate"] == pytest.approx(10.0)
    assert set(result) == {"state", "sli", "burn_rate"}


@pytest.mark.parametrize(
    ("good", "total", "objective", "sli", "rate"),
    [
        (100, 100, 0.9, 1.0, 0.0),  # no errors: zero burn
        (999, 1000, 0.999, 0.999, 1.0),  # consuming exactly the allowance burns at 1x
        (0, 10, 0.5, 0.0, 2.0),  # total outage burns at 1/(1-objective)
        (5, 10, 0.0, 0.5, 0.5),  # objective 0 is allowed by the handler
    ],
)
def test_error_burn_computed_values(good, total, objective, sli, rate):
    result = burn(good=good, total=total, objective=objective)
    assert result["state"] == "SUCCESS"
    assert result["sli"] == pytest.approx(sli)
    assert result["burn_rate"] == pytest.approx(rate)


@pytest.mark.parametrize("total", [0, -3])
def test_error_burn_non_positive_total_names_total(total):
    result = burn(good=5, total=total)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "total"


@pytest.mark.parametrize("objective", [1, 1.0, 2, -0.1])
def test_error_burn_objective_outside_half_open_unit_interval(objective):
    result = burn(objective=objective)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "objective"
    assert "[0, 1)" in result["message"]


def test_error_burn_objective_checked_before_total():
    assert burn(total=0, objective=1)["field"] == "objective"


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"good": "5"}, "good"),
        ({"good": 5.5}, "good"),
        ({"good": True}, "good"),
        ({"total": None}, "total"),
        ({"objective": "high"}, "objective"),
        ({"probe": "nope"}, "probe"),
        ({"extra": 1}, "extra"),
    ],
)
def test_error_burn_malformed_input_names_the_field(over, field):
    result = burn(**over)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_error_burn_empty_payload_and_non_object():
    assert call("obs_error_burn", {})["field"] == "good"
    result = call("obs_error_burn", [])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "payload"


def test_error_burn_unauthorized_caller():
    result = call("obs_error_burn", {"good": 1, "total": 2, "objective": 0.5}, caller="A12")
    assert result["state"] == "UNAUTHORIZED" and result["caller"] == "A12"


def test_error_burn_probes_never_reach_the_computation():
    assert burn(probe="partial") == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert burn(probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"
    assert burn(probe="sleep")["state"] == "TIMEOUT"


def test_error_burn_handler_called_directly():
    result = _batch().error_burn({"good": 1, "total": 2, "objective": 0.5})
    assert result["state"] == "SUCCESS" and result["sli"] == 0.5 and result["burn_rate"] == pytest.approx(1.0)


def test_error_burn_oversized_total_is_still_exact_in_sli():
    result = burn(good=0, total=10**400)
    assert result["state"] == "SUCCESS" and result["sli"] == 0.0 and result["burn_rate"] == pytest.approx(100.0)


@pytest.mark.parametrize("good", [120, -5])
def test_error_burn_rejects_good_outside_zero_to_total(good):
    """Repro: good=120 total=100 objective=0.99 -> SUCCESS sli=1.2 burn_rate=-20; expected INVALID_INPUT field good."""
    result = burn(good=good, total=100)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "good"


# --- obs_multiwindow_alert ---------------------------------------------------------------------------


def alert(**over) -> dict:
    payload = {"error_ratio_1h": 0.0, "error_ratio_6h": 0.0, "error_ratio_3d": 0.0, "slo_target": 0.99}
    payload.update(over)
    return call("obs_multiwindow_alert", payload)


def test_alert_page_when_both_windows_exceed_thresholds():
    # slo 0.99: budget 0.01. 1h 0.2 -> 20x, 6h 0.07 -> 7x, 3d 0.02 -> 2x
    result = alert(error_ratio_1h=0.2, error_ratio_6h=0.07, error_ratio_3d=0.02)
    assert result == {
        "state": "SUCCESS",
        "burn_1h": 20.0,
        "burn_6h": 7.0,
        "burn_3d": 2.0,
        "page": True,
        "ticket": True,
        "severity": "page",
    }


def test_alert_all_quiet_is_none():
    result = alert()
    assert (result["burn_1h"], result["burn_6h"], result["burn_3d"]) == (0.0, 0.0, 0.0)
    assert (result["page"], result["ticket"], result["severity"]) == (False, False, "none")


def test_alert_short_spike_alone_does_not_page():
    # 1h burn is huge but the 6h window is below 6x: multi-window rule suppresses the page.
    result = alert(error_ratio_1h=0.5, error_ratio_6h=0.05, error_ratio_3d=0.02)
    assert result["burn_1h"] == 50.0 and result["burn_6h"] == 5.0
    assert result["page"] is False and result["ticket"] is True and result["severity"] == "ticket"


def test_alert_long_window_alone_does_not_page():
    result = alert(error_ratio_1h=0.1, error_ratio_6h=0.5, error_ratio_3d=0.5)
    assert result["burn_1h"] == 10.0  # below 14.4
    assert result["page"] is False and result["severity"] == "ticket"


def test_alert_page_outranks_ticket_and_ticket_is_independent_of_page():
    paged = alert(error_ratio_1h=0.2, error_ratio_6h=0.07, error_ratio_3d=0.0)
    assert paged["page"] is True and paged["ticket"] is False and paged["severity"] == "page"


def test_alert_ticket_only_on_3d_window():
    result = alert(error_ratio_3d=0.02)
    assert result["ticket"] is True and result["page"] is False and result["severity"] == "ticket"
    assert alert(error_ratio_3d=0.005)["severity"] == "none"


def test_alert_burn_is_ratio_over_budget():
    result = alert(error_ratio_1h=0.25, error_ratio_6h=0.25, error_ratio_3d=0.25, slo_target=0.5)
    assert (result["burn_1h"], result["burn_6h"], result["burn_3d"]) == (0.5, 0.5, 0.5)
    assert result["page"] is False and result["ticket"] is False
    result = alert(error_ratio_1h=0.5, error_ratio_6h=0.5, error_ratio_3d=0.5, slo_target=0.5)
    assert result["burn_3d"] == 1.0 and result["ticket"] is True  # 1x is exactly representable here


def test_alert_full_outage_at_low_target_values():
    result = alert(error_ratio_1h=1, error_ratio_6h=1, error_ratio_3d=1, slo_target=0.5)
    assert (result["burn_1h"], result["burn_6h"], result["burn_3d"]) == (2.0, 2.0, 2.0)
    assert result["severity"] == "ticket"  # 2x < 14.4x / 6x so no page


def test_alert_burn_values_rounded_to_four_places():
    result = alert(error_ratio_1h=1 / 3, error_ratio_6h=0.1, error_ratio_3d=0.1)
    assert result["burn_1h"] == round((1 / 3) / 0.01, 4)
    assert result["burn_1h"] == 33.3333


def test_alert_page_requires_exactly_the_documented_thresholds_just_below_and_above():
    below = alert(error_ratio_1h=0.1430, error_ratio_6h=0.07, error_ratio_3d=0.0)
    above = alert(error_ratio_1h=0.1450, error_ratio_6h=0.07, error_ratio_3d=0.0)
    assert below["page"] is False and above["page"] is True
    assert alert(error_ratio_1h=0.2, error_ratio_6h=0.0590, error_ratio_3d=0.0)["page"] is False
    assert alert(error_ratio_1h=0.2, error_ratio_6h=0.0610, error_ratio_3d=0.0)["page"] is True


def test_alert_pages_at_exactly_14_4x_and_6x():
    """Repro: error_ratio_1h=0.0144 6h=0.006 slo_target=0.999 -> burn_1h reported 14.4 but page False; expected True."""
    result = alert(error_ratio_1h=0.0144, error_ratio_6h=0.006, error_ratio_3d=0.0, slo_target=0.999)
    assert result["burn_1h"] == 14.4 and result["burn_6h"] == 6.0
    assert result["page"] is True


def test_alert_tickets_at_exactly_1x_burn():
    """Repro: error_ratio_3d=0.001 slo_target=0.999 -> burn_3d reported 1.0 but ticket False; expected True."""
    result = alert(error_ratio_3d=0.001, slo_target=0.999)
    assert result["burn_3d"] == 1.0
    assert result["ticket"] is True


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"error_ratio_1h": 1.5}, "error_ratio_1h"),
        ({"error_ratio_6h": -0.1}, "error_ratio_6h"),
        ({"error_ratio_3d": 2}, "error_ratio_3d"),
        ({"slo_target": 1}, "slo_target"),
        ({"slo_target": 0}, "slo_target"),
        ({"slo_target": "x"}, "slo_target"),
        ({"error_ratio_1h": "0.5"}, "error_ratio_1h"),
        ({"surprise": True}, "surprise"),
    ],
)
def test_alert_malformed_input_names_the_field(over, field):
    result = alert(**over)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_alert_missing_field_unauthorized_and_probes():
    assert call("obs_multiwindow_alert", {})["state"] == "INVALID_INPUT"
    result = call("obs_multiwindow_alert", {"error_ratio_1h": 0, "error_ratio_6h": 0, "error_ratio_3d": 0}, "A13")
    assert result["state"] == "INVALID_INPUT" and result["field"] == "slo_target"
    assert call("obs_multiwindow_alert", {"error_ratio_1h": 0, "error_ratio_6h": 0, "error_ratio_3d": 0, "slo_target": 0.9}, "A05")["state"] == "UNAUTHORIZED"
    assert alert(probe="partial")["state"] == "PARTIAL_SUCCESS"
    assert alert(probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"


def test_alert_extreme_slo_target_stays_finite_json():
    result = alert(error_ratio_1h=0.1, error_ratio_6h=1, error_ratio_3d=1, slo_target=1e-300)
    assert result["state"] == "SUCCESS" and result["burn_1h"] == 0.1 and result["severity"] == "ticket"
    json.dumps(result, allow_nan=False)


def test_alert_handler_called_directly():
    result = _a13().multiwindow_alert(
        {"error_ratio_1h": 0.2, "error_ratio_6h": 0.07, "error_ratio_3d": 0.02, "slo_target": 0.99}
    )
    assert result["severity"] == "page" and result["burn_1h"] == 20.0


# --- obs_budget_remaining ----------------------------------------------------------------------------


def budget(**over) -> dict:
    payload = {"good": 999, "total": 1000, "slo_target": 0.99}
    payload.update(over)
    return call("obs_budget_remaining", payload)


def test_budget_partial_consumption_values():
    assert budget() == {
        "state": "SUCCESS",
        "allowed_bad_events": 10.0,
        "bad_events": 1,
        "remaining_events": 9.0,
        "remaining_fraction": 0.9,
        "exhausted": False,
    }


def test_budget_untouched_when_no_bad_events():
    result = budget(good=1000)
    assert result["bad_events"] == 0 and result["remaining_events"] == 10.0
    assert result["remaining_fraction"] == 1.0 and result["exhausted"] is False


def test_budget_overspent_goes_negative_and_exhausted():
    result = budget(good=980)
    assert result["bad_events"] == 20 and result["remaining_events"] == -10.0
    assert result["remaining_fraction"] == -1.0 and result["exhausted"] is True


def test_budget_exhausted_exactly_at_allowance_with_exact_target():
    result = budget(good=5, total=10, slo_target=0.5)
    assert result["allowed_bad_events"] == 5.0 and result["bad_events"] == 5
    assert result["remaining_events"] == 0.0 and result["remaining_fraction"] == 0.0 and result["exhausted"] is True


def test_budget_fractional_allowance_is_rounded_to_two_places():
    result = budget(good=1, total=1, slo_target=0.5)
    assert result["allowed_bad_events"] == 0.5 and result["remaining_events"] == 0.5
    result = budget(good=300, total=300, slo_target=0.999)
    assert result["allowed_bad_events"] == 0.3 and result["remaining_fraction"] == 1.0


def test_budget_one_bad_event_with_tiny_allowance_overspends():
    result = budget(good=0, total=1, slo_target=0.5)
    assert result["bad_events"] == 1 and result["remaining_events"] == -0.5
    assert result["remaining_fraction"] == -1.0 and result["exhausted"] is True


def test_budget_exhausted_when_bad_events_equal_the_allowance_at_99_percent():
    """Repro: good=990 total=1000 slo_target=0.99 -> remaining_events 0.0 but exhausted False; 5/10 at 0.5 gives True."""
    result = budget(good=990)
    assert result["bad_events"] == 10 and result["remaining_events"] == 0.0
    assert result["exhausted"] is True


def test_budget_good_above_total_names_good():
    result = budget(good=1001)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "good"
    assert "exceed" in result["message"]


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"total": 0}, "total"),
        ({"good": -1}, "good"),
        ({"slo_target": 1}, "slo_target"),
        ({"slo_target": 0}, "slo_target"),
        ({"good": 1.5}, "good"),
        ({"total": "1000"}, "total"),
        ({"extra": 0}, "extra"),
    ],
)
def test_budget_malformed_input_names_the_field(over, field):
    result = budget(**over)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_budget_empty_unauthorized_and_probes():
    assert call("obs_budget_remaining", {})["state"] == "INVALID_INPUT"
    assert call("obs_budget_remaining", {"good": 1, "total": 1, "slo_target": 0.9}, "A10")["state"] == "UNAUTHORIZED"
    assert budget(probe="partial")["state"] == "PARTIAL_SUCCESS"
    assert budget(probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"


def test_budget_large_counts_stay_exact():
    result = budget(good=10**15, total=10**15, slo_target=0.999)
    assert result["allowed_bad_events"] == 1e12 and result["remaining_fraction"] == 1.0


def test_budget_oversized_total_is_rejected_as_invalid_input_not_a_dependency_failure():
    """Repro: good=0 total=10**400 slo_target=0.99 used to surface as DEPENDENCY_UNAVAILABLE 'int too large to convert to float'."""
    invalid("obs_budget_remaining", {"good": 0, "total": 10**400, "slo_target": 0.99}, "total")
    invalid("obs_budget_remaining", {"good": 0, "total": 10**15 + 1, "slo_target": 0.99}, "total")
    assert _a13().budget_remaining({"good": 0, "total": 10**400, "slo_target": 0.99})["field"] == "total"  # handler guards too


def test_budget_handler_called_directly():
    result = _a13().budget_remaining({"good": 95, "total": 100, "slo_target": 0.9})
    assert result["allowed_bad_events"] == 10.0 and result["bad_events"] == 5 and result["remaining_fraction"] == 0.5


# --- obs_alert_fatigue -------------------------------------------------------------------------------


def fatigue(**over) -> dict:
    payload = {"pages": 10, "actionable": 6, "max_noise_ratio": 0.5}
    payload.update(over)
    return call("obs_alert_fatigue", payload)


def test_fatigue_below_threshold_values():
    assert fatigue() == {
        "state": "SUCCESS",
        "noise_ratio": 0.4,
        "actionable_count": 6,
        "noise_count": 4,
        "fatigued": False,
    }


def test_fatigue_above_threshold_flags_and_rounds():
    result = fatigue(pages=3, actionable=1)
    assert result["noise_ratio"] == 0.6667 and result["noise_count"] == 2 and result["fatigued"] is True


def test_fatigue_ratio_equal_to_limit_is_not_fatigued():
    result = fatigue(pages=10, actionable=5, max_noise_ratio=0.5)
    assert result["noise_ratio"] == 0.5 and result["fatigued"] is False


def test_fatigue_zero_pages_is_zero_noise_never_fatigued():
    result = fatigue(pages=0, actionable=0, max_noise_ratio=0)
    assert result["noise_ratio"] == 0.0 and result["fatigued"] is False and result["noise_count"] == 0


def test_fatigue_all_noise_with_limit_one_is_not_fatigued_but_with_less_is():
    assert fatigue(pages=3, actionable=0, max_noise_ratio=1)["fatigued"] is False
    full = fatigue(pages=3, actionable=0, max_noise_ratio=0.99)
    assert full["noise_ratio"] == 1.0 and full["fatigued"] is True


def test_fatigue_zero_limit_flags_any_noise():
    assert fatigue(pages=4, actionable=3, max_noise_ratio=0)["fatigued"] is True
    assert fatigue(pages=4, actionable=4, max_noise_ratio=0)["fatigued"] is False


def test_fatigue_actionable_above_pages_names_actionable():
    result = fatigue(pages=3, actionable=4)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "actionable"


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"pages": -1}, "pages"),
        ({"actionable": -1}, "actionable"),
        ({"max_noise_ratio": 1.01}, "max_noise_ratio"),
        ({"max_noise_ratio": -0.1}, "max_noise_ratio"),
        ({"pages": 2.5}, "pages"),
        ({"pages": "10"}, "pages"),
        ({"bogus": 1}, "bogus"),
    ],
)
def test_fatigue_malformed_input_names_the_field(over, field):
    result = fatigue(**over)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_fatigue_empty_unauthorized_and_probes():
    assert call("obs_alert_fatigue", {})["state"] == "INVALID_INPUT"
    assert call("obs_alert_fatigue", {"pages": 1, "actionable": 1, "max_noise_ratio": 0.5}, "A01")["state"] == "UNAUTHORIZED"
    assert fatigue(probe="partial")["completed_fraction"] == 0.5
    assert fatigue(probe="sleep")["state"] == "TIMEOUT"


def test_fatigue_very_large_page_counts():
    result = fatigue(pages=10**12, actionable=10**12 - 1, max_noise_ratio=0.5)
    assert result["noise_count"] == 1 and result["noise_ratio"] == 0.0 and result["fatigued"] is False


def test_fatigue_handler_called_directly():
    result = _a13().alert_fatigue({"pages": 8, "actionable": 2, "max_noise_ratio": 0.5})
    assert result["noise_ratio"] == 0.75 and result["fatigued"] is True


# --- obs_incident_mttd_mttr --------------------------------------------------------------------------


def incident(started, detected, resolved) -> dict:
    return {"started": started, "detected": detected, "resolved": resolved}


def timing(*incidents, **over) -> dict:
    payload = {"incidents": list(incidents)}
    payload.update(over)
    return call("obs_incident_mttd_mttr", payload)


def test_timing_single_incident_values():
    result = timing(incident(T(0), T(5), T(59, 59)))
    assert result == {
        "state": "SUCCESS",
        "incident_count": 1,
        "mttd_seconds": 300.0,
        "mttr_seconds": 3599.0,
        "max_mttr_seconds": 3599.0,
    }


def test_timing_means_and_maximum_over_several_incidents():
    first = incident("2026-10-15T10:00:00Z", "2026-10-15T10:05:00Z", "2026-10-15T11:00:00Z")  # 300 / 3600
    second = incident("2026-10-16T00:00:00Z", "2026-10-16T00:01:01Z", "2026-10-16T00:10:00Z")  # 61 / 600
    result = timing(first, second)
    assert result["incident_count"] == 2
    assert result["mttd_seconds"] == 180.5
    assert result["mttr_seconds"] == 2100.0
    assert result["max_mttr_seconds"] == 3600.0


def test_timing_max_is_not_the_last_incident():
    slow = incident(T(0), T(1), T(50))
    fast = incident(T(0), T(1), T(2))
    assert timing(slow, fast)["max_mttr_seconds"] == 3000.0
    assert timing(fast, slow)["max_mttr_seconds"] == 3000.0


def test_timing_equal_timestamps_are_allowed_and_zero():
    result = timing(incident(T(0), T(0), T(0)))
    assert (result["mttd_seconds"], result["mttr_seconds"], result["max_mttr_seconds"]) == (0.0, 0.0, 0.0)


def test_timing_offsets_are_compared_as_instants():
    result = timing(incident("2026-10-15T12:00:00+02:00", "2026-10-15T10:05:00Z", "2026-10-15T10:30:00Z"))
    assert result["mttd_seconds"] == 300.0 and result["mttr_seconds"] == 1800.0


def test_timing_fractional_seconds_round_to_one_decimal():
    result = timing(incident("2026-10-15T10:00:00.250Z", "2026-10-15T10:00:00.500Z", "2026-10-15T10:00:01Z"))
    assert result["mttd_seconds"] == 0.2 and result["mttr_seconds"] == 0.8 and result["max_mttr_seconds"] == 0.8


@pytest.mark.parametrize(
    "bad",
    [
        incident(T(10), T(5), T(30)),  # detected before started
        incident(T(0), T(20), T(10)),  # resolved before detected
        incident(T(10), T(5), T(1)),  # fully reversed
    ],
)
def test_timing_ordering_violation_names_incidents_and_index(bad):
    result = timing(bad)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "incidents"
    assert "incident 0" in result["message"] and "started <= detected <= resolved" in result["message"]


def test_timing_error_message_carries_the_index_of_the_bad_incident():
    good = incident(T(0), T(1), T(2))
    result = timing(good, good, incident(T(5), T(4), T(6)))
    assert result["field"] == "incidents" and "incident 2" in result["message"]


def test_timing_timestamp_without_timezone_or_date_only_is_invalid():
    for text in ("2026-10-15T10:00:00", "2026-10-15"):
        result = timing(incident(text, T(1), T(2)))
        assert result["state"] == "INVALID_INPUT" and result["field"] == "incidents"
        assert "timezone" in result["message"]


def test_timing_unparseable_timestamp_is_invalid():
    result = timing(incident("nope", T(1), T(2)))
    assert result["state"] == "INVALID_INPUT" and "incident 0" in result["message"]


def test_timing_empty_list_and_oversized_list():
    assert invalid("obs_incident_mttd_mttr", {"incidents": []}, "incidents")["message"] == "fewer than 1 items"
    big = [incident(T(0), T(1), T(2))] * 501
    assert invalid("obs_incident_mttd_mttr", {"incidents": big}, "incidents")["message"] == "more than 500 items"


def test_timing_exactly_500_incidents_succeeds():
    result = ok("obs_incident_mttd_mttr", {"incidents": [incident(T(0), T(1), T(10))] * 500})
    assert result["incident_count"] == 500 and result["mttd_seconds"] == 60.0 and result["mttr_seconds"] == 600.0


def test_timing_schema_errors_name_the_incidents_field():
    for payload in (
        {"incidents": [{"started": T(0), "detected": T(1)}]},  # resolved missing
        {"incidents": [{"started": T(0), "detected": T(1), "resolved": T(2), "extra": 1}]},
        {"incidents": [{"started": "", "detected": T(1), "resolved": T(2)}]},
        {"incidents": "nope"},
    ):
        result = call("obs_incident_mttd_mttr", payload)
        assert result["state"] == "INVALID_INPUT" and result["field"] == "incidents", payload


def test_timing_empty_payload_unauthorized_and_probes():
    assert call("obs_incident_mttd_mttr", {})["field"] == "incidents"
    assert call("obs_incident_mttd_mttr", {"incidents": [incident(T(0), T(1), T(2))]}, "A14")["state"] == "UNAUTHORIZED"
    assert timing(incident(T(0), T(1), T(2)), probe="partial")["state"] == "PARTIAL_SUCCESS"
    assert timing(incident(T(0), T(1), T(2)), probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"


def test_timing_handler_called_directly():
    result = _a13().incident_timing({"incidents": [incident(T(0), T(2), T(12))]})
    assert result["mttd_seconds"] == 120.0 and result["mttr_seconds"] == 720.0


# --- obs_ingestion_lag_p95 ---------------------------------------------------------------------------


def lag(lags, target=10, **over) -> dict:
    payload = {"lags_seconds": lags, "target_s": target}
    payload.update(over)
    return call("obs_ingestion_lag_p95", payload)


def test_lag_nearest_rank_p95_of_twenty_values_is_the_19th():
    result = lag(list(range(1, 21)), target=19)
    assert result == {"state": "SUCCESS", "count": 20, "p95_seconds": 19.0, "max_seconds": 20.0, "within_target": True}


def test_lag_target_just_below_p95_fails():
    result = lag(list(range(1, 21)), target=18.9)
    assert result["p95_seconds"] == 19.0 and result["within_target"] is False


def test_lag_input_order_does_not_matter():
    shuffled = [9, 1, 20, 3, 14, 7, 18, 2, 19, 5, 11, 4, 16, 8, 17, 6, 12, 15, 13, 10]
    assert lag(shuffled)["p95_seconds"] == 19.0


def test_lag_hundred_values_p95_is_95():
    values = list(range(1, 101))
    assert lag(values, target=95)["within_target"] is True
    assert lag(values, target=94.9)["within_target"] is False
    assert lag(values)["p95_seconds"] == 95.0 and lag(values)["max_seconds"] == 100.0


def test_lag_small_samples_p95_is_the_maximum():
    assert lag([7], target=7) == {"state": "SUCCESS", "count": 1, "p95_seconds": 7.0, "max_seconds": 7.0, "within_target": True}
    two = lag([1.5, 2.5], target=2.5)
    assert two["p95_seconds"] == 2.5 and two["within_target"] is True


def test_lag_single_outlier_dominates_a_short_sample():
    result = lag([0, 0, 0, 0, 100], target=50)
    assert result["p95_seconds"] == 100.0 and result["within_target"] is False


def test_lag_large_sample_ignores_a_one_percent_tail():
    result = lag([1] * 99 + [500], target=1)
    assert result["p95_seconds"] == 1.0 and result["max_seconds"] == 500.0 and result["within_target"] is True


def test_lag_zero_target_requires_zero_lag():
    assert lag([0, 0, 0], target=0)["within_target"] is True
    assert lag([9, 1, 5], target=0)["within_target"] is False


def test_lag_integers_are_reported_as_floats():
    result = lag([3])
    assert isinstance(result["p95_seconds"], float) and isinstance(result["max_seconds"], float)


@pytest.mark.parametrize(
    ("lags", "target", "field"),
    [
        ([], 1, "lags_seconds"),
        ([-1], 1, "lags_seconds"),
        ([1, "a"], 2, "lags_seconds"),
        ([True, 2], 3, "lags_seconds"),
        ([1, 2], -1, "target_s"),
        ([1, 2], "x", "target_s"),
        ("1,2", 1, "lags_seconds"),
    ],
)
def test_lag_malformed_input_names_the_field(lags, target, field):
    invalid("obs_ingestion_lag_p95", {"lags_seconds": lags, "target_s": target}, field)


def test_lag_size_limits():
    assert invalid("obs_ingestion_lag_p95", {"lags_seconds": [1] * 10001, "target_s": 2}, "lags_seconds")["message"] == "more than 10000 items"
    result = lag([1] * 10000, target=2)
    assert result["count"] == 10000 and result["p95_seconds"] == 1.0


def test_lag_empty_payload_unauthorized_and_probes():
    assert call("obs_ingestion_lag_p95", {})["state"] == "INVALID_INPUT"
    assert call("obs_ingestion_lag_p95", {"lags_seconds": [1], "target_s": 1}, "A11")["state"] == "UNAUTHORIZED"
    assert lag([1], probe="partial")["state"] == "PARTIAL_SUCCESS"
    assert lag([1], probe="sleep")["state"] == "TIMEOUT"


def test_lag_handler_called_directly():
    result = _a13().ingestion_lag_p95({"lags_seconds": [4, 2, 8], "target_s": 8})
    assert result["p95_seconds"] == 8.0 and result["count"] == 3


# --- obs_dashboard_staleness -------------------------------------------------------------------------


def stale(updated, checked, max_age=2, **over) -> dict:
    payload = {"last_updated_at": updated, "checked_at": checked, "max_age_hours": max_age}
    payload.update(over)
    return call("obs_dashboard_staleness", payload)


def test_staleness_exactly_at_limit_is_fresh():
    assert stale("2026-10-15T10:00:00Z", "2026-10-15T12:00:00Z") == {"state": "SUCCESS", "age_hours": 2.0, "stale": False}


def test_staleness_one_second_over_limit_is_stale():
    result = stale("2026-10-15T10:00:00Z", "2026-10-15T12:00:01Z")
    assert result["stale"] is True and result["age_hours"] == 2.0  # rounded to 2 places


def test_staleness_age_rounded_to_two_decimals():
    assert stale("2026-10-15T10:00:00Z", "2026-10-15T10:20:00Z", max_age=24) == {
        "state": "SUCCESS",
        "age_hours": 0.33,
        "stale": False,
    }


def test_staleness_zero_age_when_checked_equals_updated():
    result = stale(T(0), T(0), max_age=0.5)
    assert result["age_hours"] == 0.0 and result["stale"] is False


def test_staleness_multi_day_age_in_hours():
    result = stale("2026-10-15T10:00:00Z", "2026-10-17T10:00:00Z", max_age=24)
    assert result["age_hours"] == 48.0 and result["stale"] is True


def test_staleness_offsets_compared_as_instants():
    result = stale("2026-10-15T10:00:00+02:00", "2026-10-15T10:00:00Z", max_age=1)
    assert result["age_hours"] == 2.0 and result["stale"] is True


def test_staleness_z_suffix_and_explicit_utc_offset_agree():
    assert stale("2026-10-15T10:00:00Z", "2026-10-15T11:30:00Z", 3) == stale("2026-10-15T10:00:00+00:00", "2026-10-15T11:30:00+00:00", 3)


def test_staleness_checked_before_updated_names_checked_at():
    result = stale("2026-10-15T10:00:00Z", "2026-10-15T09:59:59Z")
    assert result["state"] == "INVALID_INPUT" and result["field"] == "checked_at"
    assert "precedes" in result["message"]


def test_staleness_timestamp_without_timezone_names_the_field():
    assert invalid("obs_dashboard_staleness", {"last_updated_at": "2026-10-15T10:00:00", "checked_at": T(1), "max_age_hours": 1}, "last_updated_at")
    assert invalid("obs_dashboard_staleness", {"last_updated_at": T(1), "checked_at": "2026-10-15T10:00:00", "max_age_hours": 1}, "checked_at")


def test_staleness_unparseable_timestamp_names_the_field():
    invalid("obs_dashboard_staleness", {"last_updated_at": "zzz", "checked_at": T(1), "max_age_hours": 1}, "last_updated_at")
    invalid("obs_dashboard_staleness", {"last_updated_at": T(1), "checked_at": "zzz", "max_age_hours": 1}, "checked_at")


def test_staleness_last_updated_validated_before_checked_at():
    invalid("obs_dashboard_staleness", {"last_updated_at": "bad", "checked_at": "worse", "max_age_hours": 1}, "last_updated_at")


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"last_updated_at": ""}, "last_updated_at"),
        ({"checked_at": ""}, "checked_at"),
        ({"max_age_hours": 0}, "max_age_hours"),
        ({"max_age_hours": -1}, "max_age_hours"),
        ({"max_age_hours": "2"}, "max_age_hours"),
        ({"last_updated_at": 5}, "last_updated_at"),
        ({"extra": 1}, "extra"),
    ],
)
def test_staleness_malformed_input_names_the_field(over, field):
    payload = {"last_updated_at": T(0), "checked_at": T(30), "max_age_hours": 1}
    payload.update(over)
    invalid("obs_dashboard_staleness", payload, field)


def test_staleness_extreme_dates_do_not_crash():
    result = stale("0001-01-01T00:00:00Z", "9999-12-31T00:00:00Z", max_age=24)
    assert result["state"] == "SUCCESS" and result["stale"] is True and result["age_hours"] > 8e7


def test_staleness_empty_unauthorized_and_probes():
    assert call("obs_dashboard_staleness", {})["state"] == "INVALID_INPUT"
    assert call("obs_dashboard_staleness", {"last_updated_at": T(0), "checked_at": T(1), "max_age_hours": 1}, "A04")["state"] == "UNAUTHORIZED"
    assert stale(T(0), T(1), probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"
    assert stale(T(0), T(1), probe="partial")["state"] == "PARTIAL_SUCCESS"


def test_staleness_handler_called_directly():
    result = _a13().dashboard_staleness({"last_updated_at": T(0), "checked_at": T(30), "max_age_hours": 0.25})
    assert result == {"state": "SUCCESS", "age_hours": 0.5, "stale": True}


# --- obs_runbook_link_coverage -----------------------------------------------------------------------


def alert_entry(name, runbook) -> dict:
    return {"name": name, "runbook": runbook}


def coverage(alerts, known=("rb1", "rb2"), **over) -> dict:
    payload = {"alerts": alerts, "known_runbooks": list(known)}
    payload.update(over)
    return call("obs_runbook_link_coverage", payload)


def test_coverage_mixed_alerts_values_and_unlinked_order():
    result = coverage([alert_entry("a", "rb1"), alert_entry("b", "rb9"), alert_entry("c", "")])
    assert result == {
        "state": "SUCCESS",
        "alert_count": 3,
        "linked_count": 1,
        "coverage_ratio": 0.3333,
        "unlinked_alerts": ["b", "c"],
    }


def test_coverage_all_linked():
    result = coverage([alert_entry("a", "rb1"), alert_entry("b", "rb2")])
    assert result["coverage_ratio"] == 1.0 and result["unlinked_alerts"] == [] and result["linked_count"] == 2


def test_coverage_none_linked_lists_every_alert_in_input_order():
    result = coverage([alert_entry("z", "x"), alert_entry("a", "y"), alert_entry("m", "")])
    assert result["coverage_ratio"] == 0.0 and result["unlinked_alerts"] == ["z", "a", "m"]


def test_coverage_match_is_exact_case_and_whitespace_sensitive():
    result = coverage([alert_entry("upper", "RB1"), alert_entry("padded", "rb1 "), alert_entry("exact", "rb1")], known=["rb1"])
    assert result["linked_count"] == 1 and result["unlinked_alerts"] == ["upper", "padded"]


def test_coverage_empty_runbook_string_is_unlinked():
    result = coverage([alert_entry("a", "")])
    assert result["linked_count"] == 0 and result["unlinked_alerts"] == ["a"]


def test_coverage_duplicate_alert_names_are_counted_per_alert():
    result = coverage([alert_entry("dup", "rb1"), alert_entry("dup", "nope")], known=["rb1"])
    assert result["alert_count"] == 2 and result["linked_count"] == 1 and result["coverage_ratio"] == 0.5
    assert result["unlinked_alerts"] == ["dup"]


def test_coverage_duplicate_known_runbooks_do_not_inflate():
    result = coverage([alert_entry("a", "rb1")], known=["rb1", "rb1", "rb1"])
    assert result["linked_count"] == 1 and result["coverage_ratio"] == 1.0


def test_coverage_ratio_rounds_to_four_places():
    result = coverage([alert_entry("a", "rb1"), alert_entry("b", "x"), alert_entry("c", "rb1")])
    assert result["coverage_ratio"] == 0.6667


def test_coverage_size_limits():
    assert invalid("obs_runbook_link_coverage", {"alerts": [], "known_runbooks": ["rb1"]}, "alerts")["message"] == "fewer than 1 items"
    assert invalid("obs_runbook_link_coverage", {"alerts": [alert_entry("a", "rb1")], "known_runbooks": []}, "known_runbooks")
    assert invalid("obs_runbook_link_coverage", {"alerts": [alert_entry("a", "rb1")] * 1001, "known_runbooks": ["rb1"]}, "alerts")["message"] == "more than 1000 items"
    result = coverage([alert_entry("a", "rb1")] * 1000, known=["rb1"])
    assert result["alert_count"] == 1000 and result["coverage_ratio"] == 1.0


@pytest.mark.parametrize(
    ("alerts", "known", "field"),
    [
        ([{"name": "", "runbook": "rb1"}], ["rb1"], "alerts"),
        ([{"name": "a"}], ["rb1"], "alerts"),
        ([{"runbook": "rb1"}], ["rb1"], "alerts"),
        ([{"name": "a", "runbook": "rb1", "extra": 1}], ["rb1"], "alerts"),
        ([{"name": "a", "runbook": 5}], ["rb1"], "alerts"),
        ("a", ["rb1"], "alerts"),
        ([{"name": "a", "runbook": "rb1"}], [""], "known_runbooks"),
        ([{"name": "a", "runbook": "rb1"}], [1], "known_runbooks"),
    ],
)
def test_coverage_malformed_input_names_the_field(alerts, known, field):
    invalid("obs_runbook_link_coverage", {"alerts": alerts, "known_runbooks": known}, field)


def test_coverage_empty_unauthorized_and_probes():
    assert call("obs_runbook_link_coverage", {})["state"] == "INVALID_INPUT"
    assert call("obs_runbook_link_coverage", {"alerts": [alert_entry("a", "rb1")], "known_runbooks": ["rb1"]}, "A15")["state"] == "UNAUTHORIZED"
    assert coverage([alert_entry("a", "rb1")], probe="partial")["state"] == "PARTIAL_SUCCESS"
    assert coverage([alert_entry("a", "rb1")], probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"


def test_coverage_handler_called_directly():
    result = _a13().runbook_link_coverage({"alerts": [alert_entry("a", "r"), alert_entry("b", "q")], "known_runbooks": ["r"]})
    assert result["unlinked_alerts"] == ["b"] and result["coverage_ratio"] == 0.5


# --- obs_cardinality_estimate ------------------------------------------------------------------------


def lab(name, count) -> dict:
    return {"label": name, "distinct_values": count}


def cardinality(labels, budget_=10000, **over) -> dict:
    payload = {"labels": labels, "series_budget": budget_}
    payload.update(over)
    return call("obs_cardinality_estimate", payload)


HTTP_LABELS = [lab("method", 5), lab("status", 10), lab("path", 200)]


def test_cardinality_product_and_highest_label():
    assert cardinality(HTTP_LABELS) == {
        "state": "SUCCESS",
        "series_estimate": 10000,
        "exceeds_budget": False,
        "highest_cardinality_label": "path",
    }


def test_cardinality_budget_boundary_is_strictly_greater():
    assert cardinality(HTTP_LABELS, 9999)["exceeds_budget"] is True
    assert cardinality(HTTP_LABELS, 10000)["exceeds_budget"] is False
    assert cardinality(HTTP_LABELS, 10001)["exceeds_budget"] is False


def test_cardinality_single_label():
    result = cardinality([lab("a", 7)], 7)
    assert result["series_estimate"] == 7 and result["exceeds_budget"] is False and result["highest_cardinality_label"] == "a"


def test_cardinality_tie_for_highest_picks_the_first_listed():
    assert cardinality([lab("b", 4), lab("a", 4)], 100)["highest_cardinality_label"] == "b"
    assert cardinality([lab("a", 4), lab("b", 4)], 100)["highest_cardinality_label"] == "a"


def test_cardinality_highest_is_not_positional():
    assert cardinality([lab("a", 4), lab("b", 9), lab("c", 2)], 1000)["highest_cardinality_label"] == "b"


def test_cardinality_single_value_labels_multiply_to_one():
    result = cardinality([lab("env", 1), lab("region", 1)], 1)
    assert result["series_estimate"] == 1 and result["exceeds_budget"] is False


def test_cardinality_duplicate_label_names_rejected():
    result = cardinality([lab("a", 7), lab("a", 3)], 100)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "labels" and "duplicate" in result["message"]


def test_cardinality_label_names_are_case_sensitive():
    assert cardinality([lab("a", 2), lab("A", 3)], 100)["series_estimate"] == 6


def test_cardinality_maximum_labels_gives_exact_big_integer():
    labels = [lab(f"l{i}", 10**6) for i in range(50)]
    result = cardinality(labels, 10**9)
    assert result["series_estimate"] == 10**300 and result["exceeds_budget"] is True
    assert result["highest_cardinality_label"] == "l0"


@pytest.mark.parametrize(
    ("labels", "budget_", "field"),
    [
        ([], 100, "labels"),
        ([lab("a", 0)], 100, "labels"),
        ([lab("a", 3.5)], 100, "labels"),
        ([lab("", 3)], 100, "labels"),
        ([{"label": "a"}], 100, "labels"),
        ([{"label": "a", "distinct_values": 2, "x": 1}], 100, "labels"),
        ([lab(f"l{i}", 3) for i in range(51)], 1, "labels"),
        ([lab("a", 3)], 0, "series_budget"),
        ([lab("a", 3)], "100", "series_budget"),
        ("a=3", 100, "labels"),
    ],
)
def test_cardinality_malformed_input_names_the_field(labels, budget_, field):
    invalid("obs_cardinality_estimate", {"labels": labels, "series_budget": budget_}, field)


def test_cardinality_empty_unauthorized_and_probes():
    assert call("obs_cardinality_estimate", {})["state"] == "INVALID_INPUT"
    assert call("obs_cardinality_estimate", {"labels": [lab("a", 1)], "series_budget": 1}, "A07")["state"] == "UNAUTHORIZED"
    assert cardinality([lab("a", 1)], 1, probe="partial")["state"] == "PARTIAL_SUCCESS"
    assert cardinality([lab("a", 1)], 1, probe="sleep")["state"] == "TIMEOUT"


def test_cardinality_handler_called_directly():
    result = _a13().cardinality_estimate({"labels": [lab("a", 3), lab("b", 4)], "series_budget": 11})
    assert result["series_estimate"] == 12 and result["exceeds_budget"] is True and result["highest_cardinality_label"] == "b"


# --- obs_probe_availability --------------------------------------------------------------------------


def probes(results, minimum=0.5, **over) -> dict:
    payload = {"results": results, "min_availability": minimum}
    payload.update(over)
    return call("obs_probe_availability", payload)


def test_probes_availability_and_longest_failure_run():
    result = probes([True, True, False, False, False, True, False], 0.5)
    assert result == {
        "state": "SUCCESS",
        "probe_count": 7,
        "availability": 0.4286,
        "longest_failure_run": 3,
        "meets_target": False,
    }


def test_probes_all_up_has_no_failure_run():
    result = probes([True] * 4, 1)
    assert result["availability"] == 1.0 and result["longest_failure_run"] == 0 and result["meets_target"] is True


def test_probes_all_down_is_one_long_run():
    result = probes([False] * 4, 0)
    assert result["availability"] == 0.0 and result["longest_failure_run"] == 4 and result["meets_target"] is True


def test_probes_availability_at_target_meets_it():
    result = probes([True, False], 0.5)
    assert result["availability"] == 0.5 and result["meets_target"] is True
    assert probes([True, False], 0.51)["meets_target"] is False


def test_probes_failure_run_resets_on_success():
    assert probes([False, False, True, False])["longest_failure_run"] == 2
    assert probes([False, True, False, False, True, False])["longest_failure_run"] == 2
    assert probes([True, False, False, False])["longest_failure_run"] == 3  # run at the end counts


def test_probes_single_probe():
    assert probes([True], 1)["availability"] == 1.0
    result = probes([False], 0.01)
    assert result["availability"] == 0.0 and result["longest_failure_run"] == 1 and result["meets_target"] is False


def test_probes_availability_rounded_to_four_places_but_target_compared_unrounded():
    result = probes([True, False, False], 0.67)
    assert result["availability"] == 0.3333 and result["meets_target"] is False
    two_thirds = probes([True, True, False], 0.6667)
    assert two_thirds["availability"] == 0.6667 and two_thirds["meets_target"] is False  # compares 0.66666.. unrounded
    assert probes([True, True, False], 0.6666)["meets_target"] is True


def test_probes_size_limits():
    assert invalid("obs_probe_availability", {"results": [], "min_availability": 0.5}, "results")["message"] == "fewer than 1 items"
    assert invalid("obs_probe_availability", {"results": [True] * 10001, "min_availability": 1}, "results")["message"] == "more than 10000 items"
    result = probes([True] * 9999 + [False], 0.9999)
    assert result["probe_count"] == 10000 and result["availability"] == 0.9999 and result["meets_target"] is True


@pytest.mark.parametrize(
    ("results", "minimum", "field"),
    [
        ([1, 0], 0.5, "results"),
        (["true"], 0.5, "results"),
        ([None], 0.5, "results"),
        ([True], 1.1, "min_availability"),
        ([True], -0.1, "min_availability"),
        ([True], "1", "min_availability"),
        (True, 0.5, "results"),
    ],
)
def test_probes_malformed_input_names_the_field(results, minimum, field):
    invalid("obs_probe_availability", {"results": results, "min_availability": minimum}, field)


def test_probes_empty_unauthorized_and_probe_flag():
    assert call("obs_probe_availability", {})["state"] == "INVALID_INPUT"
    assert call("obs_probe_availability", {"results": [True], "min_availability": 1}, "A12")["state"] == "UNAUTHORIZED"
    assert probes([True], 1, probe="sleep")["state"] == "TIMEOUT"
    assert probes([True], 1, probe="partial")["completed_fraction"] == 0.5


def test_probes_handler_called_directly():
    result = _a13().probe_availability({"results": [True, False, False, True], "min_availability": 0.5})
    assert result["availability"] == 0.5 and result["longest_failure_run"] == 2 and result["meets_target"] is True


# --- scripts/obs_slo.py agrees with obs_multiwindow_alert at the exact boundaries (RV-H P3-4) -------------------


def _slo():
    return importlib.import_module("swarm.tools.support").script("obs_slo")


def _evaluate(tmp_path, good: int, total: int, objective: float = 0.999) -> dict:
    from types import SimpleNamespace

    args = SimpleNamespace(service="svc", sli="availability", objective=objective, window="28d", latency_ms=300.0, runbook="r.md",
                           events=None, now=None, good=good, total=total, suspect=None)
    ctx = SimpleNamespace(root=tmp_path, dry_run=True, correlation_id="C-1")
    return _slo().evaluate(args, ctx)


def test_error_burn_reports_the_exact_boundary_rate():
    """Repro: good=9856 total=10000 objective=0.999 gave burn_rate 14.399999999999956 (< 14.4) from a float quotient."""
    result = ok("obs_error_burn", {"good": 9856, "total": 10000, "objective": 0.999})
    assert result["burn_rate"] == 14.4
    assert ok("obs_error_burn", {"good": 9940, "total": 10000, "objective": 0.999})["burn_rate"] == 6.0
    assert ok("obs_error_burn", {"good": 9990, "total": 10000, "objective": 0.999})["burn_rate"] == 1.0


@pytest.mark.parametrize(
    ("good", "alert_sev", "action", "status"),
    [
        (9856, 1, "page", "fail"),    # exactly 14.4x over 1h
        (9857, 2, "page", "fail"),    # 14.3x: below the 14.4x page, still above 6x
        (9940, 2, "page", "fail"),    # exactly 6x
        (9941, 3, "ticket", "ok"),    # 5.9x: only the 1x ticket
        (9990, 3, "ticket", "ok"),    # exactly 1x
        (9991, None, None, "ok"),     # 0.9x: within budget
    ],
)
def test_slo_evaluate_alerts_at_the_exact_thresholds(tmp_path, good, alert_sev, action, status):
    result = _evaluate(tmp_path, good, 10000)
    assert result["status"] == status
    if alert_sev is None:
        assert result["alert"] is None
    else:
        assert (result["alert"]["sev"], result["alert"]["action"]) == (alert_sev, action)


@pytest.mark.parametrize("objective", [0.99, 0.999, 0.9995, 0.95, 0.9])
@pytest.mark.parametrize("total", [1000, 10000])
def test_slo_threshold_comparisons_equal_obs_multiwindow_alert_for_the_same_windows(objective, total):
    slo, a13 = _slo(), _a13()
    for errors in range(0, total // 4 + 1, max(1, total // 1000)):
        good = total - errors
        ratio = errors / total  # a terminating decimal: its shortest repr is exactly errors/total
        page_1h = slo.burn_at_least(good, total, objective, 14.4)
        page_6h = slo.burn_at_least(good, total, objective, 6.0)
        ticket = slo.burn_at_least(good, total, objective, 1.0)
        result = a13.multiwindow_alert({"error_ratio_1h": ratio, "error_ratio_6h": ratio, "error_ratio_3d": ratio, "slo_target": objective})
        assert result["page"] is (page_1h and page_6h), (objective, total, errors)
        assert result["ticket"] is ticket, (objective, total, errors)


def test_slo_burn_with_no_data_and_huge_counts_stays_exact_and_does_not_overflow():
    slo = _slo()
    assert slo.burn(0, 0, 0.999) == (None, None)
    assert slo.burn_at_least(0, 0, 0.999, 1.0) is False
    sli, rate = slo.burn(0, 10**400, 0.5)
    assert (sli, rate) == (0.0, 2.0)
    assert slo.burn_at_least(10**400 - 144 * 10**396, 10**400, 0.999, 14.4) is True
