"""Behaviour tests for the A16 Cloud-Native Architect tools.

Tools: cloud_twelve_factor_audit, cloud_composite_availability, cloud_dr_feasibility, cloud_data_residency,
cloud_landing_zone_lint, cloud_wellarch_scorecard, cloud_blast_radius, cloud_cell_capacity, cloud_cidr_plan,
cloud_managed_vs_self_hosted, cloud_replica_placement, cloud_latency_budget.

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
AGENT, SLUG, PREFIX = "A16", "a16-cloud-architect", "cloud_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = [
    "cloud_twelve_factor_audit", "cloud_composite_availability", "cloud_dr_feasibility", "cloud_data_residency",
    "cloud_landing_zone_lint", "cloud_wellarch_scorecard", "cloud_blast_radius", "cloud_cell_capacity", "cloud_cidr_plan",
    "cloud_managed_vs_self_hosted", "cloud_replica_placement", "cloud_latency_budget",
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


def ids(result: dict, key: str = "findings") -> list[str]:
    return [item["id"] for item in result[key]]


def severity_of(result: dict, finding_id: str) -> str:
    return next(item["severity"] for item in result["findings"] if item["id"] == finding_id)


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


# --- cloud_twelve_factor_audit ----------------------------------------------------------------------------------

CONFORMANT = {
    "codebase_in_vcs": True, "repos_per_app": 1, "dependencies_declared": True, "dependencies_locked": True, "assumes_system_tools": False,
    "config_from_environment": True, "secrets_in_codebase": False, "config_grouped_by_named_environment": False,
    "backing_services_via_config_urls": True, "hardcoded_backing_service_hosts": 0, "build_release_run_separated": True,
    "immutable_releases": True, "code_changed_at_runtime": False, "stateless_processes": True, "sticky_sessions": False,
    "local_disk_state_across_requests": False, "self_contained_port_binding": True, "port_from_environment": True,
    "scales_out_via_processes": True, "daemonizes_itself": False, "startup_seconds": 12, "handles_sigterm_gracefully": True,
    "jobs_idempotent_or_requeued": True, "deploy_gap_days": 0.5, "dev_prod_backing_service_mismatch": False, "logs_to_stdout": True,
    "app_manages_log_files": False, "admin_tasks_run_as_one_off_processes": True, "admin_code_shipped_with_release": True,
}


def audit(**changes) -> dict:
    return call("cloud_twelve_factor_audit", {"service": {**CONFORMANT, **changes}})


def test_twelve_factor_conformant_service_scores_100_in_every_factor():
    result = ok("cloud_twelve_factor_audit", {"service": CONFORMANT})
    assert result["score_pct"] == 100.0 and result["conformant_factors"] == 12
    assert result["findings"] == [] and result["verdict"] == "pass"
    assert [(f["factor"], f["name"], f["total"]) for f in result["factors"]] == [
        (1, "codebase", 2), (2, "dependencies", 3), (3, "config", 3), (4, "backing_services", 2), (5, "build_release_run", 3),
        (6, "processes", 3), (7, "port_binding", 2), (8, "concurrency", 2), (9, "disposability", 3), (10, "dev_prod_parity", 2),
        (11, "logs", 2), (12, "admin_processes", 2),
    ]
    assert all(f["passed"] == f["total"] and f["score_pct"] == 100.0 for f in result["factors"])


def test_twelve_factor_committed_secret_is_a_blocker_and_lowers_only_its_factor():
    result = audit(secrets_in_codebase=True)
    assert result["state"] == "SUCCESS"
    assert result["factors"][2] == {"factor": 3, "name": "config", "passed": 2, "total": 3, "score_pct": 66.67}
    assert result["score_pct"] == 96.55 and result["conformant_factors"] == 11  # 28 of 29 checks
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("factor-03:secrets_in_codebase", "blocker")]
    assert result["verdict"] == "fail"


def test_twelve_factor_minor_findings_only_warn():
    result = audit(sticky_sessions=True, dependencies_locked=False)
    assert ids(result) == ["factor-02:dependencies_locked", "factor-06:sticky_sessions"]
    assert {f["severity"] for f in result["findings"]} == {"minor"} and result["verdict"] == "warn"


def test_twelve_factor_major_findings_fail_and_sort_after_blockers():
    result = audit(logs_to_stdout=False, codebase_in_vcs=False, hardcoded_backing_service_hosts=3, repos_per_app=2)
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [
        ("factor-01:codebase_in_vcs", "blocker"), ("factor-01:repos_per_app", "major"),
        ("factor-04:hardcoded_backing_service_hosts", "major"), ("factor-11:logs_to_stdout", "major")]
    assert result["factors"][0]["passed"] == 0 and result["verdict"] == "fail"


# every check of the descriptor: (key, failing value, factor, severity)
FACTOR_ROWS = [
    ("codebase_in_vcs", False, 1, "blocker"), ("repos_per_app", 2, 1, "major"),
    ("dependencies_declared", False, 2, "major"), ("dependencies_locked", False, 2, "minor"), ("assumes_system_tools", True, 2, "minor"),
    ("config_from_environment", False, 3, "major"), ("secrets_in_codebase", True, 3, "blocker"),
    ("config_grouped_by_named_environment", True, 3, "minor"),
    ("backing_services_via_config_urls", False, 4, "major"), ("hardcoded_backing_service_hosts", 1, 4, "major"),
    ("build_release_run_separated", False, 5, "major"), ("immutable_releases", False, 5, "major"), ("code_changed_at_runtime", True, 5, "major"),
    ("stateless_processes", False, 6, "major"), ("sticky_sessions", True, 6, "minor"), ("local_disk_state_across_requests", True, 6, "major"),
    ("self_contained_port_binding", False, 7, "major"), ("port_from_environment", False, 7, "minor"),
    ("scales_out_via_processes", False, 8, "major"), ("daemonizes_itself", True, 8, "minor"),
    ("startup_seconds", 31, 9, "minor"), ("handles_sigterm_gracefully", False, 9, "major"), ("jobs_idempotent_or_requeued", False, 9, "minor"),
    ("deploy_gap_days", 2, 10, "minor"), ("dev_prod_backing_service_mismatch", True, 10, "major"),
    ("logs_to_stdout", False, 11, "major"), ("app_manages_log_files", True, 11, "minor"),
    ("admin_tasks_run_as_one_off_processes", False, 12, "minor"), ("admin_code_shipped_with_release", False, 12, "minor"),
]


def test_twelve_factor_table_covers_every_descriptor_field():
    assert sorted(key for key, *_ in FACTOR_ROWS) == sorted(CONFORMANT)


@pytest.mark.parametrize("key,failing,factor,severity", FACTOR_ROWS)
def test_twelve_factor_each_check_fails_alone_with_its_factor_and_severity(key, failing, factor, severity):
    result = audit(**{key: failing})
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [(f"factor-{factor:02d}:{key}", severity)]
    row = result["factors"][factor - 1]
    assert row["passed"] == row["total"] - 1 and result["conformant_factors"] == 11


@pytest.mark.parametrize("seconds,expected", [(30, True), (30.5, False)])
def test_twelve_factor_startup_boundary_is_30_seconds_inclusive(seconds, expected):
    result = audit(startup_seconds=seconds)
    assert ("factor-09:startup_seconds" not in ids(result)) is expected


@pytest.mark.parametrize("days,expected", [(1, True), (1.01, False)])
def test_twelve_factor_deploy_gap_boundary_is_one_day_inclusive(days, expected):
    assert ("factor-10:deploy_gap_days" not in ids(audit(deploy_gap_days=days))) is expected


def test_twelve_factor_undeclared_major_class_check_counts_as_not_passed_and_fails_closed():
    descriptor = dict(CONFORMANT)
    del descriptor["logs_to_stdout"]
    result = ok("cloud_twelve_factor_audit", {"service": descriptor})
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("factor-11:logs_to_stdout", "major")]
    assert result["findings"][0]["summary"] == "factor 11 logs: logs_to_stdout is not declared"
    assert result["factors"][10] == {"factor": 11, "name": "logs", "passed": 1, "total": 2, "score_pct": 50.0}
    assert result["score_pct"] == 96.55 and result["verdict"] == "fail"


def test_twelve_factor_undeclared_minor_class_check_only_warns():
    descriptor = dict(CONFORMANT)
    del descriptor["app_manages_log_files"]
    result = ok("cloud_twelve_factor_audit", {"service": descriptor})
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("factor-11:app_manages_log_files", "minor")]
    assert result["verdict"] == "warn"


def test_twelve_factor_empty_descriptor_demonstrates_nothing_and_fails_without_a_blocker():
    result = ok("cloud_twelve_factor_audit", {"service": {}})
    assert result["score_pct"] == 0.0 and result["conformant_factors"] == 0 and len(result["findings"]) == 29
    assert {f["severity"] for f in result["findings"]} == {"major", "minor"}  # unknown is never a blocker
    assert sum(f["severity"] == "major" for f in result["findings"]) == 17 and result["verdict"] == "fail"


def test_twelve_factor_a_declared_violation_keeps_its_own_severity_next_to_undeclared_checks():
    result = ok("cloud_twelve_factor_audit", {"service": {"secrets_in_codebase": True}})
    assert severity_of(result, "factor-03:secrets_in_codebase") == "blocker" and result["verdict"] == "fail"
    assert severity_of(ok("cloud_twelve_factor_audit", {"service": {"codebase_in_vcs": True}}), "factor-03:secrets_in_codebase") == "major"


def test_twelve_factor_rejects_wrongly_typed_or_out_of_range_answers():
    invalid("cloud_twelve_factor_audit", {"service": {"repos_per_app": 0}}, "service")
    invalid("cloud_twelve_factor_audit", {"service": {"logs_to_stdout": "yes"}}, "service")
    invalid("cloud_twelve_factor_audit", {"service": {"unknown_check": True}}, "service")


# --- cloud_composite_availability -------------------------------------------------------------------------------


def tier(tier_id, pct, replicas=1, needed=1, shared=False):
    return {"id": tier_id, "availability_pct": pct, "replicas": replicas, "min_required": needed, "shared_failure_domain": shared}


def test_composite_availability_two_of_three_tier_in_series_with_a_single_database():
    result = ok("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("edge", 99.5, 3, 2), tier("db", 99.95)]})
    # edge = 3a^2(1-a)+a^3 = 0.99992525 for a=0.995; series product with 0.9995
    assert result["tiers"] == [{"id": "db", "effective_pct": 99.95}, {"id": "edge", "effective_pct": 99.992525}]
    assert result["composite_pct"] == 99.942529 and result["weakest_tier"] == "db"
    assert result["downtime_min_per_year"] == 302.07 and result["target_downtime_min_per_year"] == 525.6
    assert result["nines"] == 3.24 and result["meets_target"] is True and result["verdict"] == "pass" and result["findings"] == []


def test_composite_availability_plain_series_of_two_three_nines_tiers_misses_the_target():
    result = ok("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("a", 99.9), tier("b", 99.9)]})
    assert result["composite_pct"] == 99.8001 and result["downtime_min_per_year"] == 1050.67
    assert result["meets_target"] is False and result["verdict"] == "fail"
    assert result["weakest_tier"] == "a"  # ties resolve by id
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("chain-below-target", "major")]  # no tier is below 99.9 alone


def test_composite_availability_tier_below_target_is_named():
    result = ok("cloud_composite_availability", {"target_pct": 99.95, "tiers": [tier("db", 99.9), tier("app", 99.99999)]})
    assert ids(result) == ["tier-below-target:db"] and severity_of(result, "tier-below-target:db") == "major"


def test_composite_availability_target_met_exactly_counts_as_met():
    # one tier at 99.9 and target 99.9: composite == target, ">=" holds
    result = ok("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("only", 99.9)]})
    assert result["meets_target"] is True and result["composite_pct"] == 99.9 and result["verdict"] == "pass"
    below = ok("cloud_composite_availability", {"target_pct": 99.9001, "tiers": [tier("only", 99.9)]})
    assert below["meets_target"] is False


def test_composite_availability_one_of_two_parallel_pair():
    result = ok("cloud_composite_availability", {"target_pct": 99.0, "tiers": [tier("pair", 99, 2, 1)]})
    assert result["composite_pct"] == 99.99 and result["nines"] == 4.0
    assert result["downtime_min_per_year"] == 52.56


def test_composite_availability_shared_failure_domain_gets_no_redundancy_credit():
    result = ok("cloud_composite_availability", {"target_pct": 99.0, "tiers": [tier("pair", 99, 3, 1, shared=True)]})
    assert result["composite_pct"] == 99.0
    assert ids(result) == ["redundancy-not-credited:pair"] and severity_of(result, "redundancy-not-credited:pair") == "info"
    assert result["verdict"] == "pass"


def test_composite_availability_two_of_three_at_99_percent():
    result = ok("cloud_composite_availability", {"target_pct": 99.0, "tiers": [tier("quorum", 99, 3, 2)]})
    assert result["composite_pct"] == 99.9702


def test_composite_availability_zero_availability_tier_gives_zero_nines():
    result = ok("cloud_composite_availability", {"target_pct": 99.0, "tiers": [tier("dead", 0)]})
    assert result["composite_pct"] == 0.0 and result["nines"] == 0.0 and result["downtime_min_per_year"] == 525600.0


def test_composite_availability_rejects_semantic_problems_naming_the_field():
    invalid("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("a", 99, 2, 3)]}, "tiers")
    invalid("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("a", 99), tier("a", 99)]}, "tiers")
    invalid("cloud_composite_availability", {"target_pct": 100, "tiers": [tier("a", 99)]}, "target_pct")
    invalid("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("a", 100)]}, "tiers")


# --- cloud_dr_feasibility ---------------------------------------------------------------------------------------

DR_BASE = {"rto_target_min": 60, "rpo_target_min": 15, "detect_decide_min": 10, "traffic_cutover_min": 5, "validation_min": 10}
DR_ALL = {**DR_BASE, "backup_interval_min": 240, "infra_provision_min": 60, "data_restore_min": 120, "compute_start_min": 30,
          "compute_scale_min": 15, "replication_lag_s": 30}


def dr(strategy, **changes):
    return call("cloud_dr_feasibility", {**DR_ALL, "strategy": strategy, **changes})


def test_dr_warm_standby_meets_targets_and_evaluates_only_strategies_with_enough_inputs():
    payload = {**DR_BASE, "strategy": "warm_standby", "compute_scale_min": 15, "replication_lag_s": 30}
    result = ok("cloud_dr_feasibility", payload)
    assert (result["achievable_rto_min"], result["achievable_rpo_min"]) == (40.0, 0.5)  # 10+5+10+15 and 30 s
    assert (result["rto_margin_min"], result["rpo_margin_min"]) == (20.0, 14.5)
    assert result["meets_rto"] and result["meets_rpo"] and result["verdict"] == "pass"
    assert result["evaluated_strategies"] == ["warm_standby", "active_active"]
    assert result["cheapest_sufficient_strategy"] == "warm_standby" and result["findings"] == []


def test_dr_backup_restore_misses_both_targets_and_names_the_cheapest_sufficient_strategy():
    result = ok("cloud_dr_feasibility", {**DR_ALL, "strategy": "backup_restore"})
    assert (result["achievable_rto_min"], result["achievable_rpo_min"]) == (205.0, 240.0)  # 10+5+10+60+120, interval
    assert (result["rto_margin_min"], result["rpo_margin_min"]) == (-145.0, -225.0)
    assert not result["meets_rto"] and not result["meets_rpo"] and result["verdict"] == "fail"
    assert result["evaluated_strategies"] == ["backup_restore", "pilot_light", "warm_standby", "active_active"]
    assert result["cheapest_sufficient_strategy"] == "pilot_light"
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [
        ("rpo-missed", "blocker"), ("rto-missed", "blocker"), ("stronger-strategy-sufficient", "info")]


def test_dr_pilot_light_and_active_active_models():
    pilot = ok("cloud_dr_feasibility", {**DR_ALL, "strategy": "pilot_light"})
    assert pilot["achievable_rto_min"] == 55.0 and pilot["findings"] == []  # 10+5+10+30
    active = ok("cloud_dr_feasibility", {**DR_ALL, "strategy": "active_active"})
    assert active["achievable_rto_min"] == 15.0  # detect + cutover only
    assert ids(active) == ["cheaper-strategy-sufficient"]  # pilot light would be enough


def test_dr_rto_boundary_is_inclusive():
    assert dr("pilot_light", rto_target_min=55)["meets_rto"] is True
    result = dr("pilot_light", rto_target_min=54.99)
    assert result["meets_rto"] is False and ids(result)[0] == "rto-missed"


def test_dr_rpo_boundary_is_inclusive_and_lag_is_converted_from_seconds():
    assert dr("warm_standby", replication_lag_s=900)["meets_rpo"] is True  # 15 min == target
    assert dr("warm_standby", replication_lag_s=901)["meets_rpo"] is False


def test_dr_synchronous_replication_gives_zero_rpo_even_with_a_lag_figure():
    result = dr("active_active", rpo_target_min=0, synchronous_replication=True)
    assert result["achievable_rpo_min"] == 0.0 and result["meets_rpo"] is True
    async_result = dr("active_active", rpo_target_min=0)
    assert not async_result["meets_rpo"] and "zero-rpo-needs-synchronous-replication" in ids(async_result)


def test_dr_missing_strategy_specific_inputs_name_the_field():
    base = {**DR_BASE, "replication_lag_s": 30}
    invalid("cloud_dr_feasibility", {**base, "strategy": "backup_restore"}, "backup_interval_min")
    invalid("cloud_dr_feasibility", {**base, "strategy": "backup_restore", "backup_interval_min": 60}, "infra_provision_min")
    invalid("cloud_dr_feasibility", {**base, "strategy": "pilot_light"}, "compute_start_min")
    invalid("cloud_dr_feasibility", {**base, "strategy": "warm_standby"}, "compute_scale_min")
    invalid("cloud_dr_feasibility", {**DR_BASE, "strategy": "pilot_light", "compute_start_min": 5}, "replication_lag_s")
    invalid("cloud_dr_feasibility", {**DR_BASE, "strategy": "active_active"}, "replication_lag_s")


# --- cloud_data_residency ---------------------------------------------------------------------------------------

REGIONS = [{"region": "eu-west-1", "jurisdiction": "EU"}, {"region": "us-east-1", "jurisdiction": "US"}]
RULES = [{"data_class": "pii", "allowed_jurisdictions": ["EU"]}, {"data_class": "telemetry", "allowed_jurisdictions": ["*"]}]


def place(resource, data_class, region, kind="primary"):
    return {"resource": resource, "data_class": data_class, "region": region, "kind": kind}


def residency(placements, regions=REGIONS, rules=RULES):
    return call("cloud_data_residency", {"regions": regions, "rules": rules, "placements": placements})


def test_residency_compliant_placements_pass_and_wildcard_allows_any_region():
    result = residency([place("db", "pii", "eu-west-1"), place("metrics", "telemetry", "us-east-1")])
    assert result["state"] == "SUCCESS" and result["placements_checked"] == 2
    assert (result["violation_count"], result["unruled_count"], result["findings"], result["verdict"]) == (0, 0, [], "pass")
    assert result["by_data_class"] == [{"data_class": "pii", "placements": 1, "violations": 0},
                                       {"data_class": "telemetry", "placements": 1, "violations": 0}]


def test_residency_violations_are_blockers_for_persistent_copies_and_major_for_transient_ones():
    result = residency([place("db", "pii", "eu-west-1"), place("db-replica", "pii", "us-east-1", "replica"),
                        place("cache", "pii", "us-east-1", "cache"), place("dw", "pii", "us-east-1", "analytics"),
                        place("bak", "pii", "us-east-1", "backup"), place("logs", "pii", "us-east-1", "log")])
    assert result["violation_count"] == 5 and result["verdict"] == "fail"
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [
        ("residency-violation:bak:us-east-1", "blocker"), ("residency-violation:db-replica:us-east-1", "blocker"),
        ("residency-violation:dw:us-east-1", "blocker"), ("residency-violation:cache:us-east-1", "major"),
        ("residency-violation:logs:us-east-1", "major")]
    assert result["by_data_class"] == [{"data_class": "pii", "placements": 6, "violations": 5}]
    assert result["findings"][0]["summary"] == "backup of pii in us-east-1 (us) is outside eu"


def test_residency_class_without_a_rule_fails_closed_and_is_counted_separately():
    result = residency([place("ledger", "payments", "eu-west-1")])
    assert (result["violation_count"], result["unruled_count"]) == (0, 1)
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("no-rule:payments:ledger", "major")]
    assert result["verdict"] == "fail"


def test_residency_jurisdictions_compare_case_insensitively_and_ignore_blanks_around():
    result = residency([place("db", "pii", "eu-west-1")], rules=[{"data_class": "pii", "allowed_jurisdictions": [" eu "]}])
    assert result["violation_count"] == 0


def test_residency_rejects_semantic_problems_naming_the_field():
    invalid("cloud_data_residency", {"regions": REGIONS, "rules": RULES, "placements": [place("db", "pii", "ap-south-1")]}, "placements")
    invalid("cloud_data_residency", {"regions": REGIONS + [REGIONS[0]], "rules": RULES, "placements": [place("db", "pii", "eu-west-1")]}, "regions")
    invalid("cloud_data_residency", {"regions": [{"region": "r", "jurisdiction": "  "}], "rules": RULES, "placements": [place("d", "pii", "r")]}, "regions")
    invalid("cloud_data_residency", {"regions": REGIONS, "rules": RULES + [RULES[0]], "placements": [place("db", "pii", "eu-west-1")]}, "rules")
    invalid("cloud_data_residency", {"regions": REGIONS, "rules": [{"data_class": "pii", "allowed_jurisdictions": [" "]}],
                                     "placements": [place("db", "pii", "eu-west-1")]}, "rules")


# --- cloud_landing_zone_lint ------------------------------------------------------------------------------------

BASE_GR = ["deny-leave-organization", "deny-root-user-actions", "protect-audit-logging", "restrict-regions"]
GOOD_GLASS = {"configured": True, "hardware_mfa": True, "alert_on_use": True, "last_tested_days_ago": 30}


def account(acct_id, role, ou, guardrails=None, mfa=True, iam=0):
    return {"id": acct_id, "role": role, "ou": ou, "root_mfa": mfa, "iam_users": iam, "guardrails": guardrails if guardrails is not None else []}


def good_zone():
    return [
        account("mgmt", "management", ""),
        account("sec", "security_audit", "Security", BASE_GR),
        account("logs", "log_archive", "Security", BASE_GR + ["deny-log-deletion"]),
        account("prod", "workload_prod", "Workloads/Prod", BASE_GR + ["deny-public-storage", "require-encryption-at-rest"]),
        account("dev", "workload_nonprod", "Workloads/NonProd", BASE_GR),
    ]


def lz(accounts, glass=GOOD_GLASS):
    return call("cloud_landing_zone_lint", {"accounts": accounts, "break_glass": glass})


def test_landing_zone_clean_organization_passes_with_full_coverage():
    result = lz(good_zone())
    assert result["state"] == "SUCCESS" and result["findings"] == [] and result["verdict"] == "pass"
    assert (result["accounts_checked"], result["ou_count"], result["guardrail_coverage_pct"]) == (5, 4, 100.0)  # Security, Workloads, +Prod, +NonProd


def test_landing_zone_missing_guardrails_lower_coverage_and_fail_the_account():
    zone = good_zone()
    zone[3]["guardrails"] = list(BASE_GR)  # prod misses its two extra guardrails: 17 of 19 required present
    result = lz(zone)
    assert result["guardrail_coverage_pct"] == 89.47
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("guardrail-missing:prod", "major")]
    assert "deny-public-storage, require-encryption-at-rest" in result["findings"][0]["summary"] and result["verdict"] == "fail"


def test_landing_zone_management_account_is_exempt_from_guardrails_and_sandbox_needs_a_budget_alarm():
    zone = good_zone() + [account("sbx", "sandbox", "Sandbox", BASE_GR)]
    result = lz(zone)
    assert ids(result) == ["guardrail-missing:sbx"] and "budget-alarm" in result["findings"][0]["summary"]
    assert result["guardrail_coverage_pct"] == 95.83  # 23 of 24 required: management is not counted


@pytest.mark.parametrize("index,missing", [
    (1, "deny-leave-organization"), (1, "deny-root-user-actions"), (1, "protect-audit-logging"), (1, "restrict-regions"),  # baseline, security_audit
    (2, "deny-log-deletion"),  # log_archive
    (3, "deny-public-storage"), (3, "require-encryption-at-rest"),  # workload_prod
])
def test_landing_zone_each_required_guardrail_is_enforced_for_its_role(index, missing):
    zone = good_zone()
    zone[index]["guardrails"] = [g for g in zone[index]["guardrails"] if g != missing]
    result = lz(zone)
    assert ids(result) == [f"guardrail-missing:{zone[index]['id']}"] and missing in result["findings"][0]["summary"]


def test_landing_zone_nonprod_needs_only_the_baseline_guardrails():
    zone = good_zone()
    assert zone[4]["guardrails"] == BASE_GR and lz(zone)["findings"] == []


def test_landing_zone_management_account_cardinality():
    no_mgmt = lz(good_zone()[1:])
    assert severity_of(no_mgmt, "no-management-account") == "blocker"
    two = lz(good_zone() + [account("mgmt2", "management", "")])
    assert severity_of(two, "multiple-management-accounts") == "blocker"


def test_landing_zone_requires_log_archive_and_security_accounts():
    zone = [a for a in good_zone() if a["role"] not in ("log_archive", "security_audit")]
    result = lz(zone)
    assert severity_of(result, "no-log-archive-account") == "blocker" and severity_of(result, "no-security-audit-account") == "major"


def test_landing_zone_prod_and_nonprod_sharing_an_ou_is_flagged_and_root_placement_is_minor():
    zone = good_zone()
    zone[4]["ou"] = "Workloads/Prod"
    assert severity_of(lz(zone), "prod-nonprod-shared-ou:Workloads/Prod") == "major"
    zone = good_zone()
    zone[4]["ou"] = ""
    zone.append(account("prod2", "workload_prod", "", BASE_GR + ["deny-public-storage", "require-encryption-at-rest"]))
    result = lz(zone)
    assert severity_of(result, "prod-nonprod-shared-ou:(root)") == "major"
    assert severity_of(result, "account-at-root:dev") == "minor" and severity_of(result, "account-at-root:prod2") == "minor"
    assert "account-at-root:mgmt" not in ids(result)


def test_landing_zone_root_mfa_and_iam_users():
    zone = good_zone()
    zone[0]["root_mfa"] = False
    zone[4]["iam_users"] = 2
    result = lz(zone)
    assert severity_of(result, "root-mfa-missing:mgmt") == "blocker" and severity_of(result, "iam-users-present:dev") == "minor"


def test_landing_zone_break_glass_checks_and_the_180_day_boundary():
    assert lz(good_zone(), {**GOOD_GLASS, "last_tested_days_ago": 180})["findings"] == []
    stale = lz(good_zone(), {**GOOD_GLASS, "last_tested_days_ago": 181})
    assert ids(stale) == ["break-glass-untested"] and stale["verdict"] == "warn"
    weak = lz(good_zone(), {**GOOD_GLASS, "hardware_mfa": False, "alert_on_use": False})
    assert ids(weak) == ["break-glass-no-alert", "break-glass-no-hardware-mfa"] and weak["verdict"] == "fail"
    missing = lz(good_zone(), {"configured": False, "hardware_mfa": True, "alert_on_use": True, "last_tested_days_ago": 3650})
    assert ids(missing) == ["break-glass-missing"] and severity_of(missing, "break-glass-missing") == "blocker"


def test_landing_zone_with_only_a_management_account_evaluates_nothing_and_scores_zero():
    result = lz([account("mgmt", "management", "")])
    assert result["guardrail_coverage_pct"] == 0.0 and "guardrails-not-evaluated" in ids(result) and "no-log-archive-account" in ids(result)
    assert severity_of(result, "guardrails-not-evaluated") == "major" and result["verdict"] == "fail"


def test_landing_zone_rejects_semantic_problems_naming_the_field():
    invalid("cloud_landing_zone_lint", {"accounts": [account("a", "sandbox", "A//B")], "break_glass": GOOD_GLASS}, "accounts")
    invalid("cloud_landing_zone_lint", {"accounts": [account("a", "sandbox", "/A")], "break_glass": GOOD_GLASS}, "accounts")
    invalid("cloud_landing_zone_lint", {"accounts": [account("a", "sandbox", "A"), account("a", "network", "B")], "break_glass": GOOD_GLASS}, "accounts")


# --- cloud_wellarch_scorecard -----------------------------------------------------------------------------------


def answer(pillar, question, weight=3, risk="none"):
    return {"pillar": pillar, "question": question, "weight": weight, "risk": risk}


def full_answers(**overrides):
    rows = {p: answer(p, "q", 3, "none") for p in
            ("operational_excellence", "security", "reliability", "performance_efficiency", "cost_optimization", "sustainability")}
    rows.update(overrides)
    return list(rows.values())


def test_wellarch_all_none_scores_100_and_passes():
    result = ok("cloud_wellarch_scorecard", {"answers": full_answers()})
    assert result["overall_score_pct"] == 100.0 and result["remediation"] == [] and result["high_risk_count"] == 0
    assert result["verdict"] == "pass" and result["weakest_pillar"] == "operational_excellence"  # ties resolve in pillar order
    assert [p["pillar"] for p in result["pillars"]] == ["operational_excellence", "security", "reliability", "performance_efficiency",
                                                       "cost_optimization", "sustainability"]


def test_wellarch_medium_risk_halves_the_weight_and_high_risk_loses_all_of_it():
    answers = full_answers(security=answer("security", "iam", 5, "medium")) + [answer("security", "mfa", 5, "none")]
    result = ok("cloud_wellarch_scorecard", {"answers": answers})
    security = result["pillars"][1]
    assert security == {"pillar": "security", "score_pct": 75.0, "answered": 2, "high_risk": 0, "medium_risk": 1}
    assert result["overall_score_pct"] == 95.83 and result["weakest_pillar"] == "security" and result["verdict"] == "warn"
    high = ok("cloud_wellarch_scorecard", {"answers": full_answers(reliability=answer("reliability", "dr", 2, "high"))})
    assert high["pillars"][2]["score_pct"] == 0.0 and high["high_risk_count"] == 1


def test_wellarch_pillar_weights_shift_the_overall_score():
    answers = full_answers(security=answer("security", "iam", 5, "medium"))  # security at 50
    weights = {"operational_excellence": 1, "security": 5, "reliability": 1, "performance_efficiency": 1, "cost_optimization": 1, "sustainability": 1}
    result = ok("cloud_wellarch_scorecard", {"answers": answers, "pillar_weights": weights})
    assert result["overall_score_pct"] == 75.0  # (500 + 5*50) / 10


def test_wellarch_remediation_is_ranked_by_impact_with_severity_by_risk_and_weight():
    answers = full_answers() + [
        answer("security", "mfa", 5, "high"),         # impact 5, blocker
        answer("reliability", "dr", 4, "high"),       # impact 4, blocker (weight >= 4)
        answer("reliability", "backups", 3, "high"),  # impact 3, major
        answer("cost_optimization", "tags", 5, "medium"),  # impact 2.5, minor
        answer("sustainability", "region", 5, "medium"),   # impact 2.5, minor, later pillar
    ]
    result = ok("cloud_wellarch_scorecard", {"answers": answers})
    assert [(r["rank"], r["id"], r["severity"], r["impact"]) for r in result["remediation"]] == [
        (1, "security:mfa", "blocker", 5.0), (2, "reliability:dr", "blocker", 4.0), (3, "reliability:backups", "major", 3.0),
        (4, "cost_optimization:tags", "minor", 2.5), (5, "sustainability:region", "minor", 2.5)]
    assert result["high_risk_count"] == 3 and result["verdict"] == "fail"
    assert result["remediation"][0]["summary"] == "high risk on 'mfa' (weight 5)"


def test_wellarch_rejects_semantic_problems_naming_the_field():
    invalid("cloud_wellarch_scorecard", {"answers": full_answers()[:5] + [answer("security", "x")]}, "answers")  # sustainability missing
    invalid("cloud_wellarch_scorecard", {"answers": full_answers() + [answer("security", "q")]}, "answers")  # duplicate (pillar, question)


# --- cloud_blast_radius -----------------------------------------------------------------------------------------


def svc(sid, crit, instances, deps=()):
    return {"id": sid, "criticality": crit, "instances": instances, "depends_on": list(deps)}


def inst(az, region="r1", cell=None):
    return {"region": region, "az": az, **({"cell": cell} if cell else {})}


def blast(services, limit=50):
    return call("cloud_blast_radius", {"services": services, "max_az_blast_pct": limit})


def test_blast_radius_hard_dependency_propagates_and_single_az_database_is_a_spof():
    result = blast([svc("api", 8, [inst("a"), inst("b")], ["db"]), svc("db", 9, [inst("a")])])
    assert result["domains_evaluated"] == 3 and result["worst_domain"] == "az:r1/a" and result["worst_blast_pct"] == 100.0
    assert result["ranking"] == [
        {"domain": "az:r1/a", "scope": "az", "blast_pct": 100.0, "down_services": ["api", "db"], "degraded_count": 0},
        {"domain": "region:r1", "scope": "region", "blast_pct": 100.0, "down_services": ["api", "db"], "degraded_count": 0},
        {"domain": "az:r1/b", "scope": "az", "blast_pct": 0.0, "down_services": [], "degraded_count": 1}]
    assert result["spof_services"] == ["db"]
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("blast-exceeds-limit:az:r1/a", "major"), ("spof:db", "major")]
    assert result["verdict"] == "fail"


def test_blast_radius_multi_az_services_only_degrade_when_one_az_fails():
    result = blast([svc("api", 5, [inst("a"), inst("b")]), svc("db", 5, [inst("a"), inst("b")], [])])
    assert result["spof_services"] == [] and result["findings"] == [] and result["verdict"] == "pass"
    az = next(r for r in result["ranking"] if r["domain"] == "az:r1/a")
    assert az["blast_pct"] == 0.0 and az["degraded_count"] == 2
    assert result["worst_domain"] == "region:r1" and result["worst_blast_pct"] == 100.0  # region loss is ranked but not gated


def test_blast_radius_is_weighted_by_criticality_and_the_limit_is_exclusive():
    services = [svc("x", 1, [inst("a")]), svc("y", 3, [inst("b")])]
    at_limit = blast(services, 75)  # az b takes down 3 of 4 = 75.0%, az a 1 of 4 = 25.0%
    assert [(r["domain"], r["blast_pct"], r["down_services"]) for r in at_limit["ranking"]] == [
        ("region:r1", 100.0, ["x", "y"]), ("az:r1/b", 75.0, ["y"]), ("az:r1/a", 25.0, ["x"])]
    assert ids(at_limit) == ["spof:x", "spof:y"] and at_limit["verdict"] == "warn"  # not above the limit; both SPOFs are minor
    over = blast(services, 74.99)
    assert ids(over) == ["blast-exceeds-limit:az:r1/b", "spof:x", "spof:y"] and over["verdict"] == "fail"


def test_blast_radius_spof_severity_follows_criticality():
    result = blast([svc("low", 7, [inst("a")]), svc("high", 8, [inst("b")])], 100)
    assert severity_of(result, "spof:low") == "minor" and severity_of(result, "spof:high") == "major"


def test_blast_radius_cells_degrade_services_present_in_several_cells():
    services = [svc("s1", 5, [inst("a", cell="c1"), inst("b", cell="c2")]), svc("s2", 5, [inst("a", cell="c1")])]
    result = blast(services, 100)
    cell = next(r for r in result["ranking"] if r["domain"] == "cell:c1")
    assert cell == {"domain": "cell:c1", "scope": "cell", "blast_pct": 50.0, "down_services": ["s2"], "degraded_count": 1}
    assert result["spof_services"] == ["s2"]


def test_blast_radius_dependency_cycles_terminate():
    services = [svc("a", 1, [inst("z1")], ["b"]), svc("b", 1, [inst("z2")], ["a"])]
    result = blast(services, 100)
    assert next(r for r in result["ranking"] if r["domain"] == "az:r1/z1")["down_services"] == ["a", "b"]


def test_blast_radius_ranking_is_limited_to_ten_and_ties_sort_by_domain():
    services = [svc(f"s{i}", 1, [inst(f"z{i:02d}", region=f"r{i}")]) for i in range(8)]
    result = blast(services, 100)
    assert result["domains_evaluated"] == 16 and len(result["ranking"]) == 10
    assert [r["domain"] for r in result["ranking"][:4]] == ["az:r0/z00", "az:r1/z01", "az:r2/z02", "az:r3/z03"]
    assert result["worst_blast_pct"] == 12.5


def test_blast_radius_rejects_semantic_problems_naming_the_field():
    invalid("cloud_blast_radius", {"services": [svc("a", 1, [inst("a")], ["ghost"])], "max_az_blast_pct": 10}, "services")
    invalid("cloud_blast_radius", {"services": [svc("a", 1, [inst("a")]), svc("a", 1, [inst("b")])], "max_az_blast_pct": 10}, "services")


# --- cloud_cell_capacity ----------------------------------------------------------------------------------------

CELL = {"load_rps": 900, "cell_max_rps": 300, "target_utilisation_pct": 70, "growth_pct": 0, "spare_cells": 1, "current_cells": 6}


def cells(**changes):
    return call("cloud_cell_capacity", {**CELL, **changes})


def test_cell_capacity_values_for_the_reference_load():
    result = ok("cloud_cell_capacity", CELL)
    assert result["usable_rps_per_cell"] == 210.0 and result["projected_load_rps"] == 900.0
    assert (result["cells_for_load"], result["cells_required"]) == (5, 6)  # ceil(900/210) = 5, plus one spare
    assert result["headroom_pct"] == 40.0 and result["blast_radius_pct"] == 16.6667
    assert (result["shortfall_cells"], result["current_cells_sufficient"], result["findings"], result["verdict"]) == (0, True, [], "pass")


def test_cell_capacity_load_that_exactly_fills_cells_does_not_round_up():
    assert cells(load_rps=840)["cells_for_load"] == 4  # 4 * 210
    assert cells(load_rps=841)["cells_for_load"] == 5
    assert cells(load_rps=700, growth_pct=20)["cells_for_load"] == 4  # 700 * 1.2 = 840
    assert cells(load_rps=700, growth_pct=20)["projected_load_rps"] == 840.0


def test_cell_capacity_shortfall_against_deployed_cells_fails():
    result = cells(current_cells=4)
    assert (result["shortfall_cells"], result["current_cells_sufficient"]) == (2, False)
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("cells-short", "major")] and result["verdict"] == "fail"
    assert cells(current_cells=5)["shortfall_cells"] == 1 and cells(current_cells=6)["shortfall_cells"] == 0


def test_cell_capacity_flags_single_cell_and_missing_spare_as_minor():
    result = cells(load_rps=100, spare_cells=0, current_cells=1)
    assert result["cells_required"] == 1 and result["blast_radius_pct"] == 100.0
    assert ids(result) == ["no-spare-cell", "single-cell"] and result["verdict"] == "warn"
    assert ids(cells(load_rps=100, spare_cells=1, current_cells=2)) == []


def test_cell_capacity_rejects_non_positive_load_through_the_schema():
    invalid("cloud_cell_capacity", {**CELL, "load_rps": 0}, "load_rps")
    invalid("cloud_cell_capacity", {**CELL, "target_utilisation_pct": 101}, "target_utilisation_pct")


# --- cloud_cidr_plan --------------------------------------------------------------------------------------------


def net(name, cidr, routable=True, group=None, required=None):
    item = {"name": name, "cidr": cidr, "routable": routable}
    if group is not None:
        item["group"] = group
    if required is not None:
        item["required_addresses"] = required
    return item


def plan(networks, **extra):
    return call("cloud_cidr_plan", {"networks": networks, **extra})


def test_cidr_disjoint_private_networks_pass():
    result = plan([net("a", "10.0.0.0/16"), net("b", "10.1.0.0/16"), net("c", "100.64.0.0/10"), net("d", "fd00::/48"), net("e", "172.16.0.0/12")])
    assert result["state"] == "SUCCESS" and result["networks_checked"] == 5
    assert (result["overlap_count"], result["findings"], result["findings_truncated"], result["verdict"]) == (0, [], False, "pass")


@pytest.mark.parametrize("a,b,relation", [
    ("10.0.0.0/16", "10.0.128.0/17", "vpc-a (10.0.0.0/16) contains vpc-b (10.0.128.0/17)"),
    ("10.0.128.0/17", "10.0.0.0/16", "vpc-a (10.0.128.0/17) is inside vpc-b (10.0.0.0/16)"),
    ("10.0.0.0/16", "10.0.0.0/16", "vpc-a (10.0.0.0/16) equals vpc-b (10.0.0.0/16)"),
])
def test_cidr_overlap_between_routable_networks_is_a_blocker(a, b, relation):
    result = plan([net("vpc-a", a), net("vpc-b", b)])
    assert result["overlap_count"] == 1 and result["verdict"] == "fail"
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("overlap:vpc-a:vpc-b", "blocker")]
    assert result["findings"][0]["summary"] == relation


def test_cidr_overlap_matters_only_for_networks_that_coexist():
    unrouted_other_groups = plan([net("pods-a", "10.244.0.0/16", False, "cluster-a"), net("pods-b", "10.244.0.0/16", False, "cluster-b")])
    assert unrouted_other_groups["overlap_count"] == 0
    routed_vs_unrouted = plan([net("vpc", "10.244.0.0/16"), net("pods", "10.244.0.0/17", False, "cluster-b")])
    assert routed_vs_unrouted["overlap_count"] == 0
    same_group = plan([net("pods", "10.244.0.0/16", False, "cluster-a"), net("svc", "10.244.8.0/24", False, "cluster-a")])
    assert [(f["id"], f["severity"]) for f in same_group["findings"]] == [("overlap:pods:svc", "major")]
    assert same_group["overlap_count"] == 1 and same_group["verdict"] == "fail"


def test_cidr_ipv4_and_ipv6_are_never_compared_with_each_other():
    assert plan([net("v4", "10.0.0.0/8"), net("v6", "fd00::/8")])["overlap_count"] == 0


def test_cidr_public_ranges_are_minor_and_rfc_private_ranges_are_not():
    result = plan([net("pub", "8.8.8.0/24"), net("ok", "192.168.0.0/16"), net("straddle", "172.0.0.0/8")])
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("public-range:pub", "minor"), ("public-range:straddle", "minor")]
    assert result["verdict"] == "warn"


@pytest.mark.parametrize("cidr,private", [
    ("10.255.255.0/24", True), ("11.0.0.0/24", False), ("172.16.0.0/16", True), ("172.31.255.0/24", True), ("172.32.0.0/16", False),
    ("192.168.255.0/24", True), ("192.169.0.0/24", False), ("100.64.0.0/16", True), ("100.128.0.0/16", False),
    ("fd12:3456::/32", True), ("2001:db8::/32", False),
])
def test_cidr_private_range_table_rfc1918_rfc6598_and_ula(cidr, private):
    assert (ids(plan([net("n", cidr)])) == []) is private


def test_cidr_sizing_after_reserved_addresses_and_headroom():
    assert plan([net("s", "10.0.0.0/24", required=251)])["undersized_count"] == 0  # 256 - 5 reserved
    result = plan([net("s", "10.0.0.0/24", required=252)])
    assert result["undersized_count"] == 1 and [(f["id"], f["severity"]) for f in result["findings"]] == [("undersized:s", "major")]
    assert plan([net("s", "10.0.0.0/24", required=200)], headroom_pct=25)["undersized_count"] == 0  # 250 <= 251
    assert plan([net("s", "10.0.0.0/24", required=201)], headroom_pct=25)["undersized_count"] == 1  # 251.25 > 251
    assert plan([net("s", "10.0.0.0/24", required=256)], reserved_addresses=0)["undersized_count"] == 0
    assert plan([net("s", "10.0.0.0/24", required=257)], reserved_addresses=0)["undersized_count"] == 1
    assert plan([net("tiny", "10.0.0.0/32", required=1)])["undersized_count"] == 1  # nothing left after the reserved addresses


def test_cidr_findings_are_truncated_at_100_but_counts_are_exact():
    networks = [net(f"n{i:02d}", "10.0.0.0/16") for i in range(60)]
    result = plan(networks)
    assert result["overlap_count"] == 1770 and len(result["findings"]) == 100 and result["findings_truncated"] is True


def test_cidr_rejects_semantic_problems_naming_the_field():
    invalid("cloud_cidr_plan", {"networks": [net("a", "10.0.0.1/16")]}, "networks")  # host bits set
    invalid("cloud_cidr_plan", {"networks": [net("a", "banana")]}, "networks")
    invalid("cloud_cidr_plan", {"networks": [net("a", "10.0.0.0/16"), net("a", "10.1.0.0/16")]}, "networks")
    assert "network a:" in call("cloud_cidr_plan", {"networks": [net("a", "10.0.0.1/16")]})["message"]


# --- cloud_managed_vs_self_hosted -------------------------------------------------------------------------------


def crit(cid, weight, managed, hosted, critical=False):
    return {"id": cid, "weight": weight, "managed_score": managed, "self_hosted_score": hosted, "critical": critical}


def matrix(criteria, **extra):
    return call("cloud_managed_vs_self_hosted", {"criteria": criteria, **extra})


def test_matrix_weighted_scores_recommendation_and_decisive_criteria():
    result = matrix([crit("ops-burden", 8, 5, 2), crit("cost", 5, 2, 4)])
    assert (result["managed_score_pct"], result["self_hosted_score_pct"], result["margin_pct"]) == (76.92, 55.38, 21.54)
    assert result["recommendation"] == "managed" and result["disqualified"] == [] and result["decisive_criteria"] == ["ops-burden", "cost"]
    assert result["findings"] == [] and result["verdict"] == "pass"
    flipped = matrix([crit("ops-burden", 1, 5, 2), crit("cost", 9, 2, 4)])
    assert flipped["recommendation"] == "self_hosted" and flipped["decisive_criteria"] == ["cost", "ops-burden"]


def test_matrix_equal_scores_are_a_tie_with_a_minor_finding():
    result = matrix([crit("a", 3, 3, 3), crit("b", 2, 4, 4)])
    assert result["recommendation"] == "tie" and result["margin_pct"] == 0.0 and result["decisive_criteria"] == []
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("tie", "minor")] and result["verdict"] == "warn"


def test_matrix_tie_margin_is_exclusive_on_the_tie_side():
    criteria = [crit("a", 1, 3, 3), crit("b", 1, 3, 2)]  # managed 60, self-hosted 50: margin 10 points
    assert matrix(criteria, tie_margin_pct=10)["recommendation"] == "managed"
    assert matrix(criteria, tie_margin_pct=10.01)["recommendation"] == "tie"
    assert matrix(criteria)["recommendation"] == "managed"  # default tie margin is 5 points
    four_apart = [crit(c, 1, 3, 3) for c in "abcd"] + [crit("e", 1, 4, 3)]  # 64 versus 60 points
    assert matrix(four_apart)["recommendation"] == "tie"
    five_apart = [crit(c, 1, 3, 3) for c in "abc"] + [crit("d", 1, 4, 3)]  # 65 versus 60: exactly the default margin
    assert matrix(five_apart)["recommendation"] == "managed"


def test_matrix_critical_criterion_scored_1_disqualifies_an_option_regardless_of_the_totals():
    result = matrix([crit("cost", 10, 5, 1), crit("compliance", 1, 1, 5, critical=True)])
    assert result["recommendation"] == "self_hosted" and result["disqualified"] == ["managed"]
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("knocked-out:managed", "info")] and result["verdict"] == "pass"
    non_critical = matrix([crit("cost", 10, 5, 1), crit("compliance", 1, 1, 5)])
    assert non_critical["recommendation"] == "managed" and non_critical["disqualified"] == []


def test_matrix_when_both_options_are_disqualified_there_is_no_recommendation():
    result = matrix([crit("sovereignty", 5, 1, 5, critical=True), crit("uptime", 5, 5, 1, critical=True)])
    assert result["recommendation"] == "none" and result["disqualified"] == ["managed", "self_hosted"]
    assert severity_of(result, "no-viable-option") == "blocker" and result["verdict"] == "fail"


def test_matrix_rejects_duplicate_criteria_naming_the_field():
    invalid("cloud_managed_vs_self_hosted", {"criteria": [crit("a", 1, 1, 1), crit("a", 1, 1, 1)]}, "criteria")


# --- cloud_replica_placement ------------------------------------------------------------------------------------


def spread(*counts):
    return [{"az": f"az-{i}", "replicas": n} for i, n in enumerate(counts)]


def replicas(counts, tolerate=1, mode="majority", **extra):
    return call("cloud_replica_placement", {"placements": spread(*counts), "quorum_mode": mode, "tolerate_az_failures": tolerate, **extra})


def test_replicas_three_in_three_azs_survive_one_az_loss():
    result = replicas([1, 1, 1])
    assert (result["total_replicas"], result["az_count"], result["required_live"], result["worst_case_remaining"]) == (3, 3, 2, 2)
    assert result["survives_az_failures"] is True and result["min_azs_for_tolerance"] == 3 and result["quorums_intersect"] is True
    assert result["findings"] == [] and result["verdict"] == "pass"


def test_replicas_three_in_two_azs_lose_quorum_when_the_larger_az_fails():
    result = replicas([2, 1])
    assert (result["worst_case_remaining"], result["survives_az_failures"], result["min_azs_for_tolerance"]) == (1, False, 3)
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("az-loss-breaks-quorum", "blocker")]
    assert result["findings"][0]["summary"] == "losing the 1 largest AZ(s) leaves 1 replicas, 2 needed; spread across at least 3 AZs"
    assert result["verdict"] == "fail"


def test_replicas_five_in_a_2_2_1_spread_survive_and_a_3_1_1_spread_does_not():
    good = replicas([2, 2, 1])
    assert good["survives_az_failures"] is True and good["worst_case_remaining"] == 3 and good["min_azs_for_tolerance"] == 3
    skewed = replicas([3, 1, 1])
    assert skewed["survives_az_failures"] is False and skewed["worst_case_remaining"] == 2
    assert ids(skewed) == ["az-loss-breaks-quorum", "unbalanced-spread"]
    assert "rebalance replicas across the existing AZs" in skewed["findings"][0]["summary"]


def test_replicas_even_counts_are_minor_and_need_a_wider_spread():
    result = replicas([2, 1, 1])
    assert result["required_live"] == 3 and result["survives_az_failures"] is False and result["min_azs_for_tolerance"] == 4
    assert severity_of(result, "even-replica-count") == "minor"
    assert severity_of(replicas([1, 1, 1, 1], 0), "even-replica-count") == "minor"


def test_replicas_failures_beyond_what_any_spread_tolerates_report_minus_one():
    result = replicas([1, 1])  # 2 replicas need both up
    assert result["min_azs_for_tolerance"] == -1 and result["survives_az_failures"] is False
    assert "no AZ spread of this replica count can tolerate it" in result["findings"][0]["summary"]
    assert replicas([2, 2, 1], 2)["min_azs_for_tolerance"] == 5


def test_replicas_zero_tolerated_failures_always_survive_and_single_replica_cannot_tolerate_one():
    assert replicas([1], 0)["survives_az_failures"] is True
    one = replicas([1], 1)
    assert one["survives_az_failures"] is False and one["min_azs_for_tolerance"] == -1


def test_replicas_explicit_quorums_must_intersect_and_gate_survival_by_the_larger_quorum():
    bad = replicas([2, 2, 1], mode="explicit", read_quorum=2, write_quorum=3)  # r + w = 5 is not > 5
    assert bad["quorums_intersect"] is False and severity_of(bad, "quorums-do-not-intersect") == "blocker"
    good = replicas([2, 2, 1], mode="explicit", read_quorum=3, write_quorum=3)
    assert good["quorums_intersect"] is True and good["required_live"] == 3 and good["survives_az_failures"] is True
    assert good["findings"] == []
    no_conflict_free = replicas([1, 1, 1, 1], 0, mode="explicit", read_quorum=3, write_quorum=2)  # r + w = 5 > 4 but 2w = 4 is not > 4
    assert no_conflict_free["quorums_intersect"] is False


def test_replicas_reject_semantic_problems_naming_the_field():
    invalid("cloud_replica_placement", {"placements": spread(1, 1, 1), "quorum_mode": "explicit", "tolerate_az_failures": 1}, "read_quorum")
    invalid("cloud_replica_placement", {"placements": spread(1, 1, 1), "quorum_mode": "explicit", "tolerate_az_failures": 1, "read_quorum": 2},
            "write_quorum")
    invalid("cloud_replica_placement", {"placements": spread(1, 1, 1), "quorum_mode": "explicit", "tolerate_az_failures": 1,
                                        "read_quorum": 4, "write_quorum": 2}, "read_quorum")
    invalid("cloud_replica_placement", {"placements": [{"az": "a", "replicas": 1}, {"az": "a", "replicas": 1}], "quorum_mode": "majority",
                                        "tolerate_az_failures": 1}, "placements")


# --- cloud_latency_budget ---------------------------------------------------------------------------------------


def hop(name, p50, p99, calls=1, group=None):
    item = {"name": name, "p50_ms": p50, "p99_ms": p99, "calls": calls}
    if group is not None:
        item["group"] = group
    return item


REF_HOPS = [hop("edge", 5, 20), hop("api", 30, 120), hop("db", 3, 25, 2)]


def budget(hops, target=300):
    return call("cloud_latency_budget", {"p99_target_ms": target, "hops": hops})


def test_latency_budget_sequential_composition_and_stage_shares():
    result = ok("cloud_latency_budget", {"p99_target_ms": 300, "hops": REF_HOPS})
    assert (result["critical_path_p99_ms"], result["critical_path_p50_ms"], result["budget_remaining_ms"]) == (190.0, 41.0, 110.0)
    assert result["meets_target"] is True and result["tail_hit_pct"] == 3.94  # 1 - 0.99^4 over 4 calls
    assert result["stages"] == [{"name": "api", "p99_ms": 120.0, "share_pct": 63.16}, {"name": "db", "p99_ms": 50.0, "share_pct": 26.32},
                                {"name": "edge", "p99_ms": 20.0, "share_pct": 10.53}]
    assert result["findings"] == [] and result["verdict"] == "pass"


def test_latency_budget_target_is_inclusive_and_over_budget_is_major():
    assert budget(REF_HOPS, 190)["meets_target"] is True
    over = budget(REF_HOPS, 189.99)
    assert over["meets_target"] is False and over["budget_remaining_ms"] == -0.01
    assert severity_of(over, "over-budget") == "major" and over["verdict"] == "fail"


def test_latency_budget_dominant_stage_needs_more_than_half_of_the_target():
    assert "dominant-stage:api" not in ids(budget(REF_HOPS, 240))  # 120 is exactly half
    result = budget(REF_HOPS, 239.99)
    assert severity_of(result, "dominant-stage:api") == "minor"


def test_latency_budget_parallel_group_contributes_its_slowest_member():
    hops = [hop("front", 5, 10), hop("search", 20, 40, group="fanout"), hop("ads", 30, 90, group="fanout"), hop("rank", 10, 30, 2, group="fanout")]
    result = budget(hops, 500)
    # fanout = max(40, 90, 30*2) = 90; critical path 10 + 90
    assert result["critical_path_p99_ms"] == 100.0 and result["critical_path_p50_ms"] == 35.0  # p50: 5 + max(20, 30, 10*2)
    assert [s["name"] for s in result["stages"]] == ["group:fanout", "front"]
    assert result["tail_hit_pct"] == 4.90  # 5 calls


def test_latency_budget_tail_amplification_threshold_is_ten_percent():
    ten = budget([hop(f"h{i}", 1, 2) for i in range(10)], 1000)
    assert ten["tail_hit_pct"] == 9.56 and "tail-amplification" not in ids(ten)
    eleven = budget([hop(f"h{i}", 1, 2) for i in range(11)], 1000)
    assert eleven["tail_hit_pct"] == 10.47 and severity_of(eleven, "tail-amplification") == "minor" and eleven["verdict"] == "warn"


def test_latency_budget_zero_latency_path_has_zero_shares():
    result = budget([hop("noop", 0, 0)], 10)
    assert result["critical_path_p99_ms"] == 0.0 and result["stages"] == [{"name": "noop", "p99_ms": 0.0, "share_pct": 0.0}]


def test_latency_budget_lists_only_the_five_largest_stages():
    result = budget([hop(f"h{i}", 1, 10 + i) for i in range(8)], 1000)
    assert [s["name"] for s in result["stages"]] == ["h7", "h6", "h5", "h4", "h3"]


def test_latency_budget_rejects_semantic_problems_naming_the_field():
    invalid("cloud_latency_budget", {"p99_target_ms": 100, "hops": [hop("a", 50, 10)]}, "hops")
    invalid("cloud_latency_budget", {"p99_target_ms": 100, "hops": [hop("a", 1, 10), hop("a", 1, 10)]}, "hops")
    invalid("cloud_latency_budget", {"p99_target_ms": 100, "hops": [hop("group:g", 1, 10), hop("b", 1, 10, group="g")]}, "hops")
    invalid("cloud_latency_budget", {"p99_target_ms": 0, "hops": [hop("a", 1, 2)]}, "p99_target_ms")


# --- review regressions: exactness, ties, strict CIDR, fail-closed audit, secret safety ---------------------------


def test_composite_availability_extreme_redundancy_reports_the_nines_instead_of_zero():
    # 1-of-27 replicas at 99.9 each: unavailability 0.001^27 = 1e-81, which an 80-digit context rounds away
    result = ok("cloud_composite_availability", {"target_pct": 99.99, "tiers": [tier("wide", 99.9, 27, 1)]})
    assert result["composite_pct"] == 100.0 and result["downtime_min_per_year"] == 0.0
    assert result["nines"] == 81.0 and result["meets_target"] is True
    fifty = ok("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("deep", 99.99999999999999, 50, 1)]})
    assert fifty["nines"] == 800.0  # (1e-16)^50


def test_composite_availability_rounds_half_up_on_the_decimal_value_as_written():
    for written, expected in ((99.0000005, 99.000001), (99.0000015, 99.000002), (99.0000025, 99.000003)):
        result = ok("cloud_composite_availability", {"target_pct": 50, "tiers": [tier("a", written)]})
        assert result["composite_pct"] == expected, written


def test_wellarch_scores_round_half_up_not_to_even_on_exact_ties():
    names = ["operational_excellence", "security", "reliability", "performance_efficiency", "cost_optimization", "sustainability"]
    weights = dict(zip(names, (1, 1, 1, 3, 5, 5)))
    answers = [answer(n, "q", 1, "medium" if n == "performance_efficiency" else "none") for n in names]
    result = ok("cloud_wellarch_scorecard", {"answers": answers, "pillar_weights": weights})
    assert result["overall_score_pct"] == 90.63  # (100 * 13 + 50 * 3) / 16 = 90.625 exactly


def test_cell_capacity_rounds_half_up_and_survives_a_denormal_load():
    result = cells(load_rps=5e-324, cell_max_rps=1, target_utilisation_pct=100, growth_pct=0, spare_cells=0, current_cells=1)
    assert result["state"] == "SUCCESS" and result["cells_for_load"] == 1 and result["headroom_pct"] == 1.7976931348623157e308


def test_matrix_exactly_equal_totals_are_a_tie_even_with_a_zero_tie_margin():
    result = matrix([crit("a", 3, 3, 3), crit("b", 2, 4, 4)], tie_margin_pct=0)
    assert result["recommendation"] == "tie" and result["margin_pct"] == 0.0
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("tie", "minor")]
    near = matrix([crit("a", 1, 3, 3), crit("b", 1, 3, 2)], tie_margin_pct=0)  # 60 versus 50 points
    assert near["recommendation"] == "managed"


@pytest.mark.parametrize("cidr", ["10.0.0.0", "10.0.0.0/255.255.255.0", "10.0.0.0/0.0.0.255", "fe80::%eth0/64", "10.0.0.0/8 ", " 10.0.0.0/8", "10.0.0.0/08x"])
def test_cidr_requires_address_slash_prefix_length_form(cidr):
    result = call("cloud_cidr_plan", {"networks": [net("a", cidr)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "networks" and result["message"].startswith("network a:")


def test_cidr_error_messages_say_what_is_wrong_without_echoing_the_value():
    host_bits = call("cloud_cidr_plan", {"networks": [net("a", "10.0.0.1/24")]})
    assert host_bits["message"] == "network a: host bits are set; give the network address with its prefix length"
    garbage = call("cloud_cidr_plan", {"networks": [net("a", "https://user:pass@host/")]})
    assert garbage["message"] == "network a: not an IPv4 or IPv6 address/prefix-length"
    assert "pass" not in json.dumps(garbage)


def test_cidr_ipv6_global_unicast_is_the_normal_cloud_allocation_and_not_flagged():
    assert plan([net("v6", "2600:1f18::/56"), net("ula", "fd00::/48")])["findings"] == []
    assert ids(plan([net("ll", "fe80::/10")])) == ["public-range:ll"]
    assert ids(plan([net("v4", "2.0.0.0/8")])) == ["public-range:v4"]


def test_dr_a_weaker_strategy_that_suffices_is_not_called_stronger():
    # warm standby replicates 60 minutes behind: it misses a 30 minute RPO that a 10 minute backup interval meets
    payload = {**DR_ALL, "strategy": "warm_standby", "replication_lag_s": 3600, "rto_target_min": 240, "rpo_target_min": 30,
               "backup_interval_min": 10, "infra_provision_min": 20, "data_restore_min": 30}
    result = ok("cloud_dr_feasibility", payload)
    assert result["cheapest_sufficient_strategy"] == "backup_restore"
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [("rpo-missed", "blocker"), ("weaker-strategy-sufficient", "info")]


def test_twelve_factor_undeclared_checks_fail_closed_when_the_check_is_major_or_blocker_class():
    descriptor = dict(CONFORMANT)
    del descriptor["secrets_in_codebase"], descriptor["dependencies_locked"]
    result = ok("cloud_twelve_factor_audit", {"service": descriptor})
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [
        ("factor-03:secrets_in_codebase", "major"), ("factor-02:dependencies_locked", "minor")]
    assert result["verdict"] == "fail"
    only_minor = dict(CONFORMANT)
    del only_minor["sticky_sessions"]
    assert ok("cloud_twelve_factor_audit", {"service": only_minor})["verdict"] == "warn"


def test_residency_findings_for_one_resource_in_two_classes_keep_distinct_ids():
    result = residency([place("db", "pii", "us-east-1"), place("db", "pii", "us-east-1", "replica")])
    assert ids(result) == ["residency-violation:db:us-east-1", "residency-violation:db:us-east-1#2"]


CANARIES = [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
    "https://user:hunter2pass@host.example/",
]
SECRET_MARKERS = ("AKIAIOSFODNN7EXAMPLE", "ghp_a1B2", "0123456789abcdef0123456789abcdef", "abandon abandon", "hunter2pass")


def _canary_payloads(canary: str) -> dict[str, list[dict]]:
    """Per tool: payloads that carry the canary in every echoed string field, one that succeeds and one that is rejected."""
    hop_a, hop_b = hop(canary, 1, 2, group=canary), hop(canary, 1, 2)
    return {
        "cloud_composite_availability": [
            {"target_pct": 99.0, "tiers": [tier(canary, 99.5)]}, {"target_pct": 99.0, "tiers": [tier(canary, 99.5), tier(canary, 99.5)]},
            {"target_pct": 99.0, "tiers": [tier(canary, 99.5, 2, 3)]}, {"target_pct": 99.99, "tiers": [tier(canary, 99.0)]}],
        "cloud_data_residency": [
            {"regions": [{"region": canary, "jurisdiction": canary}], "rules": [{"data_class": canary, "allowed_jurisdictions": ["EU"]}],
             "placements": [place(canary, canary, canary)]},
            {"regions": REGIONS, "rules": RULES, "placements": [place(canary, canary, canary)]},
            {"regions": REGIONS, "rules": RULES, "placements": [place(canary, canary, "eu-west-1")]}],
        "cloud_landing_zone_lint": [
            {"accounts": [account(canary, "sandbox", canary)], "break_glass": GOOD_GLASS},
            {"accounts": [account(canary, "sandbox", "A"), account(canary, "network", "B")], "break_glass": GOOD_GLASS},
            {"accounts": [account(canary, "workload_prod", canary + "//x")], "break_glass": GOOD_GLASS}],
        "cloud_wellarch_scorecard": [
            {"answers": full_answers() + [answer("security", canary, 5, "high")]},
            {"answers": full_answers() + [answer("security", canary, 5, "high"), answer("security", canary, 5, "high")]}],
        "cloud_blast_radius": [
            {"services": [svc(canary, 9, [{"region": canary, "az": canary, "cell": canary}], [canary])], "max_az_blast_pct": 10},
            {"services": [svc(canary, 9, [inst(canary)], ["ghost-" + canary])], "max_az_blast_pct": 10},
            {"services": [svc(canary, 9, [inst("a")]), svc(canary, 9, [inst("b")])], "max_az_blast_pct": 10}],
        "cloud_cidr_plan": [
            {"networks": [net(canary, canary)]}, {"networks": [net(canary, "10.0.0.0/16", group=canary), net("sibling", "10.0.0.0/16", group=canary)]},
            {"networks": [net(canary, "10.0.0.0/16"), net(canary, "10.1.0.0/16")]},
            {"networks": [net("a", canary + "/8"), net("sized", "10.0.0.0/8", required=10 ** 9)]}],
        "cloud_managed_vs_self_hosted": [
            {"criteria": [crit(canary, 5, 5, 1, critical=True), crit("b", 1, 1, 5, critical=True)]},
            {"criteria": [crit(canary, 5, 5, 1), crit(canary, 1, 1, 5)]}],
        "cloud_replica_placement": [
            {"placements": [{"az": canary, "replicas": 2}, {"az": "az-other", "replicas": 1}], "quorum_mode": "majority", "tolerate_az_failures": 1},
            {"placements": [{"az": canary, "replicas": 2}, {"az": canary, "replicas": 1}], "quorum_mode": "majority", "tolerate_az_failures": 1}],
        "cloud_latency_budget": [
            {"p99_target_ms": 1, "hops": [hop_a, hop("other", 1, 2, group=canary)]},
            {"p99_target_ms": 1, "hops": [hop_b, hop(canary, 1, 2)]},
            {"p99_target_ms": 1, "hops": [hop("group:" + canary, 1, 2), hop("b", 1, 2, group=canary)]}],
    }


@pytest.mark.parametrize("canary", CANARIES)
@pytest.mark.parametrize("name", [n for n in TOOL_NAMES if n not in ("cloud_twelve_factor_audit", "cloud_dr_feasibility", "cloud_cell_capacity")])
def test_no_tool_echoes_a_secret_shaped_input_value_in_any_result_field(name, canary):
    for payload in _canary_payloads(canary)[name]:
        text = json.dumps(call(name, payload))
        assert not any(marker in text for marker in SECRET_MARKERS), (name, text)


def test_secret_shaped_identifiers_keep_the_relationships_between_fields_intact():
    canary = CANARIES[1]
    chain = blast([svc(canary, 9, [inst("a")]), svc("api", 5, [inst("a"), inst("b")], [canary])], 100)
    assert chain["state"] == "SUCCESS" and chain["spof_services"] and canary not in json.dumps(chain)
    assert next(r for r in chain["ranking"] if r["domain"] == "az:r1/a")["down_services"] == sorted(chain["spof_services"] + ["api"])


# --- review: exact values and boundaries from an independent Fraction re-computation -----------------------------


def test_composite_availability_three_of_five_binomial_and_a_shared_tier_in_series():
    # sum_{k=3..5} C(5,k) 0.99^k 0.01^(5-k) = 0.9999901494 ; times 0.999
    result = ok("cloud_composite_availability", {"target_pct": 99.9, "tiers": [tier("quorum", 99, 5, 3), tier("zone", 99.9, 3, 1, shared=True)]})
    assert result["tiers"] == [{"id": "zone", "effective_pct": 99.9}, {"id": "quorum", "effective_pct": 99.999015}]
    assert result["composite_pct"] == 99.899016 and result["downtime_min_per_year"] == 530.77 and result["nines"] == 3.0
    assert result["meets_target"] is False and ids(result) == ["chain-below-target", "redundancy-not-credited:zone"]


def test_dr_zero_figures_meet_a_zero_target_inclusively_and_margins_are_exact():
    payload = {"strategy": "active_active", "rto_target_min": 0, "rpo_target_min": 0, "detect_decide_min": 0, "traffic_cutover_min": 0,
               "validation_min": 0, "synchronous_replication": True}
    result = ok("cloud_dr_feasibility", payload)
    assert (result["achievable_rto_min"], result["achievable_rpo_min"], result["rto_margin_min"], result["rpo_margin_min"]) == (0.0, 0.0, 0.0, 0.0)
    assert result["meets_rto"] and result["meets_rpo"] and result["verdict"] == "pass" and result["findings"] == []
    assert result["evaluated_strategies"] == ["active_active"] and result["cheapest_sufficient_strategy"] == "active_active"


def test_replica_placement_more_failures_than_azs_leaves_nothing():
    result = replicas([1, 1, 1], 5)
    assert (result["worst_case_remaining"], result["survives_az_failures"], result["min_azs_for_tolerance"]) == (0, False, -1)


def test_cell_capacity_non_integral_ratio_rounds_the_cell_count_up():
    result = ok("cloud_cell_capacity", {"load_rps": 1250, "cell_max_rps": 10, "target_utilisation_pct": 99.5, "growth_pct": 0,
                                        "spare_cells": 2, "current_cells": 128})
    assert (result["usable_rps_per_cell"], result["cells_for_load"], result["cells_required"]) == (9.95, 126, 128)  # 1250 / 9.95 = 125.63
    assert result["headroom_pct"] == 1.89 and result["blast_radius_pct"] == 0.7813 and result["current_cells_sufficient"] is True


def test_cidr_sizing_on_ipv6_and_with_a_provider_that_reserves_four():
    assert plan([net("v6", "fd00::/127", required=1)], reserved_addresses=1)["undersized_count"] == 0  # 2 addresses - 1 reserved
    assert plan([net("v6", "fd00::/127", required=2)], reserved_addresses=1)["undersized_count"] == 1
    assert plan([net("gcp", "10.0.0.0/24", required=252)], reserved_addresses=4)["undersized_count"] == 0  # 256 - 4


def test_residency_jurisdiction_labels_are_case_folded_not_normalised_across_scripts():
    rules = [{"data_class": "pii", "allowed_jurisdictions": ["Stra\u00dfe"]}]
    assert residency([place("db", "pii", "r")], regions=[{"region": "r", "jurisdiction": " STRASSE "}], rules=rules)["violation_count"] == 0
    assert residency([place("db", "pii", "r")], regions=[{"region": "r", "jurisdiction": "\uff25\uff35"}], rules=[{"data_class": "pii", "allowed_jurisdictions": ["EU"]}])["violation_count"] == 1  # fullwidth EU is not EU: fail closed


def test_latency_budget_hedged_calls_in_one_group_take_the_slowest_and_all_count_for_the_tail():
    hops = [hop("a", 10, 40, 2, group="g"), hop("b", 30, 50, 1, group="g")]  # 80 versus 50 -> 80 ; p50 20 versus 30 -> 30
    result = budget(hops, 100)
    assert (result["critical_path_p99_ms"], result["critical_path_p50_ms"], result["tail_hit_pct"]) == (80.0, 30.0, 2.97)


def test_secret_shaped_ids_become_stable_kind_length_digest_labels():
    result = ok("cloud_composite_availability", {"target_pct": 99.0, "tiers": [tier(CANARIES[0], 99.5), tier(CANARIES[2], 98.0)]})
    assert [t["id"] for t in result["tiers"]] == ["[redacted:hex-key:64:" + CANARY_DIGESTS[2] + "]", "[redacted:credential:20:" + CANARY_DIGESTS[0] + "]"]
    assert result["weakest_tier"] == "[redacted:hex-key:64:" + CANARY_DIGESTS[2] + "]"
    assert ok("cloud_composite_availability", {"target_pct": 99.0, "tiers": [tier("edge", 99.5)]})["weakest_tier"] == "edge"  # ordinary ids pass through


CANARY_DIGESTS = {0: "1a5d44", 2: "a8ae6e"}


# --- QA-03 regressions ----------------------------------------------------------------------------------------------


def direct(handler: str, payload: dict) -> dict:
    """Call a handler without the registry, which validates first and refuses booleans and zero before the handler runs."""
    return getattr(importlib.import_module("swarm.tools.a16_tools"), handler)(payload)


def test_blast_domains_with_slashes_in_region_or_az_names_are_distinct_failure_domains():
    result = blast([svc("X", 5, [inst("1", region="us/east")]), svc("Y", 5, [inst("east/1", region="us")])], 50)
    assert [(r["domain"], r["blast_pct"], r["down_services"]) for r in result["ranking"]] == [
        ("az:us%2Feast/1", 50.0, ["X"]), ("az:us/east%2F1", 50.0, ["Y"]), ("region:us", 50.0, ["Y"]), ("region:us%2Feast", 50.0, ["X"])]
    assert ids(result) == ["spof:X", "spof:Y"] and result["verdict"] == "warn"  # neither AZ is above the 50% limit


def test_blast_domain_labels_escape_percent_so_escaped_and_literal_names_stay_apart():
    result = blast([svc("s", 5, [inst("a", cell="c/1"), inst("b", cell="c%2F1")])], 100)
    labels = [r["domain"] for r in result["ranking"]]
    assert {"cell:c%2F1", "cell:c%252F1"} <= set(labels) and len(labels) == len(set(labels))


def test_landing_zone_whitespace_around_ou_names_is_not_part_of_the_name():
    for padded in ("Workloads/Prod ", " Workloads / Prod "):
        zone = good_zone()
        zone[4]["ou"] = padded  # the non-prod account, padded: the same OU as prod once each name is normalised
        result = lz(zone)
        assert severity_of(result, "prod-nonprod-shared-ou:Workloads/Prod") == "major", padded
        assert result["ou_count"] == 3 and result["verdict"] == "fail", padded


@pytest.mark.parametrize("key,value,factor,severity", [
    ("repos_per_app", True, 1, "major"), ("hardcoded_backing_service_hosts", False, 4, "major"),
    ("startup_seconds", True, 9, "minor"), ("deploy_gap_days", "1", 10, "minor"),
])
def test_twelve_factor_booleans_and_non_numbers_fail_their_check_instead_of_passing_or_crashing(key, value, factor, severity):
    result = direct("twelve_factor_audit", {"service": {**CONFORMANT, key: value}})
    assert [(f["id"], f["severity"]) for f in result["findings"]] == [(f"factor-{factor:02d}:{key}", severity)]


def test_cell_capacity_zero_load_or_zero_utilisation_is_invalid_input_on_a_direct_call():
    zero_load = direct("cell_capacity", {**CELL, "load_rps": 0})
    assert zero_load["state"] == "INVALID_INPUT" and zero_load["field"] == "load_rps"
    zero_utilisation = direct("cell_capacity", {**CELL, "target_utilisation_pct": 0})
    assert zero_utilisation["state"] == "INVALID_INPUT" and zero_utilisation["field"] == "target_utilisation_pct"


def test_landing_zone_empty_evaluation_scores_zero_and_cannot_pass_without_accounts_or_guardrail_evidence():
    for accounts in ([], [account("mgmt", "management", "")]):
        result = direct("landing_zone_lint", {"accounts": accounts, "break_glass": GOOD_GLASS})
        assert result["guardrail_coverage_pct"] == 0.0, accounts
        assert severity_of(result, "guardrails-not-evaluated") == "major" and result["verdict"] == "fail", accounts
        assert "guardrails-not-evaluated" in ids(result), accounts


# --- QA-03 re-review: availability at 100% and numeric ranges ----------------------------------------------------------


@pytest.mark.parametrize("replicas,needed,shared", [(1, 1, False), (3, 2, False), (2, 1, True)])
def test_composite_availability_of_exactly_100_percent_is_invalid_input_naming_availability_pct(replicas, needed, shared):
    result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 100, replicas, needed, shared)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "availability_pct"


def test_composite_availability_just_below_100_percent_still_computes():
    assert direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 99.9999, 3, 2)]})["state"] == "SUCCESS"


@pytest.mark.parametrize("key,value", [
    ("startup_seconds", -1), ("startup_seconds", 86401), ("startup_seconds", 10 ** 400), ("startup_seconds", float("nan")),
    ("startup_seconds", float("inf")), ("deploy_gap_days", -0.5), ("deploy_gap_days", 3651),
])
def test_twelve_factor_numeric_outside_the_schema_range_is_invalid_input_naming_the_field(key, value):
    result = direct("twelve_factor_audit", {"service": {**CONFORMANT, key: value}})
    assert result["state"] == "INVALID_INPUT" and result["field"] == key


def test_twelve_factor_numeric_range_boundaries_are_inclusive_and_a_boolean_is_not_out_of_range():
    for key, value in (("startup_seconds", 0), ("startup_seconds", 86400), ("deploy_gap_days", 3650)):
        assert direct("twelve_factor_audit", {"service": {**CONFORMANT, key: value}})["state"] == "SUCCESS", (key, value)
    result = direct("twelve_factor_audit", {"service": {**CONFORMANT, "startup_seconds": True}})
    assert result["state"] == "SUCCESS" and "factor-09:startup_seconds" in ids(result)  # a failing check, not a range error


# --- QA-03 second re-verification: zero availability with no replica needed, and integers beyond the conversion limit -----


def test_zero_availability_with_no_replica_needed_is_invalid_input_naming_availability_pct_on_a_direct_call():
    # min_required 0 is outside the registered schema (minimum 1), so only a direct call reaches the 0**0 in the k-of-n term
    result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 0, 1, 0)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "availability_pct"
    assert "min_required" in result["message"]


def test_zero_availability_with_one_replica_needed_still_computes_a_zero_tier():
    result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 0, 1, 1)]})
    assert result["state"] == "SUCCESS" and result["composite_pct"] == 0.0 and result["verdict"] == "fail"


@pytest.mark.parametrize("key,value", [
    ("startup_seconds", 10 ** 5000), ("startup_seconds", -(10 ** 5000)), ("deploy_gap_days", 10 ** 5000),
], ids=["startup-plus", "startup-minus", "deploy-plus"])
def test_twelve_factor_integer_beyond_the_conversion_limit_is_invalid_input_naming_the_field(key, value):
    result = direct("twelve_factor_audit", {"service": {**CONFORMANT, key: value}})
    assert result["state"] == "INVALID_INPUT" and result["field"] == key


def test_composite_availability_integer_beyond_the_conversion_limit_is_invalid_input_naming_availability_pct():
    result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 10 ** 5000, 3, 2)]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "availability_pct"


def test_cell_capacity_load_beyond_the_conversion_limit_is_invalid_input_naming_load_rps_on_a_direct_call():
    for load in (10 ** 5000, -(10 ** 5000)):
        result = direct("cell_capacity", {**CELL, "load_rps": load})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "load_rps", load


def test_composite_availability_target_beyond_the_conversion_limit_is_invalid_input_naming_target_pct_on_a_direct_call():
    for target in (10 ** 5000, -(10 ** 5000)):
        result = direct("composite_availability", {"target_pct": target, "tiers": [tier("a", 99)]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "target_pct", target


def test_cell_capacity_cell_max_rps_beyond_the_conversion_limit_is_invalid_input_on_a_direct_call():
    for value in (10 ** 5000, -(10 ** 5000)):
        result = direct("cell_capacity", {**CELL, "cell_max_rps": value})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "cell_max_rps", value


def test_cell_capacity_target_utilisation_beyond_the_conversion_limit_is_invalid_input_on_a_direct_call():
    for value in (10 ** 5000, -(10 ** 5000)):
        result = direct("cell_capacity", {**CELL, "target_utilisation_pct": value})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "target_utilisation_pct", value


def test_cell_capacity_growth_beyond_the_conversion_limit_is_invalid_input_on_a_direct_call():
    for value in (10 ** 5000, -(10 ** 5000)):
        result = direct("cell_capacity", {**CELL, "growth_pct": value})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "growth_pct", value


# --- QA-03 round two: schema bounds, bool/NaN/inf, target_pct interval, replicas maximum --------------------------------


def test_cell_capacity_load_or_growth_outside_the_schema_is_invalid_input_naming_the_field():
    # schema: load_rps exclusiveMinimum 0 and maximum 1e9; growth_pct 0..1000 (a negative growth gave a negative projected load)
    for changes, field in (({"load_rps": -5}, "load_rps"), ({"load_rps": 0}, "load_rps"), ({"load_rps": 1000000001}, "load_rps"),
                           ({"growth_pct": -200}, "growth_pct"), ({"growth_pct": 1001}, "growth_pct")):
        result = direct("cell_capacity", {**CELL, **changes})
        assert result["state"] == "INVALID_INPUT" and result["field"] == field, changes
    assert direct("cell_capacity", {**CELL, "growth_pct": 1000})["state"] == "SUCCESS"  # 1000 is the schema maximum


def test_cell_capacity_spare_cells_outside_the_schema_or_the_conversion_limit_is_invalid_input_naming_spare_cells():
    # schema: spare_cells integer 0..100
    for value in (10 ** 5000, -(10 ** 5000), 101, -1):
        result = direct("cell_capacity", {**CELL, "spare_cells": value})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "spare_cells", value
    assert direct("cell_capacity", {**CELL, "spare_cells": 100})["state"] == "SUCCESS"  # 100 is the schema maximum


def test_boolean_nan_and_infinity_on_every_numeric_field_are_invalid_input_naming_the_field():
    bad = (True, float("nan"), float("inf"))
    for field in ("load_rps", "cell_max_rps", "target_utilisation_pct", "growth_pct", "spare_cells", "current_cells"):
        for value in bad:
            result = direct("cell_capacity", {**CELL, field: value})
            assert result["state"] == "INVALID_INPUT" and result["field"] == field, (field, value)
    for value in bad:
        result = direct("composite_availability", {"target_pct": value, "tiers": [tier("a", 99)]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "target_pct", value
        result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", value)]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "availability_pct", value
    for field in ("replicas", "min_required"):
        result = direct("composite_availability", {"target_pct": 99, "tiers": [{**tier("a", 99, 3, 2), field: True}]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == field, field


def test_composite_availability_target_of_0_or_100_is_invalid_input_naming_target_pct_on_a_direct_call():
    # schema: target_pct exclusiveMinimum 0 and exclusiveMaximum 100; at 0 every chain passed, at 100 every chain failed
    for target in (0, 100, 0.0, 100.0):
        result = direct("composite_availability", {"target_pct": target, "tiers": [tier("a", 99)]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "target_pct", target
    assert direct("composite_availability", {"target_pct": 99.99, "tiers": [tier("a", 99)]})["state"] == "SUCCESS"


def test_composite_availability_replicas_beyond_the_schema_maximum_is_invalid_input_naming_replicas():
    # schema: replicas integer 1..50; the k-of-n sum grows with replicas, so 10**4 took 17 s and 10**7 did not finish
    for replicas in (51, 1000, 10 ** 4, 10 ** 7):
        result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 99.9, replicas, 2)]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "replicas", replicas
    assert direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 99.9, 50, 2)]})["state"] == "SUCCESS"


def test_integer_fields_reject_floats_and_still_accept_integers_and_continuous_fields_still_take_floats():
    # schema: spare_cells, current_cells, replicas and min_required are integers. 2.0 has an integral value but is not an integer:
    # it reached Fraction and range() as a float, and current_cells 3.0 returned SUCCESS with a float shortfall
    for field, value in (("spare_cells", 2.0), ("current_cells", 3.0)):
        result = direct("cell_capacity", {**CELL, field: value})
        assert result["state"] == "INVALID_INPUT" and result["field"] == field, field
    assert direct("cell_capacity", {**CELL, "spare_cells": 2})["state"] == "SUCCESS"
    assert direct("cell_capacity", {**CELL, "load_rps": 900.0})["state"] == "SUCCESS"  # a continuous field takes a float
    for field, value in (("replicas", 2.0), ("min_required", 1.0)):
        replicas, needed = (value, 1) if field == "replicas" else (2, value)
        result = direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 99.9, replicas, needed)]})
        assert result["state"] == "INVALID_INPUT" and result["field"] == field, field
    assert direct("composite_availability", {"target_pct": 99, "tiers": [tier("a", 99.9, 2, 1)]})["state"] == "SUCCESS"
