"""Behaviour tests for the A20 FinOps & GreenOps tools: unit cost, rightsizing, commitments, budget burn, anomalies,
tag coverage, idle resources, storage tiering, egress, spot suitability, carbon and showback.

Every assertion is on output values for specific inputs, boundaries, INVALID_INPUT fields and callers. The registry
document is the live registry plus this lane's intake fragment while it is unmerged, so the file passes before and
after `scripts/_registry_intake.py --apply` merges and deletes the fragment.

tests/conftest.py evicts swarm* modules between tests: modules are resolved through importlib at call time.
"""
from __future__ import annotations

import copy
import functools
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT, SLUG, PREFIX = "A20", "a20-finops", "finops_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "finops_unit_cost", "finops_rightsize", "finops_commitment_coverage", "finops_budget_burn", "finops_spend_anomaly",
    "finops_tag_coverage", "finops_idle_resources", "finops_storage_tiering", "finops_egress_cost",
    "finops_spot_suitability", "finops_carbon_estimate", "finops_showback",
]


def _registry():
    return importlib.import_module("swarm.tool_registry")


def _fragment_entries() -> list[dict]:
    """Entries still waiting in the lane's intake fragment(s): <slug>.tools.json and <slug>.<shard>.tools.json."""
    entries: list[dict] = []
    for path in sorted(INTAKE.glob(f"{SLUG}.*tools.json")):
        entries.extend(json.loads(path.read_text(encoding="utf-8")))
    return entries


@functools.cache
def lane_document() -> dict:
    """The registry plus this lane's fragment entries that are not merged yet."""
    document = copy.deepcopy(_registry().load_document())
    agent = next(a for a in document["agents"] if a["id"] == AGENT)
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(e) for e in _fragment_entries() if e["name"] not in present)
    return document


def lane_tools() -> list[dict]:
    return next(a for a in lane_document()["agents"] if a["id"] == AGENT)["registered_tools"]


def call(name: str, payload, caller: str = AGENT) -> dict:
    return _registry().call_tool(name, caller, payload, data=lane_document())


def ok(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


def invalid(name: str, payload, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result
    return result


# --- registration ---------------------------------------------------------------------------------------------


def test_lane_registers_exactly_its_tools_as_read_only_owner_calls():
    tools = lane_tools()
    assert [t["name"] for t in tools] == TOOL_NAMES
    assert 10 <= len(tools) <= 15
    for tool in tools:
        suffix = tool["name"][len(PREFIX):]
        assert tool["name"].startswith(PREFIX) and tool["owner"] == AGENT and tool["permitted_callers"] == [AGENT]
        assert tool["side_effect_tier"] == "READ" and tool["idempotency"] == "idempotent"
        assert tool["entrypoint"] == f"swarm/tools/{AGENT.lower()}_tools.py"
        assert tool["handler"] == f"swarm.tools.{AGENT.lower()}_tools:{suffix}"
        assert "probe" in tool["input_schema"]["properties"] and "|" not in tool["purpose"]
        assert tool["output_schema"]["properties"]["state"] == {"type": "string", "const": "SUCCESS"}


def test_lane_entries_validate_in_a_copy_of_the_registry(tmp_path):
    copy_path = tmp_path / "agents.json"
    copy_path.write_text(json.dumps(lane_document()), encoding="utf-8")
    errors = _registry().validate_registry(copy_path)
    mine = [e for e in errors if AGENT in e or any(name in e for name in TOOL_NAMES)]
    assert mine == []


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_refuses_other_callers_and_malformed_payloads(name):
    sample = next(t for t in lane_tools() if t["name"] == name)["examples"]["success"]
    assert call(name, sample, caller="A01")["state"] == "UNAUTHORIZED"
    assert call(name, {})["state"] == "INVALID_INPUT"
    assert call(name, [sample])["field"] == "payload"
    assert call(name, {**sample, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert call(name, {**sample, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


# --- finops_unit_cost -----------------------------------------------------------------------------------------------


def unit(**over):
    payload = {"costs": [{"category": "compute", "amount": 6000}, {"category": "storage", "amount": 3000}, {"category": "network", "amount": 1000}], "volume": 2000000, "unit": "request"}
    payload.update(over)
    return ok("finops_unit_cost", payload)


def test_unit_cost_values_target_and_margin():
    result = unit(target_unit_cost=0.01, revenue_per_unit=0.02)
    assert {k: result[k] for k in result if k != "findings"} == {
        "state": "SUCCESS", "total_cost": 10000.0, "unit_cost": 0.005, "cost_per_1000_units": 5.0,
        "top_category": "compute", "top_category_share_pct": 60.0, "margin_known": True, "gross_margin_pct": 75.0,
        "verdict": "within_target",
    }
    assert result["findings"] == [{"id": "UC-CONCENTRATION", "severity": "info", "summary": "category compute is 60% of the cost"}]


def test_unit_cost_without_target_or_revenue():
    result = unit()
    assert (result["verdict"], result["margin_known"], result["gross_margin_pct"]) == ("no_target", False, 0.0)


def test_unit_cost_target_boundaries_and_severity_band():
    assert unit(target_unit_cost=0.005)["verdict"] == "within_target"  # equal is within
    minor = unit(target_unit_cost=0.004)  # 0.005 is exactly 1.25 x target: still minor
    assert minor["verdict"] == "over_target" and [f["severity"] for f in minor["findings"] if f["id"] == "UC-TARGET"] == ["minor"]
    major = unit(target_unit_cost=0.0039)
    assert [f["severity"] for f in major["findings"] if f["id"] == "UC-TARGET"] == ["major"]


def test_unit_cost_loss_making_when_cost_exceeds_revenue():
    assert unit(revenue_per_unit=0.005)["verdict"] == "no_target"  # break-even is not a loss
    loss = unit(revenue_per_unit=0.004, target_unit_cost=0.01)
    assert (loss["verdict"], loss["gross_margin_pct"]) == ("loss_making", -25.0)
    assert any(f["id"] == "UC-LOSS" and f["severity"] == "major" for f in loss["findings"])


def test_unit_cost_concentration_threshold_is_inclusive_at_sixty_percent():
    def split(a, b):
        return unit(costs=[{"category": "a", "amount": a}, {"category": "b", "amount": b}])

    assert [f["id"] for f in split(60, 40)["findings"]] == ["UC-CONCENTRATION"]
    assert split(59, 41)["findings"] == []


def test_unit_cost_merges_duplicate_categories_and_breaks_ties_by_name():
    merged = unit(costs=[{"category": "a", "amount": 1}, {"category": "a", "amount": 2}, {"category": "b", "amount": 2}], volume=3)
    assert (merged["top_category"], merged["total_cost"], merged["unit_cost"]) == ("a", 5.0, 1.66666667)
    tie = unit(costs=[{"category": "zeta", "amount": 1}, {"category": "alpha", "amount": 1}], volume=1)
    assert tie["top_category"] == "alpha"


def test_unit_cost_zero_spend_and_blank_category():
    zero = unit(costs=[{"category": "x", "amount": 0}])
    assert (zero["total_cost"], zero["unit_cost"], zero["top_category"]) == (0.0, 0.0, "")
    assert [f["id"] for f in zero["findings"]] == ["UC-NO-SPEND"]
    invalid("finops_unit_cost", {"costs": [{"category": "  ", "amount": 1}], "volume": 1, "unit": "user"}, "costs")


# --- finops_rightsize -----------------------------------------------------------------------------------------------


def workload(**over):
    base = {"name": "api", "replicas": 3, "cpu_request_m": 1000, "cpu_p95_m": 200, "cpu_p99_m": 300,
            "mem_request_mib": 2048, "mem_p95_mib": 500, "mem_p99_mib": 600}
    base.update(over)
    return base


def sized(workloads, **over):
    payload = {"workloads": workloads, "cpu_basis": "p95", "cpu_price_per_core_hour": 0.04, "mem_price_per_gib_hour": 0.005}
    payload.update(over)
    return ok("finops_rightsize", payload)


def test_rightsize_downsize_values_and_monthly_saving():
    result = sized([workload()])
    assert result["workloads"] == [{"name": "api", "action": "downsize", "cpu_request_m": 1000.0, "cpu_recommended_m": 230,
                                    "mem_request_mib": 2048.0, "mem_recommended_mib": 690, "monthly_saving": 81.97}]
    assert (result["downsize_count"], result["verdict"], result["monthly_saving_gross"], result["monthly_extra_cost"]) == (1, "rightsizing_needed", 81.97, 0.0)
    assert [f["id"] for f in result["findings"]] == ["RS-api-cpu", "RS-api-mem"]


@pytest.mark.parametrize("p95, recommended, action", [(781, 899, "downsize"), (782, 1000, "keep"), (956, 1000, "keep"), (957, 1101, "upsize")])
def test_rightsize_tolerance_band_is_inclusive(p95, recommended, action):
    # the formula gives 900 at p95 782 and 1100 at 956: exactly on the +-10 % band edge, so keep, and a kept dimension
    # reports its current request (1000) rather than a number the action does not name
    result = sized([workload(cpu_request_m=1000, cpu_p95_m=p95, cpu_p99_m=p95, mem_request_mib=1000, mem_p95_mib=800, mem_p99_mib=870)])
    record = result["workloads"][0]
    assert (record["cpu_recommended_m"], record["action"]) == (recommended, action)
    assert record["mem_recommended_mib"] == 1000  # ceil(870 x 1.15) = 1001 is inside the band of 1000: keep


def test_rightsize_cpu_basis_p99_and_custom_headroom():
    by_p99 = sized([workload(cpu_p95_m=100, cpu_p99_m=400)], cpu_basis="p99", cpu_headroom_pct=0)
    assert by_p99["workloads"][0]["cpu_recommended_m"] == 400
    by_p95 = sized([workload(cpu_p95_m=100, cpu_p99_m=400)], cpu_headroom_pct=0)
    assert by_p95["workloads"][0]["cpu_recommended_m"] == 100


def test_rightsize_cpu_downsize_is_withheld_while_p99_exceeds_the_request():
    result = sized([workload(cpu_request_m=1000, cpu_p95_m=100, cpu_p99_m=1500, mem_request_mib=700, mem_p95_mib=500, mem_p99_mib=600)])
    record = result["workloads"][0]
    assert (record["action"], record["cpu_recommended_m"]) == ("keep", 1000)  # the formula's 115 m would throttle at the 1500 m p99
    assert any(f["id"] == "RS-api-cpu-peak" and "withheld" in f["summary"] for f in result["findings"])
    assert result["keep_count"] == 1 and result["verdict"] == "right_sized"


def test_rightsize_memory_p99_above_request_is_an_upsize_with_a_major_finding():
    result = sized([workload(mem_request_mib=500, mem_p95_mib=450, mem_p99_mib=600, cpu_request_m=230)])
    record = result["workloads"][0]
    assert (record["action"], record["mem_recommended_mib"]) == ("upsize", 690)
    peak = [f for f in result["findings"] if f["id"] == "RS-api-mem-peak"]
    assert [f["severity"] for f in peak] == ["major"]


def test_rightsize_memory_peak_inside_the_tolerance_band_is_an_upsize():
    # headroom 0 puts the formula at the p99 itself (1050), inside the +-10 % band of 1000: a kept dimension would hide a peak above the request
    result = sized([workload(mem_request_mib=1000, mem_p95_mib=1000, mem_p99_mib=1050, cpu_request_m=230)], mem_headroom_pct=0)
    record = result["workloads"][0]
    assert (record["action"], record["mem_recommended_mib"], result["keep_count"]) == ("upsize", 1050, 0)
    assert result["verdict"] == "rightsizing_needed" and "RS-api-mem" in [f["id"] for f in result["findings"]]


def test_rightsize_mixed_action_and_gross_versus_extra_cost():
    result = sized([workload(replicas=1, cpu_request_m=1000, cpu_p95_m=100, cpu_p99_m=200, mem_request_mib=500, mem_p95_mib=400, mem_p99_mib=600)],
                   cpu_price_per_core_hour=1, mem_price_per_gib_hour=1, hours_per_month=100)
    assert result["workloads"][0]["action"] == "mixed" and result["mixed_count"] == 1
    assert (result["monthly_saving_gross"], result["monthly_extra_cost"], result["monthly_saving_net"]) == (88.5, 18.55, 69.95)
    assert result["workloads"][0]["monthly_saving"] == 69.95


def test_rightsize_recommendation_never_drops_below_one_unit():
    record = sized([workload(cpu_request_m=100, cpu_p95_m=0, cpu_p99_m=0, mem_request_mib=100, mem_p95_mib=0, mem_p99_mib=0)])["workloads"][0]
    assert (record["cpu_recommended_m"], record["mem_recommended_mib"]) == (1, 1)


def test_rightsize_rejects_inconsistent_percentiles_and_duplicate_names():
    prices = {"cpu_basis": "p95", "cpu_price_per_core_hour": 1, "mem_price_per_gib_hour": 1}
    invalid("finops_rightsize", {"workloads": [workload(cpu_p95_m=500, cpu_p99_m=400)], **prices}, "workloads")
    invalid("finops_rightsize", {"workloads": [workload(mem_p95_mib=700, mem_p99_mib=600)], **prices}, "workloads")
    invalid("finops_rightsize", {"workloads": [workload(), workload()], **prices}, "workloads")


# --- finops_commitment_coverage -------------------------------------------------------------------------------------


def commit(usage, commitment, discount=30, **over):
    return ok("finops_commitment_coverage", {"usage": usage, "commitment": commitment, "discount_pct": discount, **over})


def test_commitment_fully_utilized_within_band():
    result = commit([10, 12, 11, 15, 10, 9, 14, 13], 6)
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "period_count": 8, "coverage_pct": 72.9483, "utilization_pct": 100.0,
        "breakeven_utilization_pct": 70.0, "net_savings": 20.57, "unused_commitment": 0.0,
        "uncovered_on_demand": 25.43, "suggested_commitment": 6.3, "verdict": "within_band",
    }
    assert result["findings"] == []


def test_commitment_under_utilized_below_break_even_is_a_blocker():
    result = commit([10, 10, 2, 2], 7)  # cap 10: covered 24 of 40 capacity
    assert (result["utilization_pct"], result["coverage_pct"], result["verdict"]) == (60.0, 100.0, "under_utilized")
    assert (result["net_savings"], result["unused_commitment"]) == (-4.0, 11.2)
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("CC-UTIL", "major"), ("CC-BREAKEVEN", "blocker")]


def test_commitment_utilization_floor_is_strict():
    at_floor = commit([10] * 9 + [0], 7)  # exactly 90 %
    assert (at_floor["utilization_pct"], at_floor["verdict"]) == (90.0, "within_band")
    below = commit([10] * 8 + [0, 0], 7)  # 80 %: under the floor but above the 70 % break-even
    assert (below["verdict"], [f["id"] for f in below["findings"]]) == ("under_utilized", ["CC-UTIL"])


def test_commitment_break_even_is_one_minus_the_discount():
    exact = commit([10] * 7 + [0] * 3, 7, min_utilization_pct=0)  # 70 % utilization == 1 - discount: zero net savings
    assert (exact["utilization_pct"], exact["net_savings"], exact["findings"]) == (70.0, 0.0, [])
    worse = commit([10] * 6 + [0] * 4, 7, min_utilization_pct=0)
    assert (worse["net_savings"], [f["id"] for f in worse["findings"]]) == (-10.0, ["CC-BREAKEVEN"])  # covered 60 - paid 70


def test_commitment_coverage_floor_is_inclusive():
    assert commit([20] * 4, 9.8)["verdict"] == "within_band"  # coverage exactly 70 %
    short = commit([20] * 4, 9.7)
    assert (short["verdict"], short["coverage_pct"]) == ("under_covered", 69.2857)
    assert [f["id"] for f in short["findings"]] == ["CC-COVERAGE"]


def test_commitment_custom_bands_and_zero_usage():
    assert commit([20] * 4, 9.7, min_coverage_pct=69)["verdict"] == "within_band"
    idle = commit([0, 0], 1)
    assert (idle["coverage_pct"], idle["utilization_pct"], idle["verdict"], idle["suggested_commitment"]) == (0.0, 0.0, "under_utilized", 0.0)


def test_commitment_baseline_is_the_nearest_rank_tenth_percentile():
    assert commit([11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1], 1)["suggested_commitment"] == 1.4  # rank ceil(11 / 10) = 2 -> 2 x 0.7
    assert commit([5, 4, 3, 2, 1, 6, 7, 8, 9, 10], 1)["suggested_commitment"] == 0.7  # rank 1 of 10 -> 1 x 0.7


# --- finops_budget_burn ---------------------------------------------------------------------------------------------


def burn(**over):
    payload = {"budget": 10000, "spend_to_date": 6000, "period_start": "2026-10-01T00:00:00Z", "period_end": "2026-11-01T00:00:00Z", "as_of": "2026-10-11T00:00:00Z"}
    payload.update(over)
    return ok("finops_budget_burn", payload)


def test_budget_burn_projects_the_overrun_instant():
    result = burn()
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "pct_of_budget_used": 60.0, "pct_of_period_elapsed": 32.2581, "burn_ratio": 1.86,
        "run_rate_per_day": 600.0, "run_rate_source": "period_average", "projected_total": 18600.0,
        "projected_overrun_amount": 8600.0, "overrun_at": "2026-10-17T16:00:00Z", "days_until_overrun": 6.6667,
        "verdict": "will_overrun",
    }
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("BB-OVERRUN", "major")]


SHORT = {"period_start": "2026-10-01T00:00:00Z", "period_end": "2026-10-11T00:00:00Z", "as_of": "2026-10-06T00:00:00Z", "budget": 1000}


def test_budget_burn_verdict_boundaries():
    assert burn(**SHORT, spend_to_date=449)["verdict"] == "on_track"  # projected 898.0
    at_risk = burn(**SHORT, spend_to_date=450)  # projected exactly 90 % of the budget
    assert (at_risk["verdict"], at_risk["projected_total"], at_risk["overrun_at"], at_risk["days_until_overrun"]) == ("at_risk", 900.0, "", -1.0)
    assert burn(**SHORT, spend_to_date=500)["verdict"] == "at_risk"  # projected exactly the budget: not an overrun
    assert burn(**SHORT, spend_to_date=501)["verdict"] == "will_overrun"


def test_budget_burn_spend_at_budget_is_over_budget_with_zero_days():
    result = burn(**SHORT, spend_to_date=1000)
    assert (result["verdict"], result["days_until_overrun"], result["overrun_at"]) == ("over_budget", 0.0, "")
    assert [f["id"] for f in result["findings"]] == ["BB-OVER"]


def test_money_in_summaries_prints_full_digits_above_a_million():
    over = burn(**{**SHORT, "budget": 1000000}, spend_to_date=1000000)
    assert [f["summary"] for f in over["findings"]] == ["spend 1000000 has reached or exceeded the budget 1000000"]
    big = tagged([res("big", 1234567.89, environment="prod")], required=("owner", "environment"))
    assert [f["summary"] for f in big["findings"]] == ["big (1234568/month) missing: owner"]


def test_budget_burn_uses_the_recent_run_rate_when_supplied():
    result = burn(**SHORT, spend_to_date=450, recent_daily_spend=[100, 200])
    assert (result["run_rate_source"], result["run_rate_per_day"], result["projected_total"]) == ("recent_daily_spend", 150.0, 1200.0)
    assert (result["verdict"], result["overrun_at"], result["days_until_overrun"]) == ("will_overrun", "2026-10-09T16:00:00Z", 3.6667)


def test_budget_burn_zero_run_rate_never_overruns():
    result = burn(**SHORT, spend_to_date=100, recent_daily_spend=[0])
    assert (result["verdict"], result["projected_total"], result["days_until_overrun"]) == ("on_track", 100.0, -1.0)


def test_budget_burn_offsets_are_normalised_and_period_end_is_inclusive():
    shifted = burn(**{**SHORT, "as_of": "2026-10-06T05:30:00+05:30"}, spend_to_date=450)
    assert shifted == burn(**SHORT, spend_to_date=450)
    at_end = burn(**{**SHORT, "as_of": "2026-10-11T00:00:00Z"}, spend_to_date=900)
    assert (at_end["pct_of_period_elapsed"], at_end["projected_total"], at_end["verdict"]) == (100.0, 900.0, "at_risk")


@pytest.mark.parametrize("over, field", [
    ({"as_of": "2026-10-11T00:00:00"}, "as_of"),
    ({"period_start": "yesterday"}, "period_start"),
    ({"period_end": "2026-10-01T00:00:00Z"}, "period_end"),
    ({"as_of": "2026-10-01T00:00:00Z"}, "as_of"),
    ({"as_of": "2026-11-01T00:00:01Z"}, "as_of"),
])
def test_budget_burn_rejects_bad_timestamps_and_ordering(over, field):
    payload = {"budget": 10000, "spend_to_date": 6000, "period_start": "2026-10-01T00:00:00Z", "period_end": "2026-11-01T00:00:00Z", "as_of": "2026-10-11T00:00:00Z", **over}
    invalid("finops_budget_burn", payload, field)


# --- finops_spend_anomaly -------------------------------------------------------------------------------------------


def anomaly(series, **over):
    return ok("finops_spend_anomaly", {"daily_spend": series, "start_date": "2026-10-01", **over})


def test_spend_anomaly_flags_a_spike_with_mad_scale():
    result = anomaly([100, 102, 98, 101, 99, 100, 103, 500, 100, 101])
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "day_count": 10, "median": 100.5, "mad": 1.0, "scale_method": "mad", "anomaly_count": 1,
        "spike_count": 1, "excess_spend": 399.5, "verdict": "anomalous",
        "anomalies": [{"index": 7, "date": "2026-10-08", "value": 500.0, "z_score": 269.463, "direction": "up"}],
    }
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("SA-2026-10-08", "major")]


SERIES = [10, 10, 10, 11, 9, 12, 8]  # median 10, MAD 1: the values 12 and 8 sit at |M| = 0.6745 x 2 = 1.349


def test_spend_anomaly_threshold_is_exclusive():
    assert anomaly(SERIES, threshold=1.349)["anomaly_count"] == 0
    result = anomaly(SERIES, threshold=1.348)
    assert [(a["index"], a["direction"], a["z_score"]) for a in result["anomalies"]] == [(5, "up", 1.349), (6, "down", -1.349)]
    assert [f["severity"] for f in result["findings"]] == ["minor", "info"]
    assert (result["spike_count"], result["excess_spend"]) == (1, 2.0)


def test_spend_anomaly_default_threshold_is_three_and_a_half():
    normal = anomaly(SERIES)
    assert (normal["verdict"], normal["anomalies"]) == ("normal", [])
    # |M| = 0.6745 x d > 3.5 needs d > 5.1890: 15.18 stays inside the cut-off, 15.2 is a spike on a baseline of 10
    assert anomaly([10, 10, 10, 11, 9, 15.18, 8])["anomaly_count"] == 0
    assert anomaly([10, 10, 10, 11, 9, 15.2, 8])["anomaly_count"] == 1


def test_spend_anomaly_mean_absolute_deviation_fallback_when_mad_is_zero():
    result = anomaly([100] * 6 + [200])
    assert (result["scale_method"], result["mad"], result["anomalies"][0]["z_score"]) == ("mean_ad", 0.0, 5.585)
    down = anomaly([100] * 6 + [0])
    assert (down["verdict"], down["spike_count"], down["excess_spend"], down["findings"][0]["severity"]) == ("anomalous", 0, 0.0, "info")


def test_spend_anomaly_constant_series_has_none():
    result = anomaly([50] * 7)
    assert (result["scale_method"], result["anomaly_count"], result["verdict"], result["median"]) == ("constant", 0, "normal", 50.0)


@pytest.mark.parametrize("start", ["2026-02-30", "2026/10/01", "2026-10-1x", "2026-W40-1", "9999-12-30"])
def test_spend_anomaly_rejects_a_bad_start_date(start):
    # 2026-W40-1 is an ISO week date that date.fromisoformat would accept; 9999-12-30 + 6 days leaves the calendar
    invalid("finops_spend_anomaly", {"daily_spend": [1] * 7, "start_date": start}, "start_date")


def test_spend_anomaly_requires_a_week_of_data():
    invalid("finops_spend_anomaly", {"daily_spend": [1] * 6, "start_date": "2026-10-01"}, "daily_spend")


# --- finops_tag_coverage --------------------------------------------------------------------------------------------


def res(rid, cost, **tag_map):
    return {"id": rid, "monthly_cost": cost, "tags": [{"key": k, "value": v} for k, v in tag_map.items()]}


def tagged(resources, required=("owner", "env"), **over):
    return ok("finops_tag_coverage", {"resources": resources, "required_keys": list(required), **over})


def test_tag_coverage_values_case_mismatch_and_missing():
    result = tagged([res("vm-1", 100, owner="team-a", env="prod"), res("vm-2", 50, Owner="team-b")])
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "resource_count": 2, "compliant_count": 1, "coverage_pct": 50.0, "cost_coverage_pct": 66.6667,
        "untagged_cost": 50.0, "problem_counts": [{"problem": "case_mismatch", "count": 1}, {"problem": "missing", "count": 1}],
        "verdict": "non_compliant", "findings_truncated": False,
    }
    assert result["findings"] == [{"id": "vm-2", "severity": "major", "summary": "vm-2 (50/month) case_mismatch: owner; missing: env"}]


def test_tag_coverage_empty_invalid_and_duplicate_values():
    allowed = [{"key": "env", "values": ["prod", "dev"]}]
    result = tagged([res("a", 3, owner="  ", env="prod"), res("b", 2, owner="x", env="qa"), res("c", 1, owner="x", env="dev")], allowed_values=allowed)
    assert result["problem_counts"] == [{"problem": "empty_value", "count": 1}, {"problem": "invalid_value", "count": 1}]
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("a", "major"), ("b", "minor")]
    twice = {"id": "d", "monthly_cost": 1, "tags": [{"key": "owner", "value": "x"}, {"key": "owner", "value": "y"}]}
    duplicated = ok("finops_tag_coverage", {"resources": [twice], "required_keys": ["owner"]})
    assert duplicated["problem_counts"] == [{"problem": "duplicate_key", "count": 1}] and duplicated["findings"][0]["severity"] == "minor"


def test_tag_coverage_threshold_is_inclusive():
    four = [res("a", 1, owner="x", env="p"), res("b", 1, owner="x", env="p"), res("c", 1, owner="x", env="p"), res("d", 1)]
    assert tagged(four, min_coverage_pct=75)["verdict"] == "compliant"
    assert tagged(four, min_coverage_pct=75.1)["verdict"] == "non_compliant"
    assert tagged(four)["verdict"] == "non_compliant"  # default minimum is 95


def test_tag_coverage_zero_cost_falls_back_to_resource_coverage():
    result = tagged([res("a", 0, owner="x", env="p"), res("b", 0)])
    assert (result["coverage_pct"], result["cost_coverage_pct"], result["untagged_cost"]) == (50.0, 50.0, 0.0)


def test_tag_coverage_findings_are_ordered_by_cost_and_capped_at_one_hundred():
    result = tagged([res(f"r{i:03d}", i % 7) for i in range(120)])
    assert result["findings_truncated"] is True and len(result["findings"]) == 100
    assert result["resource_count"] == 120 and result["compliant_count"] == 0
    costs = [float(f["summary"].split("(")[1].split("/")[0]) for f in result["findings"]]
    assert costs == sorted(costs, reverse=True) and costs[0] == 6.0


def test_tag_coverage_valid_allowed_value_is_compliant():
    result = tagged([res("a", 1, owner="x", env="dev")], allowed_values=[{"key": "env", "values": ["dev", "prod"]}])
    assert (result["verdict"], result["findings"]) == ("compliant", [])


@pytest.mark.parametrize("over, field", [
    ({"resources": [res("a", 1), res("a", 2)]}, "resources"),
    ({"required_keys": ["owner", "owner"]}, "required_keys"),
    ({"allowed_values": [{"key": "team", "values": ["x"]}]}, "allowed_values"),
    ({"allowed_values": [{"key": "env", "values": ["x"]}, {"key": "env", "values": ["y"]}]}, "allowed_values"),
])
def test_tag_coverage_rejects_inconsistent_input(over, field):
    invalid("finops_tag_coverage", {"resources": [res("a", 1)], "required_keys": ["owner", "env"], **over}, field)


# --- finops_idle_resources ------------------------------------------------------------------------------------------


def idle(resources, threshold=5, days=3):
    return ok("finops_idle_resources", {"resources": resources, "threshold_pct": threshold, "min_idle_days": days})


def rs(rid, series, cost=10, kind="vm"):
    return {"id": rid, "kind": kind, "daily_utilization_pct": series, "monthly_cost": cost}


def test_idle_resources_trailing_run_and_severity():
    result = idle([rs("vm-1", [1, 2, 1, 3, 2, 1, 1], 120), rs("vm-2", [40, 50, 3, 2, 60, 55, 45], 80), rs("vm-3", [90, 1, 1, 1], 30)], days=3)
    assert result["idle"] == [
        {"id": "vm-1", "kind": "vm", "idle_days": 7, "monthly_cost": 120.0, "recommendation": "schedule_or_stop"},
        {"id": "vm-3", "kind": "vm", "idle_days": 3, "monthly_cost": 30.0, "recommendation": "schedule_or_stop"},
    ]
    assert (result["resource_count"], result["idle_count"], result["idle_monthly_cost"], result["verdict"]) == (3, 2, 150.0, "idle_found")
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("vm-1", "major"), ("vm-3", "minor")]


def test_idle_resources_threshold_is_strict_and_only_the_trailing_days_count():
    assert idle([rs("a", [5, 1, 1])], days=3)["idle_count"] == 0  # 5 is not below 5
    assert idle([rs("a", [5, 1, 1])], days=2)["idle"][0]["idle_days"] == 2
    assert idle([rs("a", [1, 1, 1, 90])], days=3)["idle_count"] == 0
    assert idle([rs("a", [1, 1])], days=2)["idle_count"] == 1


def test_idle_resources_short_series_is_insufficient_not_idle():
    result = idle([rs("a", [0, 0])], days=3)
    assert (result["idle_count"], result["insufficient_data_count"], result["verdict"]) == (0, 1, "clean")
    assert result["findings"] == [{"id": "a", "severity": "info", "summary": "a: fewer than 3 daily samples, idleness cannot be judged"}]


def test_idle_resources_orders_by_cost_then_id_and_maps_every_kind():
    kinds = ["vm", "database", "volume", "load_balancer", "ip_address"]
    result = idle([rs(f"r-{kind}", [0, 0, 0], 10, kind) for kind in kinds])
    assert [i["id"] for i in result["idle"]] == sorted(f"r-{k}" for k in kinds)
    assert {i["kind"]: i["recommendation"] for i in result["idle"]} == {
        "vm": "schedule_or_stop", "database": "snapshot_and_stop", "volume": "snapshot_and_review",
        "load_balancer": "review_targets", "ip_address": "review_attachment",
    }
    costly = idle([rs("b", [0] * 3, 5), rs("a", [0] * 3, 5), rs("c", [0] * 3, 50)])
    assert [i["id"] for i in costly["idle"]] == ["c", "a", "b"]


def test_idle_resources_rejects_duplicate_ids():
    invalid("finops_idle_resources", {"resources": [rs("a", [0]), rs("a", [0])], "threshold_pct": 5, "min_idle_days": 1}, "resources")


# --- finops_storage_tiering -----------------------------------------------------------------------------------------

STANDARD = {"name": "standard", "storage_per_gb_month": 0.023, "retrieval_per_gb": 0, "min_storage_days": 0, "transition_in_per_gb": 0}
INFREQUENT = {"name": "infrequent", "storage_per_gb_month": 0.0125, "retrieval_per_gb": 0.01, "min_storage_days": 30, "transition_in_per_gb": 0.01}
ARCHIVE = {"name": "archive", "storage_per_gb_month": 0.004, "retrieval_per_gb": 0.03, "min_storage_days": 90, "transition_in_per_gb": 0.03}


def dataset(**over):
    base = {"name": "logs", "size_gb": 10000, "monthly_read_gb": 100, "retention_days": 365, "current_tier": "standard"}
    base.update(over)
    return base


def tiering(tiers, datasets):
    return ok("finops_storage_tiering", {"tiers": tiers, "datasets": datasets})


def test_storage_tiering_moves_to_the_cheaper_tier_net_of_transition():
    result = tiering([STANDARD, INFREQUENT], [dataset()])
    assert result["datasets"] == [{
        "name": "logs", "current_tier": "standard", "recommended_tier": "infrequent", "monthly_cost_now": 230.0,
        "monthly_cost_after": 126.0, "monthly_saving": 104.0, "transition_cost": 100.0,
        "net_saving_over_retention": 1165.33, "payback_months": 0.9615,
    }]
    assert (result["move_count"], result["monthly_saving_total"], result["transition_cost_total"], result["verdict"]) == (1, 104.0, 100.0, "tiering_available")
    assert [f["id"] for f in result["findings"]] == ["ST-logs"]


def test_storage_tiering_picks_the_largest_net_benefit_among_eligible_tiers():
    assert tiering([STANDARD, INFREQUENT, ARCHIVE], [dataset()])["datasets"][0]["recommended_tier"] == "archive"
    assert tiering([STANDARD, INFREQUENT, ARCHIVE], [dataset(retention_days=60)])["datasets"][0]["recommended_tier"] == "infrequent"


def test_storage_tiering_minimum_storage_duration_is_inclusive():
    assert tiering([STANDARD, INFREQUENT], [dataset(retention_days=29)])["datasets"][0]["recommended_tier"] == "standard"
    assert tiering([STANDARD, INFREQUENT], [dataset(retention_days=30)])["datasets"][0]["recommended_tier"] == "infrequent"


def test_storage_tiering_requires_a_strictly_positive_net_benefit():
    # saving 104/month x 1 month = 104; a transition cost of exactly 104 nets zero and the dataset stays
    even = {**INFREQUENT, "transition_in_per_gb": 0.0104}
    stay = tiering([STANDARD, even], [dataset(retention_days=30)])
    assert stay["datasets"][0]["recommended_tier"] == "standard" and stay["move_count"] == 0 and stay["verdict"] == "optimal"
    assert stay["datasets"][0]["monthly_saving"] == 0.0 and stay["findings"] == []
    cheaper = {**INFREQUENT, "transition_in_per_gb": 0.0103}
    assert tiering([STANDARD, cheaper], [dataset(retention_days=30)])["datasets"][0]["recommended_tier"] == "infrequent"


def test_storage_tiering_keeps_read_heavy_data_where_retrieval_is_free():
    result = tiering([STANDARD, INFREQUENT], [dataset(monthly_read_gb=20000)])  # IA would cost 325 against 230
    record = result["datasets"][0]
    assert (record["recommended_tier"], record["monthly_cost_now"], record["monthly_cost_after"]) == ("standard", 230.0, 230.0)


def test_storage_tiering_rejects_inconsistent_input():
    invalid("finops_storage_tiering", {"tiers": [STANDARD, STANDARD], "datasets": [dataset()]}, "tiers")
    invalid("finops_storage_tiering", {"tiers": [STANDARD, INFREQUENT], "datasets": [dataset(current_tier="glacier")]}, "datasets")
    invalid("finops_storage_tiering", {"tiers": [STANDARD, INFREQUENT], "datasets": [dataset(), dataset()]}, "datasets")


# --- finops_egress_cost ---------------------------------------------------------------------------------------------

RATES = {"cross_az_per_gb": 0.02, "cross_region_per_gb": 0.02, "nat_processing_per_gb": 0.045}
TIERS = [{"starts_at_gb": 0, "per_gb": 0}, {"starts_at_gb": 100, "per_gb": 0.09}]


def flow(name, kind, gb, nat=False):
    return {"name": name, "kind": kind, "monthly_gb": gb, "via_nat": nat}


def egress(flows, rates=RATES, tiers=TIERS):
    return ok("finops_egress_cost", {"flows": flows, "rates": rates, "internet_tiers": tiers})


def test_egress_cost_categories_nat_and_flow_attribution():
    result = egress([flow("api-to-db", "cross_az", 20000), flow("downloads", "internet", 5000, nat=True)])
    assert result["total_gb"] == 25000.0 and result["total_cost"] == 1066.0
    assert result["breakdown"] == [
        {"category": "cross_az", "gb": 20000.0, "cost": 400.0}, {"category": "cross_region", "gb": 0.0, "cost": 0.0},
        {"category": "internet", "gb": 5000.0, "cost": 441.0}, {"category": "nat_processing", "gb": 5000.0, "cost": 225.0},
    ]
    assert result["top_flows"] == [{"name": "downloads", "kind": "internet", "gb": 5000.0, "cost": 666.0}, {"name": "api-to-db", "kind": "cross_az", "gb": 20000.0, "cost": 400.0}]
    assert (result["dominant_category"], result["findings"]) == ("", [])


def test_egress_internet_tiers_are_cumulative_and_the_free_allowance_is_a_zero_tier():
    tiers = [{"starts_at_gb": 0, "per_gb": 0}, {"starts_at_gb": 100, "per_gb": 0.09}, {"starts_at_gb": 10100, "per_gb": 0.085}]
    assert egress([flow("d", "internet", 20100)], tiers=tiers)["total_cost"] == 1750.0  # 10000 x 0.09 + 10000 x 0.085
    assert egress([flow("d", "internet", 100)], tiers=tiers)["total_cost"] == 0.0
    assert egress([flow("d", "internet", 1100)], tiers=tiers)["total_cost"] == 90.0
    assert egress([flow("a", "internet", 60), flow("b", "internet", 60)], tiers=tiers)["total_cost"] == 1.8  # 120 GB in total


def test_egress_nat_processing_applies_to_any_flow_without_double_counting_volume():
    result = egress([flow("svc", "cross_az", 10, nat=True)])
    assert result["total_gb"] == 10.0
    assert result["total_cost"] == 0.65  # 10 x 0.02 + 10 x 0.045
    assert result["top_flows"][0]["cost"] == 0.65


def test_egress_dominant_category_threshold_is_inclusive_at_half():
    rates = {"cross_az_per_gb": 0.5, "cross_region_per_gb": 0, "nat_processing_per_gb": 0}

    def tier(rate):
        return [{"starts_at_gb": 0, "per_gb": rate}]

    half = egress([flow("a", "cross_az", 100), flow("b", "internet", 100)], rates, tier(0.5))
    assert half["dominant_category"] == "cross_az" and half["findings"][0]["id"] == "EG-cross_az"
    internet = egress([flow("a", "cross_az", 100), flow("b", "internet", 100)], rates, tier(0.51))
    assert internet["dominant_category"] == "internet"
    balanced = egress([flow("a", "cross_az", 100), flow("b", "internet", 100), flow("c", "cross_region", 100)], {**rates, "cross_region_per_gb": 0.5}, tier(0.5))
    assert balanced["dominant_category"] == ""


def test_egress_advice_names_the_remedy():
    result = egress([flow("x", "cross_region", 1000)])
    assert result["dominant_category"] == "cross_region" and "replicate or cache" in result["findings"][0]["summary"]


def test_egress_top_flows_are_capped_at_ten_and_ordered_by_cost_then_name():
    flows = [flow(f"f{i:02d}", "cross_az", 10) for i in range(12)] + [flow("big", "cross_az", 500)]
    result = egress(flows)
    assert [f["name"] for f in result["top_flows"]] == ["big"] + [f"f{i:02d}" for i in range(9)]


@pytest.mark.parametrize("tiers", [
    [{"starts_at_gb": 1, "per_gb": 0.1}],
    [{"starts_at_gb": 0, "per_gb": 0.1}, {"starts_at_gb": 0, "per_gb": 0.2}],
    [{"starts_at_gb": 0, "per_gb": 0.1}, {"starts_at_gb": 50, "per_gb": 0.2}, {"starts_at_gb": 40, "per_gb": 0.3}],
])
def test_egress_rejects_malformed_tier_tables(tiers):
    invalid("finops_egress_cost", {"flows": [flow("a", "internet", 1)], "rates": RATES, "internet_tiers": tiers}, "internet_tiers")


# --- finops_spot_suitability ----------------------------------------------------------------------------------------


def spot_w(**over):
    base = {"name": "batch", "replicas": 10, "on_demand_hourly": 0.1, "spot_hourly": 0.03, "interruption_pct_month": 8,
            "fault_tolerance": "checkpointed", "recovery_minutes": 5, "checkpoint_interval_minutes": 10}
    base.update(over)
    return base


def spot(workloads, **over):
    return ok("finops_spot_suitability", {"workloads": workloads, **over})


def test_spot_checkpointed_workload_values():
    result = spot([spot_w()])
    assert result["workloads"] == [{"name": "batch", "verdict": "suitable", "reason": "ok", "effective_spot_hourly": 0.030001,
                                    "savings_pct": 69.9995, "spot_replicas": 8, "monthly_saving": 408.8}]
    assert (result["suitable_count"], result["monthly_saving_total"], result["verdict"]) == (1, 408.8, "spot_candidates")
    assert result["findings"][0]["id"] == "SP-batch" and result["findings"][0]["severity"] == "minor"


@pytest.mark.parametrize("over, verdict, reason", [
    ({"fault_tolerance": "stateful"}, "unsuitable", "stateful-no-checkpoint"),
    ({"replicas": 1}, "unsuitable", "single-replica"),
    ({"interruption_pct_month": 20}, "suitable", "ok"),
    ({"interruption_pct_month": 20.5}, "unsuitable", "interruption-rate-above-max"),
    ({"fault_tolerance": "stateless", "recovery_minutes": 0, "spot_hourly": 0.07}, "suitable", "ok"),
    ({"fault_tolerance": "stateless", "recovery_minutes": 0, "spot_hourly": 0.0701}, "marginal", "savings-below-minimum"),
])
def test_spot_verdict_boundaries(over, verdict, reason):
    record = spot([spot_w(**over)])["workloads"][0]
    assert (record["verdict"], record["reason"]) == (verdict, reason)


def test_spot_interruption_overhead_is_recovery_plus_half_the_checkpoint_interval():
    # 100 % monthly interruption and 438 lost minutes = 1 % of the 43800-minute month: effective = spot / 0.99
    stateless = spot([spot_w(interruption_pct_month=100, recovery_minutes=438, fault_tolerance="stateless")], max_interruption_pct=100)["workloads"][0]
    assert stateless["effective_spot_hourly"] == round(0.03 / 0.99, 6)
    checkpointed = spot([spot_w(interruption_pct_month=100, recovery_minutes=218, checkpoint_interval_minutes=440)], max_interruption_pct=100)["workloads"][0]
    assert checkpointed["effective_spot_hourly"] == round(0.03 / 0.99, 6)  # 218 + 440 / 2 = 438 minutes


@pytest.mark.parametrize("replicas, floor, spot_replicas", [(2, 20, 1), (5, 40, 3), (10, 0, 9), (10, 20, 8), (3, 100, 0)])
def test_spot_on_demand_floor(replicas, floor, spot_replicas):
    record = spot([spot_w(replicas=replicas)], on_demand_floor_pct=floor)["workloads"][0]
    assert record["spot_replicas"] == spot_replicas


def test_spot_counts_and_unsuitable_findings_are_informational():
    result = spot([spot_w(name="a"), spot_w(name="b", fault_tolerance="stateful"), spot_w(name="c", spot_hourly=0.09, recovery_minutes=0, fault_tolerance="stateless")])
    assert (result["suitable_count"], result["unsuitable_count"], result["marginal_count"]) == (1, 1, 1)
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("SP-a", "minor"), ("SP-b", "info"), ("SP-c", "info")]
    assert spot([spot_w(fault_tolerance="stateful")])["verdict"] == "none"


def test_spot_rejects_checkpointed_without_interval_and_duplicate_names():
    no_interval = spot_w()
    del no_interval["checkpoint_interval_minutes"]
    invalid("finops_spot_suitability", {"workloads": [no_interval]}, "workloads")
    invalid("finops_spot_suitability", {"workloads": [spot_w(), spot_w()]}, "workloads")


# --- finops_carbon_estimate -----------------------------------------------------------------------------------------


def grid(region, g, eligible=True):
    return {"region": region, "g_co2e_per_kwh": g, "eligible": eligible}


def carbon(workloads, grids, pue=1.2, **over):
    return ok("finops_carbon_estimate", {"workloads": workloads, "pue": pue, "grid_intensity": grids, **over})


def cw(name="etl", region="region-a", watts=200, hours=720):
    return {"name": name, "region": region, "avg_power_w": watts, "hours": hours}


def test_carbon_energy_emissions_and_relocation_saving():
    result = carbon([cw()], [grid("region-a", 400), grid("region-b", 50)])
    assert {k: v for k, v in result.items() if k != "findings"} == {
        "state": "SUCCESS", "total_energy_kwh": 172.8, "total_kg_co2e": 69.12, "best_region": "region-b",
        "potential_saving_kg": 60.48, "potential_saving_pct": 87.5,
        "region_breakdown": [{"region": "region-a", "kg_co2e": 69.12}],
        "workloads": [{"name": "etl", "region": "region-a", "energy_kwh": 172.8, "kg_co2e": 69.12, "potential_saving_kg": 60.48}],
        "verdict": "relocation_candidates",
    }
    assert result["findings"] == [{"id": "CO-etl", "severity": "info", "summary": "etl: moving region-a -> region-b avoids 60.48 kgCO2e"}]


def test_carbon_saving_threshold_is_inclusive():
    assert carbon([cw()], [grid("region-a", 100), grid("region-b", 90)])["verdict"] == "relocation_candidates"  # exactly 10 %
    assert carbon([cw()], [grid("region-a", 100), grid("region-b", 90.1)])["verdict"] == "no_action"
    assert carbon([cw()], [grid("region-a", 100), grid("region-b", 90.1)], min_saving_pct=9.9)["verdict"] == "relocation_candidates"


def test_carbon_best_region_respects_eligibility_and_ties_by_name():
    result = carbon([cw()], [grid("region-a", 400), grid("zz", 10, eligible=False), grid("region-c", 50), grid("region-b", 50)])
    assert result["best_region"] == "region-b"
    none = carbon([cw()], [grid("region-a", 400, eligible=False), grid("region-b", 50, eligible=False)])
    assert (none["best_region"], none["potential_saving_kg"], none["verdict"], none["findings"]) == ("", 0.0, "no_action", [])


def test_carbon_saving_is_never_negative_and_breakdown_is_sorted():
    result = carbon([cw("a", "region-b"), cw("b", "region-a", watts=100)], [grid("region-a", 400), grid("region-b", 50), grid("region-c", 20, eligible=False)])
    by_name = {w["name"]: w for w in result["workloads"]}
    assert by_name["a"]["potential_saving_kg"] == 0.0 and by_name["b"]["potential_saving_kg"] == 30.24
    assert [r["region"] for r in result["region_breakdown"]] == ["region-a", "region-b"]


def test_carbon_pue_scales_energy_linearly():
    assert carbon([cw()], [grid("region-a", 400)], pue=1.0)["total_energy_kwh"] == 144.0


def test_carbon_rejects_unknown_region_and_duplicates():
    invalid("finops_carbon_estimate", {"workloads": [cw(region="nowhere")], "pue": 1.2, "grid_intensity": [grid("region-a", 1)]}, "workloads")
    invalid("finops_carbon_estimate", {"workloads": [cw()], "pue": 1.2, "grid_intensity": [grid("region-a", 1), grid("region-a", 2)]}, "grid_intensity")
    invalid("finops_carbon_estimate", {"workloads": [cw(), cw()], "pue": 1.2, "grid_intensity": [grid("region-a", 1)]}, "workloads")


# --- finops_showback ------------------------------------------------------------------------------------------------


def con(team, direct=0, usage=0):
    return {"team": team, "direct_cost": direct, "usage": usage}


def showback(shared, method, consumers):
    return ok("finops_showback", {"shared_cost": shared, "method": method, "consumers": consumers})


def test_showback_proportional_to_direct_cost():
    result = showback(1000, "proportional", [con("beta", 100), con("alpha", 300)])
    assert result["allocations"] == [
        {"team": "alpha", "direct_cost": 300.0, "shared_allocation": 750.0, "total_cost": 1050.0, "share_pct": 75.0},
        {"team": "beta", "direct_cost": 100.0, "shared_allocation": 250.0, "total_cost": 350.0, "share_pct": 25.0},
    ]
    assert (result["allocated_total"], result["largest_consumer"], result["method"]) == (1000.0, "alpha", "proportional")
    assert [f["id"] for f in result["findings"]] == ["SB-alpha"]


def test_showback_leftover_cents_go_to_the_largest_remainder_then_team_name():
    result = showback(100, "even", [con("c"), con("a"), con("b")])
    assert [(r["team"], r["shared_allocation"]) for r in result["allocations"]] == [("a", 33.34), ("b", 33.33), ("c", 33.33)]
    assert result["allocated_total"] == 100.0
    usage = showback(10, "usage", [con("a", usage=1), con("b", usage=2)])
    assert [(r["team"], r["shared_allocation"]) for r in usage["allocations"]] == [("a", 3.33), ("b", 6.67)]


@pytest.mark.parametrize("shared, count", [(0.01, 3), (1, 7), (100, 3), (12345.67, 11), (0.5, 4)])
def test_showback_allocations_always_sum_to_the_shared_cost_to_the_cent(shared, count):
    result = showback(shared, "even", [con(f"t{i:02d}") for i in range(count)])
    assert sum(round(r["shared_allocation"] * 100) for r in result["allocations"]) == round(shared * 100)
    assert result["allocated_total"] == shared


def test_showback_usage_method_reports_idle_consumers():
    result = showback(100, "usage", [con("a", usage=5), con("idle", usage=0)])
    assert [r["shared_allocation"] for r in result["allocations"]] == [100.0, 0.0]
    assert any(f["id"] == "SB-idle-idle" for f in result["findings"])


def test_showback_dominant_consumer_threshold_is_inclusive_at_half_and_needs_a_peer():
    assert [f["id"] for f in showback(100, "even", [con("a"), con("b")])["findings"]] == ["SB-a", "SB-b"]
    assert showback(100, "even", [con("a"), con("b"), con("c")])["findings"] == []
    assert showback(100, "even", [con("solo")])["findings"] == []
    assert showback(0, "even", [con("a"), con("b")])["findings"] == []


def test_showback_zero_shared_cost_allocates_nothing():
    result = showback(0, "proportional", [con("a", 5), con("b", 5)])
    assert [(r["shared_allocation"], r["share_pct"]) for r in result["allocations"]] == [(0.0, 0.0), (0.0, 0.0)]


@pytest.mark.parametrize("payload, field", [
    ({"shared_cost": 100.005, "method": "even", "consumers": [con("a")]}, "shared_cost"),
    ({"shared_cost": 10, "method": "proportional", "consumers": [con("a", 0), con("b", 0)]}, "consumers"),
    ({"shared_cost": 10, "method": "usage", "consumers": [con("a", 5, 0), con("b", 5, 0)]}, "consumers"),
    ({"shared_cost": 10, "method": "even", "consumers": [con("a"), con("a")]}, "consumers"),
])
def test_showback_rejects_unallocatable_input(payload, field):
    invalid("finops_showback", payload, field)


def test_showback_accepts_two_decimal_shared_cost():
    assert showback(100.01, "even", [con("a"), con("b")])["allocated_total"] == 100.01


# --- review regressions ---------------------------------------------------------------------------------------------


def test_commitment_below_break_even_is_under_utilized_even_when_the_policy_floor_is_lower():
    # discount 5 %: break-even utilization is 95 %, above the default 90 % floor. 90 % utilization loses money.
    low_discount = commit([10] * 9 + [0], 9.5, discount=5)
    assert (low_discount["utilization_pct"], low_discount["breakeven_utilization_pct"], low_discount["net_savings"]) == (90.0, 95.0, -5.0)  # covered 90 - paid 95
    assert low_discount["verdict"] == "under_utilized"
    assert [(f["id"], f["severity"]) for f in low_discount["findings"]] == [("CC-BREAKEVEN", "blocker")]
    # a caller floor of 0 must not turn a losing commitment into within_band either
    worse = commit([10] * 6 + [0] * 4, 7, min_utilization_pct=0)
    assert (worse["verdict"], worse["net_savings"]) == ("under_utilized", -10.0)
    # exactly at the break-even nothing is lost: still within_band
    exact = commit([10] * 7 + [0] * 3, 7, min_utilization_pct=0)
    assert (exact["verdict"], exact["net_savings"], exact["findings"]) == ("within_band", 0.0, [])


@pytest.mark.parametrize("field, value", [("period_start", "0001-01-01T00:00:00+05:00"), ("as_of", "9999-12-31T23:59:59-05:00")])
def test_budget_burn_timestamps_outside_the_calendar_after_conversion_are_invalid_input(field, value):
    payload = {"budget": 100, "spend_to_date": 10, "period_start": "2026-10-01T00:00:00Z", "period_end": "9999-12-31T00:00:00Z", "as_of": "2026-10-02T00:00:00Z", field: value}
    result = call("finops_budget_burn", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] in {"period_start", "as_of", "period_end"}, result


@pytest.mark.parametrize("payload", [
    {"costs": [{"category": "a", "amount": 1e12}], "volume": 1e-300, "unit": "user"},
    {"costs": [{"category": "a", "amount": 1e12}], "volume": 1e-9, "unit": "user", "revenue_per_unit": 1e-300},
])
def test_a_result_beyond_float_range_is_invalid_input_not_a_dependency_failure(payload):
    assert invalid("finops_unit_cost", payload, "payload")["message"].startswith("a result is outside the representable range")


def test_budget_burn_overflowing_run_rate_is_invalid_input():
    payload = {"budget": 1e-300, "spend_to_date": 1e12, "period_start": "2026-10-01T00:00:00Z", "period_end": "2026-10-31T00:00:00Z", "as_of": "2026-10-02T00:00:00Z"}
    invalid("finops_budget_burn", payload, "payload")


def test_spot_on_demand_floor_that_leaves_no_spot_replica_is_marginal_not_suitable():
    result = spot([spot_w(replicas=3, fault_tolerance="stateless")], on_demand_floor_pct=100)
    record = result["workloads"][0]
    assert (record["verdict"], record["reason"], record["spot_replicas"], record["monthly_saving"]) == ("marginal", "on-demand-floor-leaves-no-spot-replicas", 0, 0.0)
    assert (result["verdict"], result["suitable_count"], result["marginal_count"], result["monthly_saving_total"]) == ("none", 0, 1, 0.0)
    two = spot([spot_w(replicas=2, fault_tolerance="stateless")], on_demand_floor_pct=50)["workloads"][0]  # ceil(2 x 0.5) = 1 on demand, 1 on spot
    assert (two["verdict"], two["spot_replicas"]) == ("suitable", 1)


def test_rightsize_a_kept_dimension_reports_its_current_request_so_applying_everything_is_safe():
    # memory must grow (p99 600 > request 500); the cpu downsize is withheld because the p99 (1500 m) exceeds the request
    result = sized([workload(cpu_request_m=1000, cpu_p95_m=100, cpu_p99_m=1500, mem_request_mib=500, mem_p95_mib=450, mem_p99_mib=600)])
    record = result["workloads"][0]
    assert (record["action"], record["cpu_recommended_m"], record["mem_recommended_mib"]) == ("upsize", 1000, 690)
    assert record["cpu_request_m"] == 1000.0 and result["downsize_count"] == 0
    fractional = sized([workload(cpu_request_m=100.5, cpu_p95_m=90, cpu_p99_m=90, mem_request_mib=10.25, mem_p95_mib=9, mem_p99_mib=9)])["workloads"][0]
    assert (fractional["action"], fractional["cpu_recommended_m"], fractional["mem_recommended_mib"]) == ("keep", 101, 11)  # 104 m and 11 MiB sit inside +-10 %


def test_rightsize_majors_survive_the_finding_cap_and_truncation_is_announced():
    # 60 workloads each give a minor cpu downsize, a major memory upsize and a major peak finding: 180 findings
    many = [workload(name=f"w{i:02d}", cpu_request_m=1000, cpu_p95_m=100, cpu_p99_m=200, mem_request_mib=500, mem_p95_mib=450, mem_p99_mib=600) for i in range(60)]
    result = sized(many)
    findings = result["findings"]
    assert len(findings) == 100 and findings[-1]["id"] == "TRUNCATED"
    assert findings[-1]["summary"].startswith("81 further finding(s) omitted")
    assert [f["severity"] for f in findings[:-1]] == ["major"] * 99  # 120 majors exist: no minor displaced one
    assert result["workload_count"] == 60 and len(result["workloads"]) == 60


def test_spot_and_storage_cap_findings_with_an_announcement():
    result = spot([spot_w(name=f"w{i:03d}") for i in range(150)])
    assert len(result["findings"]) == 100 and result["findings"][-1]["id"] == "TRUNCATED" and result["suitable_count"] == 150
    assert result["findings"][-1]["summary"].startswith("51 further finding(s) omitted")


def test_tag_coverage_judges_every_value_of_a_repeated_key():
    result = tagged([{"id": "a", "monthly_cost": 1, "tags": [{"key": "owner", "value": "x"}, {"key": "owner", "value": " "}, {"key": "env", "value": "p"}]}])
    assert result["problem_counts"] == [{"problem": "duplicate_key", "count": 1}, {"problem": "empty_value", "count": 1}]
    assert result["findings"][0]["severity"] == "major"  # a blank duplicate makes the allocation ambiguous
    allowed = tagged([{"id": "a", "monthly_cost": 1, "tags": [{"key": "owner", "value": "ok"}, {"key": "owner", "value": "bad"}, {"key": "env", "value": "p"}]}],
                     allowed_values=[{"key": "owner", "values": ["ok"]}])
    assert [p["problem"] for p in allowed["problem_counts"]] == ["duplicate_key", "invalid_value"]


def test_tag_coverage_case_mismatch_is_case_only_not_unicode_folding():
    # "ß" casefolds to "ss", but Strasse and Straße are different keys, not a capitalisation mistake
    result = tagged([res("a", 1, **{"Straße": "x"})], required=("STRASSE",))
    assert result["problem_counts"] == [{"problem": "missing", "count": 1}]
    assert tagged([res("a", 1, **{"Straße": "x"})], required=("straße",))["problem_counts"] == [{"problem": "case_mismatch", "count": 1}]


def test_tag_coverage_findings_stay_small_with_many_required_keys():
    keys = [f"required-key-{i:02d}-" + "k" * 100 for i in range(20)]
    result = tagged([res(f"r{i:03d}", i) for i in range(300)], required=keys)
    assert len(json.dumps(result)) < 100_000 and result["findings_truncated"] is True
    summary = result["findings"][0]["summary"]
    assert summary.count("missing:") == 5 and summary.endswith("; (+15 more)")
    assert result["problem_counts"] == [{"problem": "missing", "count": 6000}]


def test_every_tool_is_deterministic():
    for tool in lane_tools():
        sample = tool["examples"]["success"]
        assert json.dumps(call(tool["name"], sample), sort_keys=False) == json.dumps(call(tool["name"], copy.deepcopy(sample)), sort_keys=False)


# --- secret safety: no input value that could be a credential is echoed --------------------------------------------------

CANARIES = [
    "AKIAIOSFODNN7EXAMPLE", "ghp_1234567890abcdefghijklmnopqrstuvwxyz", "ab" * 32,
    "abandon ability able about above absent absorb abstract absurd abuse access accident", "https://user:pa55w0rd@host.example/x",
]


@pytest.mark.parametrize("canary", CANARIES)
def test_tag_values_are_never_echoed(canary):
    resources = [res("a", 10, owner=canary, env=canary), res("b", 5, owner=canary), res("c", 1, owner="", env=canary)]
    for allowed in (None, [{"key": "env", "values": ["prod"]}]):
        extra = {"allowed_values": allowed} if allowed else {}
        result = tagged(resources, **extra)
        assert result["findings"] and canary not in json.dumps(result)


@pytest.mark.parametrize("canary", CANARIES)
def test_rejected_timestamps_and_dates_are_not_echoed(canary):
    base = {"budget": 100, "spend_to_date": 10, "period_start": "2026-10-01T00:00:00Z", "period_end": "2026-11-01T00:00:00Z", "as_of": "2026-10-02T00:00:00Z"}
    for field in ("period_start", "period_end", "as_of"):
        result = call("finops_budget_burn", {**base, field: canary})
        assert result["state"] == "INVALID_INPUT" and canary not in json.dumps(result)
    result = call("finops_spend_anomaly", {"daily_spend": [1] * 7, "start_date": canary})
    assert result["state"] == "INVALID_INPUT" and canary not in json.dumps(result)


@pytest.mark.parametrize("canary", CANARIES)
def test_identifiers_that_fail_a_cross_reference_are_not_echoed(canary):
    tier_result = call("finops_storage_tiering", {"tiers": [STANDARD, INFREQUENT], "datasets": [dataset(current_tier=canary[:40])]})
    assert tier_result["state"] == "INVALID_INPUT" and canary[:40] not in json.dumps(tier_result)
    carbon_result = call("finops_carbon_estimate", {"workloads": [cw(region=canary[:40])], "pue": 1.2, "grid_intensity": [grid("region-a", 1)]})
    assert carbon_result["state"] == "INVALID_INPUT" and canary[:40] not in json.dumps(carbon_result)
    duplicate = call("finops_rightsize", {"workloads": [workload(name=canary[:80]), workload(name=canary[:80])], "cpu_basis": "p95", "cpu_price_per_core_hour": 1, "mem_price_per_gib_hour": 1})
    assert duplicate["state"] == "INVALID_INPUT" and canary[:80] not in json.dumps(duplicate)
    keys = call("finops_tag_coverage", {"resources": [res("a", 1)], "required_keys": ["owner"], "allowed_values": [{"key": canary[:128], "values": ["x"]}]})
    assert keys["state"] == "INVALID_INPUT" and canary[:128] not in json.dumps(keys)


# --- independent invariants (recomputed in the test, not read off the handler) ----------------------------------------------


def test_commitment_break_even_sign_agrees_with_net_savings_for_many_series():
    import random

    rng = random.Random(20)
    for _ in range(300):
        usage = [rng.choice([0, 1, 5, 10, 10, 12.5, 20, 33.3, 100]) for _ in range(rng.randint(1, 30))]
        discount, commitment = rng.choice([5, 10, 30, 33.3, 50, 72]), rng.choice([1, 3, 7, 10, 15.5, 50])
        result = commit(usage, commitment, discount=discount, min_utilization_pct=rng.choice([0, 50, 90]))
        flagged = any(f["id"] == "CC-BREAKEVEN" for f in result["findings"])
        # the verdict is exact; net_savings is printed to the cent, so a loss under half a cent shows as -0.0
        assert result["net_savings"] <= 0 if flagged else result["net_savings"] >= 0, (usage, commitment, discount)
        assert result["net_savings"] >= 0 or flagged, (usage, commitment, discount)
        assert not (flagged and result["verdict"] == "within_band"), (usage, commitment, discount)


def test_spend_anomaly_dates_roll_over_a_leap_day():
    result = anomaly([10] * 7 + [100], start_date="2028-02-25")  # a series of 8 days: index 7 is 2028-03-03 (2028 is a leap year)
    assert [(a["index"], a["date"]) for a in result["anomalies"]] == [(7, "2028-03-03")]
    assert result["findings"][0]["id"] == "SA-2028-03-03"


def test_idle_worst_case_output_stays_well_below_the_registry_output_limit():
    resources = [rs(f"{i:04d}" + "i" * 115, [0] * 90, cost=1e9 - i, kind="load_balancer") for i in range(300)]
    result = idle(resources, threshold=100, days=1)
    assert result["idle_count"] == 300 and len(result["idle"]) == 300
    assert len(result["findings"]) == 100 and result["findings"][-1]["id"] == "TRUNCATED"
    assert len(json.dumps(result)) < 160_000  # the limit is 262144 bytes
