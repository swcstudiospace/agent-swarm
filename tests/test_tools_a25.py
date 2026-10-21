"""Behaviour tests for the A25 Resilience & Chaos Engineer tools.

Tools: resil_pdb_check, resil_spread_check, resil_probe_check, resil_backup_coverage, resil_restore_rto,
resil_chaos_spec_check, resil_fmea_rpn, resil_chaos_budget_gate, resil_shutdown_check, resil_drill_cadence,
resil_load_shed_check, resil_quorum_safety.

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
AGENT, SLUG, PREFIX = "A25", "a25-resilience", "resil_"
INTAKE = ROOT / "registry" / "intake"
TOOL_NAMES = ["resil_pdb_check", "resil_spread_check", "resil_probe_check", "resil_backup_coverage", "resil_restore_rto",
              "resil_chaos_spec_check", "resil_fmea_rpn", "resil_chaos_budget_gate", "resil_shutdown_check",
              "resil_drill_cadence", "resil_load_shed_check", "resil_quorum_safety"]
ZONE = "topology.kubernetes.io/zone"
LEGACY_ZONE = "failure-domain.beta.kubernetes.io/zone"
HOST = "kubernetes.io/hostname"


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


def found(result: dict) -> dict[tuple[str, str], str]:
    """{(rule id, subject): severity} of a result's findings."""
    return {(f["id"], f["subject"]): f["severity"] for f in result["findings"]}


def rules(result: dict) -> set[str]:
    return {f["id"] for f in result["findings"]}


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


# --- resil_pdb_check ------------------------------------------------------------------------------------------


def wl(name="web", replicas=4, kind="Deployment", **budget):
    return {"name": name, "kind": kind, "replicas": replicas, **budget}


def pdb(*workloads):
    return ok("resil_pdb_check", {"workloads": list(workloads)})


def test_pdb_healthy_budgets_pass_and_budgets_are_sorted_by_workload():
    result = pdb(wl("web", 4, min_available="50%"), wl("db", 3, kind="StatefulSet", max_unavailable="1"))
    assert result == {
        "state": "SUCCESS", "workloads_checked": 2, "without_pdb": 0, "blocking": 0, "unsafe": 0, "status": "pass", "findings": [],
        "budgets": [{"workload": "db", "has_pdb": True, "desired_healthy": 2, "disruptions_allowed": 1},
                    {"workload": "web", "has_pdb": True, "desired_healthy": 2, "disruptions_allowed": 2}]}


def test_pdb_percentages_round_up_for_both_fields():
    # 10 replicas: minAvailable 33% -> ceil(3.3) = 4 healthy; maxUnavailable 33% -> ceil(3.3) = 4 unavailable -> 6 healthy
    result = pdb(wl("a", 10, min_available="33%"), wl("b", 10, max_unavailable="33%"))
    assert {b["workload"]: (b["desired_healthy"], b["disruptions_allowed"]) for b in result["budgets"]} == {"a": (4, 6), "b": (6, 4)}
    assert found(result) == {("pdb-permissive", "a"): "minor"}


def test_pdb_missing_budget_by_replica_count():
    result = pdb(wl("two", 2), wl("one", 1), wl("zero", 0))
    assert found(result) == {("pdb-missing", "two"): "major", ("single-replica", "one"): "minor"}
    assert result["without_pdb"] == 3 and result["status"] == "fail"
    assert {b["workload"]: b["disruptions_allowed"] for b in result["budgets"]} == {"one": 1, "two": 2, "zero": 0}
    assert all(not b["has_pdb"] and b["desired_healthy"] == 0 for b in result["budgets"])


@pytest.mark.parametrize("budget", [{"min_available": "3"}, {"min_available": "100%"}, {"max_unavailable": "0"}, {"max_unavailable": "0%"}])
def test_pdb_zero_disruptions_blocks_eviction(budget):
    result = pdb(wl("db", 3, **budget))
    assert found(result) == {("pdb-blocks-eviction", "db"): "major"}
    assert result["blocking"] == 1 and result["budgets"][0]["disruptions_allowed"] == 0


def test_pdb_min_available_above_replicas_is_unsatisfiable_not_just_blocking():
    result = pdb(wl("db", 3, min_available="4"))
    assert found(result) == {("pdb-unsatisfiable", "db"): "blocker"}
    assert result["blocking"] == 1 and result["budgets"][0]["disruptions_allowed"] == 0


@pytest.mark.parametrize(("replicas", "budget", "expected"), [
    (4, {"max_unavailable": "2"}, {}),                                         # exactly half: fine
    (4, {"max_unavailable": "3"}, {("pdb-permissive", "w"): "minor"}),         # more than half
    (2, {"max_unavailable": "1"}, {}),                                         # one of two is not "more than half"
    (2, {"max_unavailable": "2"}, {("pdb-no-protection", "w"): "major"}),      # everything may go at once
    (3, {"min_available": "0"}, {("pdb-no-protection", "w"): "major"}),
    (3, {"max_unavailable": "3"}, {("pdb-no-protection", "w"): "major"}),     # everything may go at once
])
def test_pdb_permissive_and_unprotective_budgets(replicas, budget, expected):
    result = pdb(wl("w", replicas, **budget))
    assert found(result) == expected
    assert result["unsafe"] == len(expected)


def test_pdb_stuck_unhealthy_pods_depend_on_eviction_policy_and_boundary():
    stuck = pdb(wl("w", 3, ready_replicas=1, min_available="2"))
    assert found(stuck) == {("pdb-stuck-unhealthy", "w"): "major"}
    assert found(pdb(wl("w", 3, ready_replicas=1, min_available="2", unhealthy_pod_eviction_policy="AlwaysAllow"))) == {}
    assert found(pdb(wl("w", 3, ready_replicas=2, min_available="2"))) == {}          # ready == desired: not stuck
    assert found(pdb(wl("w", 3, ready_replicas=1, min_available="2", unhealthy_pod_eviction_policy="IfHealthyBudget"))) == {
        ("pdb-stuck-unhealthy", "w"): "major"}


def test_pdb_zero_replicas_with_a_budget_is_ignored():
    result = pdb(wl("idle", 0, min_available="1"))
    assert result["findings"] == [] and result["blocking"] == 0 and result["budgets"][0]["desired_healthy"] == 1


@pytest.mark.parametrize("budget", [{"min_available": "1", "max_unavailable": "1"}, {"min_available": "abc"}, {"max_unavailable": "101%"},
                                    {"min_available": "1.5"}, {"max_unavailable": "-1"}])
def test_pdb_invalid_budget_specs_name_the_field(budget):
    invalid("resil_pdb_check", {"workloads": [wl("w", 3, **budget)]}, "workloads")


def test_pdb_duplicate_workload_names_are_invalid():
    invalid("resil_pdb_check", {"workloads": [wl("w"), wl("w")]}, "workloads")


# --- resil_spread_check ---------------------------------------------------------------------------------------


def spread_payload(**over):
    base = {"workload": "web", "replicas": 6, "min_available_replicas": 4,
            "topology_domains": [{"key": ZONE, "count": 3}, {"key": HOST, "count": 9}],
            "spread_constraints": [{"topology_key": ZONE, "max_skew": 1, "when_unsatisfiable": "DoNotSchedule"}],
            "anti_affinity": []}
    return {**base, **over}


def test_spread_even_hard_zone_spread_survives_a_zone_loss():
    result = ok("resil_spread_check", spread_payload())
    assert result == {"state": "SUCCESS", "replicas": 6, "zone_count": 3, "worst_case_pods_per_zone": 2, "pods_surviving_zone_loss": 4,
                      "unschedulable_replicas": 0, "status": "pass", "findings": []}


@pytest.mark.parametrize(("replicas", "skew", "worst"), [(5, 1, 2), (6, 2, 3), (6, 3, 4), (2, 1, 1), (1, 1, 1), (9, 1, 3), (10, 1, 4)])
def test_spread_worst_case_pods_per_zone_follows_the_skew_formula(replicas, skew, worst):
    # floor((replicas + (zones - 1) * skew) / zones), never above replicas: 3 zones
    constraint = [{"topology_key": ZONE, "max_skew": skew, "when_unsatisfiable": "DoNotSchedule"}]
    result = ok("resil_spread_check", spread_payload(replicas=replicas, min_available_replicas=0, spread_constraints=constraint))
    assert result["worst_case_pods_per_zone"] == worst and result["pods_surviving_zone_loss"] == replicas - worst


def test_spread_loose_skew_breaches_the_minimum_after_a_zone_loss():
    constraint = [{"topology_key": ZONE, "max_skew": 2, "when_unsatisfiable": "DoNotSchedule"}]
    result = ok("resil_spread_check", spread_payload(spread_constraints=constraint))
    assert (result["worst_case_pods_per_zone"], result["pods_surviving_zone_loss"]) == (3, 3)
    assert found(result) == {("zone-loss-breaches-minimum", "web"): "major"} and result["status"] == "fail"


def test_spread_minimum_survivors_boundary_is_inclusive():
    result = ok("resil_spread_check", spread_payload(min_available_replicas=4))
    assert result["pods_surviving_zone_loss"] == 4 and result["findings"] == []
    assert rules(ok("resil_spread_check", spread_payload(min_available_replicas=5))) == {"zone-loss-breaches-minimum"}


def test_spread_without_any_rule_guarantees_nothing():
    result = ok("resil_spread_check", spread_payload(replicas=3, min_available_replicas=0, spread_constraints=[]))
    assert found(result) == {("zone-spread-missing", "web"): "major"}
    assert (result["worst_case_pods_per_zone"], result["pods_surviving_zone_loss"]) == (3, 0)


def test_spread_soft_rules_only_warn():
    soft = [{"topology_key": ZONE, "max_skew": 1, "when_unsatisfiable": "ScheduleAnyway"}]
    result = ok("resil_spread_check", spread_payload(min_available_replicas=0, spread_constraints=soft))
    assert found(result) == {("zone-spread-soft-only", "web"): "minor"} and result["status"] == "warn"
    preferred = [{"topology_key": ZONE, "mode": "preferred"}]
    result = ok("resil_spread_check", spread_payload(min_available_replicas=0, spread_constraints=[], anti_affinity=preferred))
    assert found(result) == {("zone-spread-soft-only", "web"): "minor"}


def test_spread_required_zone_anti_affinity_allows_one_pod_per_zone():
    required = [{"topology_key": ZONE, "mode": "required"}]
    result = ok("resil_spread_check", spread_payload(replicas=3, min_available_replicas=2, spread_constraints=[], anti_affinity=required))
    assert (result["worst_case_pods_per_zone"], result["pods_surviving_zone_loss"], result["unschedulable_replicas"]) == (1, 2, 0)
    assert result["findings"] == []


def test_spread_required_anti_affinity_beyond_domain_count_stays_pending():
    required = [{"topology_key": HOST, "mode": "required"}]
    domains = [{"key": ZONE, "count": 3}, {"key": HOST, "count": 3}]
    result = ok("resil_spread_check", spread_payload(replicas=5, min_available_replicas=0, topology_domains=domains, anti_affinity=required))
    assert result["unschedulable_replicas"] == 2 and found(result)[("unschedulable-replicas", "web")] == "blocker"
    result = ok("resil_spread_check", spread_payload(replicas=3, min_available_replicas=0, topology_domains=domains, anti_affinity=required))
    assert result["unschedulable_replicas"] == 0


def test_spread_min_domains_above_domain_count_caps_pods_per_domain():
    constraint = [{"topology_key": ZONE, "max_skew": 1, "when_unsatisfiable": "DoNotSchedule", "min_domains": 5}]
    result = ok("resil_spread_check", spread_payload(replicas=4, min_available_replicas=0, spread_constraints=constraint))
    assert result["unschedulable_replicas"] == 1 and result["worst_case_pods_per_zone"] == 1
    assert found(result) == {("unschedulable-replicas", "web"): "blocker"}
    fits = ok("resil_spread_check", spread_payload(replicas=3, min_available_replicas=0, spread_constraints=constraint))
    assert fits["unschedulable_replicas"] == 0 and fits["findings"] == []
    met = {**constraint[0], "min_domains": 3}
    assert ok("resil_spread_check", spread_payload(replicas=4, min_available_replicas=0, spread_constraints=[met]))["unschedulable_replicas"] == 0


def test_spread_single_zone_and_replicas_below_zone_count():
    single = ok("resil_spread_check", spread_payload(replicas=2, min_available_replicas=0, topology_domains=[{"key": ZONE, "count": 1}],
                                                     spread_constraints=[]))
    assert found(single) == {("single-zone", "web"): "major"}
    few = ok("resil_spread_check", spread_payload(replicas=2, min_available_replicas=1))
    assert found(few) == {("replicas-below-zone-count", "web"): "info"} and few["status"] == "pass"
    lone = ok("resil_spread_check", spread_payload(replicas=1, min_available_replicas=1))
    assert found(lone) == {("zone-loss-breaches-minimum", "web"): "major"}


def test_spread_deprecated_zone_label_is_flagged():
    domains = [{"key": LEGACY_ZONE, "count": 3}]
    constraint = [{"topology_key": LEGACY_ZONE, "max_skew": 1, "when_unsatisfiable": "DoNotSchedule"}]
    result = ok("resil_spread_check", spread_payload(topology_domains=domains, spread_constraints=constraint))
    assert found(result) == {("deprecated-zone-label", "web"): "minor"} and result["zone_count"] == 3


@pytest.mark.parametrize(("over", "field"), [
    ({"min_available_replicas": 7}, "min_available_replicas"),
    ({"topology_domains": [{"key": HOST, "count": 3}]}, "topology_domains"),
    ({"topology_domains": [{"key": ZONE, "count": 3}, {"key": ZONE, "count": 2}]}, "topology_domains"),
    ({"spread_constraints": [{"topology_key": "rack", "max_skew": 1, "when_unsatisfiable": "DoNotSchedule"}]}, "spread_constraints"),
    ({"spread_constraints": [{"topology_key": ZONE, "max_skew": 1, "when_unsatisfiable": "ScheduleAnyway", "min_domains": 2}]},
     "spread_constraints"),
    ({"anti_affinity": [{"topology_key": "rack", "mode": "required"}]}, "anti_affinity"),
])
def test_spread_invalid_inputs_name_the_field(over, field):
    invalid("resil_spread_check", spread_payload(**over), field)


# --- resil_probe_check ----------------------------------------------------------------------------------------


def http(path="/healthz", **fields):
    return {"kind": "http", "endpoint": path, **fields}


def probes(containers, serves=True, **over):
    return ok("resil_probe_check", {"workload": "web", "serves_traffic": serves, "containers": containers, **over})


def test_probe_well_configured_container_passes():
    result = probes([{"name": "app", "startup": http(period_seconds=5, failure_threshold=30), "liveness": http("/livez"),
                      "readiness": http("/readyz")}], expected_startup_seconds=40)
    assert result == {"state": "SUCCESS", "containers_checked": 1, "missing_readiness": 0, "probes_defined": 3, "status": "pass", "findings": []}


def test_probe_missing_readiness_only_matters_when_serving_traffic():
    served = probes([{"name": "app", "liveness": http()}])
    assert found(served) == {("readiness-missing", "web/app"): "major"} and served["missing_readiness"] == 1
    worker = probes([{"name": "app", "liveness": http()}], serves=False)
    assert worker["findings"] == [] and worker["missing_readiness"] == 1


def test_probe_without_any_liveness_is_only_informational():
    result = probes([{"name": "app", "readiness": http()}])
    assert found(result) == {("liveness-missing", "web/app"): "info"} and result["status"] == "pass"


@pytest.mark.parametrize(("failures", "period", "expected"), [(1, 5, True), (2, 5, False), (1, 9, True), (1, 10, False), (3, 3, True), (3, 4, False)])
def test_probe_liveness_window_boundary_is_ten_seconds(failures, period, expected):
    result = probes([{"name": "app", "liveness": http(failure_threshold=failures, period_seconds=period), "readiness": http("/r")}])
    assert ("liveness-too-aggressive" in rules(result)) is expected


def test_probe_default_liveness_window_is_thirty_seconds():
    assert rules(probes([{"name": "app", "liveness": http(), "readiness": http("/r")}])) == set()


@pytest.mark.parametrize(("expected", "rule", "severity"), [
    (45, "liveness-kills-slow-start", "blocker"),     # window 30 s < 45 s
    (30.1, "liveness-kills-slow-start", "blocker"),
    (30, "thin-startup-budget", "minor"),             # window == startup: not killed, but under 1.25x headroom
    (24.1, "thin-startup-budget", "minor"),
    (24, None, None),                                  # 24 x 1.25 == 30: exactly enough headroom
])
def test_probe_liveness_alone_against_measured_startup(expected, rule, severity):
    result = probes([{"name": "app", "liveness": http(), "readiness": http("/r")}], expected_startup_seconds=expected)
    assert found(result) == ({(rule, "web/app"): severity} if rule else {})


def test_probe_startup_probe_budget_includes_initial_delay_and_replaces_the_liveness_check():
    startup = http(period_seconds=5, failure_threshold=6)                      # 30 s
    tight = probes([{"name": "app", "startup": startup, "liveness": http(), "readiness": http("/r")}], expected_startup_seconds=31)
    assert found(tight) == {("startup-budget-too-small", "web/app"): "blocker"}
    equal = probes([{"name": "app", "startup": startup, "liveness": http(), "readiness": http("/r")}], expected_startup_seconds=30)
    assert found(equal) == {("thin-startup-budget", "web/app"): "minor"}
    delayed = http(initial_delay_seconds=10, period_seconds=5, failure_threshold=6)  # 40 s
    assert found(probes([{"name": "app", "startup": delayed, "liveness": http(), "readiness": http("/r")}], expected_startup_seconds=33)) == {
        ("thin-startup-budget", "web/app"): "minor"}
    assert probes([{"name": "app", "startup": delayed, "liveness": http(), "readiness": http("/r")}], expected_startup_seconds=32)["findings"] == []


def test_probe_readiness_only_container_is_never_killed_by_slow_start():
    result = probes([{"name": "app", "readiness": http()}], expected_startup_seconds=600)
    assert found(result) == {("liveness-missing", "web/app"): "info"}


def test_probe_liveness_equal_to_readiness_endpoint_is_flagged():
    same = probes([{"name": "app", "liveness": http("/health"), "readiness": http("/health")}])
    assert found(same) == {("liveness-equals-readiness", "web/app"): "minor"}
    assert probes([{"name": "app", "liveness": {"kind": "tcp", "endpoint": "/health"}, "readiness": http("/health")}])["findings"] == []
    assert probes([{"name": "app", "liveness": {"kind": "tcp"}, "readiness": {"kind": "tcp"}}])["findings"] == []   # no endpoint: nothing to compare


def test_probe_counts_and_orders_findings_across_containers():
    result = probes([{"name": "b", "liveness": http(failure_threshold=1, period_seconds=1)}, {"name": "a"}])
    assert result["containers_checked"] == 2 and result["missing_readiness"] == 2 and result["probes_defined"] == 1
    assert [(f["id"], f["subject"]) for f in result["findings"]] == [
        ("liveness-too-aggressive", "web/b"), ("readiness-missing", "web/a"), ("readiness-missing", "web/b"), ("liveness-missing", "web/a")]


@pytest.mark.parametrize("kind", ["startup", "liveness"])
def test_probe_success_threshold_above_one_is_refused_by_the_api_rules(kind):
    invalid("resil_probe_check", {"workload": "w", "serves_traffic": True, "containers": [{"name": "a", kind: http(success_threshold=2)}]},
            "containers")


def test_probe_readiness_may_use_success_threshold_above_one():
    assert probes([{"name": "a", "liveness": http(), "readiness": http("/r", success_threshold=3)}])["findings"] == []


def test_probe_duplicate_container_names_are_invalid():
    invalid("resil_probe_check", {"workload": "w", "serves_traffic": True, "containers": [{"name": "a"}, {"name": "a"}]}, "containers")


# --- resil_backup_coverage ------------------------------------------------------------------------------------

AS_OF = "2026-10-20T12:00:00+00:00"


def asset(name="db", criticality="critical", rpo=1, kind="database"):
    return {"name": name, "kind": kind, "criticality": criticality, "rpo_hours": rpo}


def job(name="db", interval=0.5, retention=30, last="2026-10-20T11:45:00+00:00", offsite=True, tested="2026-09-30T08:00:00+00:00"):
    entry = {"asset": name, "interval_hours": interval, "retention_days": retention, "offsite": offsite}
    if last:
        entry["last_success"] = last
    if tested:
        entry["last_restore_test"] = tested
    return entry


def backup(assets, jobs, **over):
    return ok("resil_backup_coverage", {"as_of": AS_OF, "min_retention_days": 30, "restore_test_max_age_days": 90, "assets": assets,
                                        "jobs": jobs, **over})


def test_backup_reports_coverage_and_protection_counts():
    result = backup([asset("etcd", kind="etcd"), asset("orders-db", rpo=24)], [job("etcd")])
    assert (result["assets_total"], result["assets_covered"], result["assets_protected"], result["coverage_percent"]) == (2, 1, 1, 50.0)
    assert found(result) == {("backup-missing", "orders-db"): "blocker"} and result["status"] == "fail"


def test_backup_missing_job_is_a_blocker_only_for_critical_assets():
    result = backup([asset("a", "critical"), asset("b", "important"), asset("c", "standard")], [])
    assert found(result) == {("backup-missing", "a"): "blocker", ("backup-missing", "b"): "major", ("backup-missing", "c"): "major"}
    assert result["coverage_percent"] == 0.0 and result["assets_protected"] == 0


@pytest.mark.parametrize(("last", "severity"), [
    ("2026-10-20T11:00:00+00:00", None),          # exactly one RPO old: not stale
    ("2026-10-20T10:59:59+00:00", "major"),
    ("2026-10-20T10:00:00+00:00", "major"),       # exactly twice the RPO: still major
    ("2026-10-20T09:59:59+00:00", "blocker"),
])
def test_backup_staleness_boundaries(last, severity):
    result = backup([asset("db", rpo=1)], [job("db", last=last)])
    assert found(result) == ({("backup-stale", "db"): severity} if severity else {})


def test_backup_stale_offset_timestamps_are_compared_in_absolute_time():
    result = backup([asset("db", rpo=1)], [job("db", last="2026-10-20T14:00:00+03:00")])   # 11:00Z: one hour old
    assert result["findings"] == []


@pytest.mark.parametrize(("interval", "flagged"), [(1, False), (1.01, True), (24, True), (0.5, False)])
def test_backup_schedule_must_fit_the_rpo(interval, flagged):
    result = backup([asset("db", rpo=1)], [job("db", interval=interval)])
    assert ("schedule-exceeds-rpo" in rules(result)) is flagged


def test_backup_job_that_never_succeeded():
    assert found(backup([asset("db", "critical")], [job("db", last=None)])) == {("backup-never-succeeded", "db"): "blocker"}
    assert found(backup([asset("db", "standard")], [job("db", last=None)])) == {("backup-never-succeeded", "db"): "major"}


@pytest.mark.parametrize(("retention", "flagged"), [(29, True), (30, False), (365, False)])
def test_backup_retention_boundary_is_inclusive(retention, flagged):
    result = backup([asset("db")], [job("db", retention=retention)])
    assert ("retention-too-short" in rules(result)) is flagged
    assert flagged is (result["status"] == "fail")


def test_backup_offsite_and_restore_test_severity_depends_on_criticality():
    crit = backup([asset("db", "critical")], [job("db", offsite=False, tested=None)])
    assert found(crit) == {("no-offsite-copy", "db"): "major", ("restore-untested", "db"): "major"}
    std = backup([asset("db", "standard")], [job("db", offsite=False, tested=None)])
    assert found(std) == {("no-offsite-copy", "db"): "minor", ("restore-untested", "db"): "minor"}
    assert std["status"] == "warn" and std["assets_protected"] == 1


@pytest.mark.parametrize(("tested", "flagged"), [("2026-07-22T12:00:00+00:00", False), ("2026-07-22T11:59:59+00:00", True)])
def test_backup_restore_test_age_boundary_is_ninety_days(tested, flagged):
    result = backup([asset("db")], [job("db", tested=tested)])
    assert found(result) == ({("restore-test-stale", "db"): "minor"} if flagged else {})


def test_backup_merges_several_jobs_of_one_asset():
    old = job("db", interval=24, retention=7, last="2026-10-18T00:00:00+00:00", offsite=False, tested=None)
    new = job("db", interval=0.5, retention=45, last="2026-10-20T11:50:00+00:00", offsite=True, tested="2026-10-01T00:00:00+00:00")
    assert backup([asset("db", rpo=1)], [old, new])["findings"] == []
    assert backup([asset("db", rpo=1)], [new, old])["findings"] == []


@pytest.mark.parametrize(("assets", "jobs", "over", "field"), [
    ([asset("db")], [job("other")], {}, "jobs"),
    ([asset("db")], [job("db", last="2026-10-20T12:00:01+00:00")], {}, "jobs"),
    ([asset("db")], [job("db", tested="2026-10-21T00:00:00+00:00")], {}, "jobs"),
    ([asset("db")], [job("db", last="2026-10-20T11:00:00.000")], {}, "jobs"),
    ([asset("db"), asset("db")], [], {}, "assets"),
    ([asset("db")], [], {"as_of": "2026-10-20T12:00:00.000"}, "as_of"),
    ([asset("db")], [], {"as_of": "yesterday at noon plus seven"}, "as_of"),
])
def test_backup_invalid_inputs_name_the_field(assets, jobs, over, field):
    invalid("resil_backup_coverage", {"as_of": AS_OF, "min_retention_days": 30, "restore_test_max_age_days": 90, "assets": assets,
                                      "jobs": jobs, **over}, field)


# --- resil_restore_rto ----------------------------------------------------------------------------------------


def rto(**over):
    base = {"data_size_gib": 60, "restore_throughput_mib_s": 1024, "parallel_streams": 1, "parallel_efficiency": 1, "replay_size_gib": 0,
            "fixed_overhead_minutes": 0, "safety_margin": 1, "target_rto_minutes": 10}
    return {**base, **over}


def test_rto_worked_example_with_parallel_streams_and_replay():
    result = ok("resil_restore_rto", {"data_size_gib": 500, "restore_throughput_mib_s": 100, "parallel_streams": 4, "parallel_efficiency": 0.8,
                                      "replay_size_gib": 20, "replay_rate_mib_s": 50, "fixed_overhead_minutes": 15, "safety_margin": 1.25,
                                      "target_rto_minutes": 120})
    assert result == {"state": "SUCCESS", "effective_throughput_mib_s": 340.0, "transfer_minutes": 25.1, "replay_minutes": 6.83,
                      "estimated_minutes": 46.92, "estimated_with_margin_minutes": 58.66, "headroom_minutes": 61.34,
                      "max_data_gib_for_target": 1477.67, "meets_target": True, "status": "pass", "findings": []}


def test_rto_one_minute_restore_hits_the_target_boundary_exactly():
    # 60 GiB at 1024 MiB/s = 60 s = 1 minute
    equal = ok("resil_restore_rto", rto(target_rto_minutes=1))
    assert (equal["estimated_minutes"], equal["meets_target"], equal["headroom_minutes"]) == (1.0, True, 0.0)
    assert found(equal) == {("thin-rto-headroom", "restore"): "minor"} and equal["status"] == "warn"
    over = ok("resil_restore_rto", rto(target_rto_minutes=0.99))
    assert over["meets_target"] is False and found(over) == {("rto-exceeds-target", "restore"): "blocker"}


def test_rto_margin_can_erode_a_passing_estimate():
    result = ok("resil_restore_rto", rto(safety_margin=2, target_rto_minutes=1.5))
    assert (result["estimated_minutes"], result["estimated_with_margin_minutes"], result["meets_target"]) == (1.0, 2.0, False)
    assert found(result) == {("rto-margin-eroded", "restore"): "major"} and result["headroom_minutes"] == -0.5


def test_rto_headroom_below_ten_percent_is_thin_and_exactly_ten_percent_is_not():
    # 540 GiB at 1024 MiB/s is exactly 9 minutes; a 10 minute target leaves exactly 10%
    assert found(ok("resil_restore_rto", rto(data_size_gib=540, target_rto_minutes=10))) == {}
    assert found(ok("resil_restore_rto", rto(data_size_gib=541, target_rto_minutes=10))) == {("thin-rto-headroom", "restore"): "minor"}


def test_rto_network_limit_caps_parallel_throughput():
    result = ok("resil_restore_rto", rto(restore_throughput_mib_s=100, parallel_streams=4, parallel_efficiency=1, network_limit_mib_s=250,
                                         data_size_gib=1, target_rto_minutes=60))
    assert result["effective_throughput_mib_s"] == 250.0 and found(result) == {("network-bound", "restore"): "info"}
    free = ok("resil_restore_rto", rto(restore_throughput_mib_s=100, parallel_streams=4, parallel_efficiency=1, network_limit_mib_s=400,
                                       data_size_gib=1, target_rto_minutes=60))
    assert free["effective_throughput_mib_s"] == 400.0 and free["findings"] == []


def test_rto_max_data_for_target_is_the_data_size_that_just_fits():
    result = ok("resil_restore_rto", rto(target_rto_minutes=10, safety_margin=1, fixed_overhead_minutes=4))
    assert result["max_data_gib_for_target"] == 360.0            # 6 min x 60 s x 1024 MiB/s / 1024
    fit = ok("resil_restore_rto", rto(data_size_gib=360, target_rto_minutes=10, fixed_overhead_minutes=4))
    assert (fit["estimated_minutes"], fit["meets_target"]) == (10.0, True)
    none = ok("resil_restore_rto", rto(target_rto_minutes=3, fixed_overhead_minutes=4))
    assert none["max_data_gib_for_target"] == 0.0 and none["meets_target"] is False


def test_rto_replay_needs_a_rate():
    invalid("resil_restore_rto", rto(replay_size_gib=5), "replay_rate_mib_s")
    result = ok("resil_restore_rto", rto(replay_size_gib=1.5, replay_rate_mib_s=1024 / 60, target_rto_minutes=100))
    assert result["replay_minutes"] == 1.5


# --- resil_chaos_spec_check -----------------------------------------------------------------------------------


def chaos(**over):
    base = {"name": "kill-cart", "environment": "staging", "start": "2026-10-21T10:00:00+00:00", "duration_minutes": 30,
            "max_duration_minutes": 60,
            "steady_state": [{"metric": "success_ratio", "comparison": "gte", "threshold": 0.999}],
            "abort_conditions": [{"metric": "success_ratio", "comparison": "lt", "threshold": 0.99}],
            "affected_instances": 1, "total_instances": 10, "max_blast_percent": 25, "customer_facing": False, "automatic_abort": True,
            "rollback_documented": True, "approval_recorded": True, "error_budget_gate_passed": True, "freeze_windows": []}
    return {**base, **over}


def prod(**over):
    return chaos(environment="production", **over)


def test_chaos_staging_experiment_passes_at_l2():
    result = ok("resil_chaos_spec_check", chaos())
    assert result == {"state": "SUCCESS", "window_end": "2026-10-21T10:30:00+00:00", "blast_radius_percent": 10.0, "required_autonomy": "L2",
                      "blockers": 0, "status": "pass", "findings": []}


def test_chaos_production_experiment_needs_l3_and_every_gate():
    result = ok("resil_chaos_spec_check", prod(customer_facing=True))
    assert result["required_autonomy"] == "L3" and found(result) == {("customer-impact", "kill-cart"): "info"} and result["status"] == "pass"
    bad = ok("resil_chaos_spec_check", prod(automatic_abort=False, approval_recorded=False, error_budget_gate_passed=False))
    assert found(bad) == {("manual-abort-only", "kill-cart"): "blocker", ("approval-missing", "kill-cart"): "blocker",
                          ("error-budget-gate-not-passed", "kill-cart"): "blocker"}
    assert bad["blockers"] == 3 and bad["status"] == "fail"


def test_chaos_staging_does_not_require_approval_budget_gate_or_automatic_abort():
    result = ok("resil_chaos_spec_check", chaos(automatic_abort=False, approval_recorded=False, error_budget_gate_passed=False))
    assert result["findings"] == [] and result["status"] == "pass"


def test_chaos_steady_state_and_abort_conditions_are_mandatory():
    result = ok("resil_chaos_spec_check", chaos(steady_state=[], abort_conditions=[]))
    assert found(result) == {("no-steady-state-hypothesis", "kill-cart"): "blocker", ("no-abort-conditions", "kill-cart"): "blocker"}
    untied = ok("resil_chaos_spec_check", chaos(abort_conditions=[{"metric": "cpu", "comparison": "gt", "threshold": 0.9}]))
    assert found(untied) == {("abort-not-tied-to-steady-state", "kill-cart"): "minor"} and untied["status"] == "warn"
    only_abort = ok("resil_chaos_spec_check", chaos(steady_state=[]))
    assert rules(only_abort) == {"no-steady-state-hypothesis"}


@pytest.mark.parametrize(("affected", "total", "percent", "flagged"), [(1, 4, 25.0, False), (3, 10, 30.0, True), (26, 100, 26.0, True), (25, 100, 25.0, False)])
def test_chaos_blast_radius_limit_boundary_and_severity_by_environment(affected, total, percent, flagged):
    staging = ok("resil_chaos_spec_check", chaos(affected_instances=affected, total_instances=total))
    assert staging["blast_radius_percent"] == percent
    assert found(staging) == ({("blast-radius-exceeds-limit", "kill-cart"): "major"} if flagged else {})
    production = ok("resil_chaos_spec_check", prod(affected_instances=affected, total_instances=total))
    assert found(production) == ({("blast-radius-exceeds-limit", "kill-cart"): "blocker"} if flagged else {})


@pytest.mark.parametrize(("duration", "flagged"), [(60, False), (61, True)])
def test_chaos_duration_limit_boundary(duration, flagged):
    result = ok("resil_chaos_spec_check", chaos(duration_minutes=duration))
    assert ("duration-exceeds-limit" in rules(result)) is flagged


@pytest.mark.parametrize(("w_start", "w_end", "overlaps"), [
    ("2026-10-21T10:30:00+00:00", "2026-10-21T11:00:00+00:00", False),   # starts exactly when the experiment ends
    ("2026-10-21T10:29:59+00:00", "2026-10-21T11:00:00+00:00", True),
    ("2026-10-21T09:00:00+00:00", "2026-10-21T10:00:00+00:00", False),   # ends exactly when the experiment starts
    ("2026-10-21T09:00:00+00:00", "2026-10-21T10:00:01+00:00", True),
    ("2026-10-21T10:10:00+00:00", "2026-10-21T10:20:00+00:00", True),    # inside
    ("2026-10-21T09:00:00+00:00", "2026-10-21T12:00:00+00:00", True),    # covers
    ("2026-10-21T13:30:00+03:00", "2026-10-21T14:00:00+03:00", False),   # 10:30Z-11:00Z: touches the experiment end in another offset
    ("2026-10-21T13:29:00+03:00", "2026-10-21T14:00:00+03:00", True),
])
def test_chaos_freeze_window_overlap_boundaries(w_start, w_end, overlaps):
    windows = [{"start": w_start, "end": w_end, "reason": "release freeze"}]
    staging = ok("resil_chaos_spec_check", chaos(freeze_windows=windows))
    assert found(staging) == ({("freeze-window", "kill-cart"): "major"} if overlaps else {})
    assert found(ok("resil_chaos_spec_check", prod(freeze_windows=windows))) == ({("freeze-window", "kill-cart"): "blocker"} if overlaps else {})


def test_chaos_missing_rollback_plan_is_major():
    assert found(ok("resil_chaos_spec_check", chaos(rollback_documented=False))) == {("no-rollback-plan", "kill-cart"): "major"}


def test_chaos_window_end_keeps_the_offset_and_crosses_midnight():
    result = ok("resil_chaos_spec_check", chaos(start="2026-10-21T23:45:00+02:00", duration_minutes=30))
    assert result["window_end"] == "2026-10-22T00:15:00+02:00"


@pytest.mark.parametrize(("over", "field"), [
    ({"affected_instances": 11}, "affected_instances"),
    ({"start": "2026-10-21T10:00:00.000"}, "start"),
    ({"freeze_windows": [{"start": "2026-10-22T00:00:00+00:00", "end": "2026-10-22T00:00:00+00:00", "reason": "empty"}]}, "freeze_windows"),
    ({"freeze_windows": [{"start": "2026-10-22T00:00:00+00:00", "end": "2026-10-21T00:00:00+00:00", "reason": "backwards"}]}, "freeze_windows"),
    ({"freeze_windows": [{"start": "2026-10-22T00:00:00.000", "end": "2026-10-23T00:00:00+00:00", "reason": "naive"}]}, "freeze_windows"),
])
def test_chaos_invalid_inputs_name_the_field(over, field):
    invalid("resil_chaos_spec_check", chaos(**over), field)


# --- resil_fmea_rpn -------------------------------------------------------------------------------------------


def mode(mid, s, o, d):
    return {"id": mid, "severity": s, "occurrence": o, "detection": d}


def fmea(threshold, *modes):
    return ok("resil_fmea_rpn", {"rpn_threshold": threshold, "modes": list(modes)})


def test_fmea_ranks_by_rpn_and_reports_pareto_and_action():
    result = fmea(100, mode("zone-outage", 9, 3, 4), mode("cert-expiry", 7, 4, 6), mode("pod-oom", 4, 5, 2))
    assert [(m["id"], m["rpn"], m["action_required"]) for m in result["ranked"]] == [("cert-expiry", 168, True), ("zone-outage", 108, True), ("pod-oom", 40, False)]
    assert (result["modes_total"], result["requiring_action"], result["max_rpn"], result["total_rpn"], result["pareto_count"]) == (3, 2, 168, 316, 2)
    assert found(result) == {("rpn-over-threshold", "cert-expiry"): "major", ("rpn-over-threshold", "zone-outage"): "major"}
    assert result["status"] == "fail"


@pytest.mark.parametrize(("rpn_factors", "severity", "rule", "action"), [
    ((10, 10, 2), "blocker", "rpn-over-threshold", True),      # 200 == 2 x threshold
    ((10, 10, 1), "major", "rpn-over-threshold", True),        # 100 == threshold
    ((7, 7, 2), "minor", "rpn-approaching", False),            # 98: >= threshold / 2 and below the threshold
    ((5, 5, 2), "minor", "rpn-approaching", False),            # 50 == threshold / 2
    ((7, 7, 1), None, None, False),                            # 49 < threshold / 2
])
def test_fmea_threshold_boundaries(rpn_factors, severity, rule, action):
    result = fmea(100, mode("m", *rpn_factors))
    assert result["ranked"][0]["action_required"] is action
    assert found(result) == ({(rule, "m"): severity} if severity else {})


def test_fmea_severity_nine_and_ten_always_need_action():
    result = fmea(100, mode("catastrophic", 9, 1, 1), mode("annoying", 8, 1, 1))
    assert found(result) == {("critical-severity", "catastrophic"): "major"}
    assert [m["action_required"] for m in result["ranked"]] == [True, False]


def test_fmea_ties_sort_by_severity_then_id():
    result = fmea(1000, mode("b", 2, 6, 5), mode("a", 6, 2, 5), mode("c", 5, 4, 3), mode("d", 6, 5, 2))
    assert [(m["id"], m["rpn"]) for m in result["ranked"]] == [("a", 60), ("d", 60), ("c", 60), ("b", 60)]


@pytest.mark.parametrize(("rpns", "expected"), [([100], 1), ([80, 20], 1), ([70, 30], 2), ([50, 30, 10, 10], 2), ([25, 25, 25, 25], 4)])
def test_fmea_pareto_count_reaches_eighty_percent_inclusive(rpns, expected):
    factors = {100: (10, 10, 1), 80: (10, 8, 1), 70: (10, 7, 1), 50: (10, 5, 1), 30: (10, 3, 1), 25: (5, 5, 1), 20: (10, 2, 1), 10: (10, 1, 1)}
    modes = [mode(f"m{index}", *factors[rpn]) for index, rpn in enumerate(rpns)]
    assert fmea(1000, *modes)["pareto_count"] == expected


def test_fmea_duplicate_ids_are_invalid():
    invalid("resil_fmea_rpn", {"rpn_threshold": 100, "modes": [mode("a", 1, 1, 1), mode("a", 2, 2, 2)]}, "modes")


# --- resil_chaos_budget_gate ----------------------------------------------------------------------------------


def gate(**over):
    base = {"environment": "production", "slo_target": 0.999, "window_days": 30, "budget_remaining_fraction": 0.8, "current_burn_rate": 0.4,
            "open_incident": False, "experiment_duration_minutes": 30, "expected_error_ratio": 0.05, "reserve_fraction": 0.3,
            "max_cost_fraction": 0.1}
    return {**base, **over}


def test_gate_worked_example_goes():
    # budget minutes = 43200 x 0.001 = 43.2; cost = 0.05 x 30 / 43.2
    assert ok("resil_chaos_budget_gate", gate()) == {"state": "SUCCESS", "decision": "go", "cost_fraction": 0.034722, "remaining_after_fraction": 0.765278,
                                                     "max_duration_minutes": 86.4, "status": "pass", "findings": []}


def easy(**over):
    """SLO 0.9 over one day: the budget is 144 minutes of full outage, so a 10% error ratio for 144 minutes costs exactly 0.1."""
    base = {"slo_target": 0.9, "window_days": 1, "expected_error_ratio": 0.1, "experiment_duration_minutes": 144,
            "budget_remaining_fraction": 0.5, "reserve_fraction": 0.4, "max_cost_fraction": 0.1}
    return gate(**{**base, **over})


def test_gate_cost_and_reserve_boundaries_are_inclusive():
    result = ok("resil_chaos_budget_gate", easy())
    assert (result["decision"], result["cost_fraction"], result["remaining_after_fraction"]) == ("go", 0.1, 0.4)
    assert result["max_duration_minutes"] == 144.0
    costly = ok("resil_chaos_budget_gate", easy(experiment_duration_minutes=145))
    assert found(costly) == {("experiment-too-expensive", "production"): "blocker", ("reserve-breached", "production"): "blocker"}
    assert costly["decision"] == "no-go" and costly["status"] == "fail"
    reserve = ok("resil_chaos_budget_gate", easy(reserve_fraction=0.41))
    assert found(reserve) == {("reserve-breached", "production"): "blocker"} and reserve["max_duration_minutes"] == 129.6


@pytest.mark.parametrize(("burn", "decision"), [(0.99, "go"), (1, "no-go"), (3.5, "no-go")])
def test_gate_burn_rate_at_or_above_one_blocks(burn, decision):
    result = ok("resil_chaos_budget_gate", gate(current_burn_rate=burn))
    assert result["decision"] == decision
    assert ("burn-rate-too-high" in rules(result)) is (decision == "no-go")


@pytest.mark.parametrize(("remaining", "expected"), [(0.0, {"budget-exhausted", "reserve-breached"}), (-0.2, {"budget-exhausted", "reserve-breached"}),
                                                     (0.01, {"reserve-breached"})])
def test_gate_exhausted_budget_blocks_and_allows_no_duration(remaining, expected):
    result = ok("resil_chaos_budget_gate", gate(budget_remaining_fraction=remaining))
    assert rules(result) == expected and result["decision"] == "no-go" and result["max_duration_minutes"] == 0.0


def test_gate_open_incident_blocks_production():
    result = ok("resil_chaos_budget_gate", gate(open_incident=True))
    assert found(result) == {("open-incident", "production"): "blocker"} and result["decision"] == "no-go"


def test_gate_staging_is_never_gated():
    result = ok("resil_chaos_budget_gate", gate(environment="staging", open_incident=True, budget_remaining_fraction=0, current_burn_rate=9))
    assert result["decision"] == "go" and result["status"] == "pass" and found(result) == {("staging-not-gated", "staging"): "info"}


def test_gate_max_duration_is_capped_by_the_per_experiment_cost():
    # remaining - reserve = 0.5 but max_cost 0.1 binds: 0.1 x 43.2 / 0.05 = 86.4 minutes
    assert ok("resil_chaos_budget_gate", gate())["max_duration_minutes"] == 86.4
    # reserve binds: spendable 0.05 -> 43.2 minutes
    assert ok("resil_chaos_budget_gate", gate(reserve_fraction=0.75))["max_duration_minutes"] == 43.2


# --- resil_shutdown_check -------------------------------------------------------------------------------------


def shutdown(**over):
    base = {"workload": "web", "serves_traffic": True, "termination_grace_period_seconds": 60, "prestop_seconds": 10, "app_drain_seconds": 30,
            "handles_sigterm": True, "keeps_serving_after_sigterm": False, "endpoint_propagation_seconds": 5}
    return {**base, **over}


def test_shutdown_worked_example():
    assert ok("resil_shutdown_check", shutdown()) == {"state": "SUCCESS", "required_seconds": 40, "margin_seconds": 20,
                                                      "recommended_grace_seconds": 45, "status": "pass", "findings": []}


@pytest.mark.parametrize(("grace", "rule", "severity"), [(39, "sigkill-before-drain", "blocker"), (40, "thin-margin", "minor"), (44, "thin-margin", "minor"),
                                                         (45, None, None), (46, None, None)])
def test_shutdown_grace_versus_required_time_boundaries(grace, rule, severity):
    result = ok("resil_shutdown_check", shutdown(termination_grace_period_seconds=grace))
    assert found(result) == ({(rule, "web"): severity} if rule else {})
    assert result["margin_seconds"] == grace - 40


def test_shutdown_zero_grace_is_its_own_blocker():
    result = ok("resil_shutdown_check", shutdown(termination_grace_period_seconds=0))
    assert found(result) == {("grace-zero", "web"): "blocker"} and result["margin_seconds"] == -40


@pytest.mark.parametrize(("serves", "keeps", "prestop", "flagged"), [
    (True, False, 4, True), (True, False, 5, False), (True, True, 0, False), (False, False, 0, False)])
def test_shutdown_endpoint_propagation_race(serves, keeps, prestop, flagged):
    result = ok("resil_shutdown_check", shutdown(serves_traffic=serves, keeps_serving_after_sigterm=keeps, prestop_seconds=prestop,
                                                 app_drain_seconds=0, termination_grace_period_seconds=30))
    assert ("traffic-after-sigterm" in rules(result)) is flagged
    if flagged:
        assert found(result)[("traffic-after-sigterm", "web")] == "major"


def test_shutdown_recommended_grace_adds_max_of_five_seconds_and_ten_percent():
    assert ok("resil_shutdown_check", shutdown(prestop_seconds=0, app_drain_seconds=20, endpoint_propagation_seconds=10))["recommended_grace_seconds"] == 35
    assert ok("resil_shutdown_check", shutdown(prestop_seconds=0, app_drain_seconds=100, serves_traffic=False,
                                               termination_grace_period_seconds=200))["recommended_grace_seconds"] == 110
    assert ok("resil_shutdown_check", shutdown(prestop_seconds=0, app_drain_seconds=45, serves_traffic=False))["recommended_grace_seconds"] == 50


def test_shutdown_ignored_sigterm_severity_depends_on_inflight_work():
    assert found(ok("resil_shutdown_check", shutdown(handles_sigterm=False))) == {("sigterm-ignored", "web"): "major"}
    quiet = ok("resil_shutdown_check", shutdown(handles_sigterm=False, app_drain_seconds=0, prestop_seconds=10))
    assert found(quiet) == {("sigterm-ignored", "web"): "minor"}


@pytest.mark.parametrize(("timeout", "flagged"), [(59, True), (60, False), (3600, False)])
def test_shutdown_grace_must_not_exceed_node_drain_timeout(timeout, flagged):
    result = ok("resil_shutdown_check", shutdown(node_drain_timeout_seconds=timeout))
    assert ("grace-exceeds-drain-timeout" in rules(result)) is flagged


# --- resil_drill_cadence --------------------------------------------------------------------------------------


def req(scenario="region-failover", days=180, evidence=True, **targets):
    return {"scenario": scenario, "max_interval_days": days, "require_evidence": evidence, **targets}


def drill(scenario="region-failover", at="2026-08-01T09:00:00+00:00", outcome="passed", evidence="drill-1", **measured):
    return {"scenario": scenario, "performed_at": at, "outcome": outcome, "evidence_ref": evidence, **measured}


def cadence(requirements, drills, **over):
    return ok("resil_drill_cadence", {"as_of": AS_OF, "requirements": requirements, "drills": drills, **over})


def test_drill_cadence_worked_example():
    result = cadence([req(target_rto_minutes=60)], [drill(measured_rto_minutes=42)])
    assert result == {"state": "SUCCESS", "scenarios_total": 1, "compliant": 1, "overdue": 0, "never_drilled": 0, "status": "pass", "findings": [],
                      "schedule": [{"scenario": "region-failover", "state": "compliant", "last_passed_at": "2026-08-01T09:00:00+00:00",
                                    "due_at": "2027-01-28T09:00:00+00:00", "days_until_due": 99.88}]}


@pytest.mark.parametrize(("days", "severity"), [(81, None), (80, "major"), (41, "major"), (40, "blocker"), (39, "blocker")])
def test_drill_overdue_boundaries(days, severity):
    # last drill 80 days 3 hours before as_of
    result = cadence([req(days=days)], [drill()])
    assert found(result) == ({("drill-overdue", "region-failover"): severity} if severity else {})
    assert result["overdue"] == (1 if severity else 0)


def test_drill_exactly_on_the_interval_is_on_time_and_exactly_double_is_major():
    # 2026-08-01T12:00Z is exactly 80 days before AS_OF
    on_time = cadence([req(days=80)], [drill(at="2026-08-01T12:00:00+00:00")])
    assert on_time["findings"] == [] and on_time["schedule"][0]["days_until_due"] == 0.0
    double = cadence([req(days=40)], [drill(at="2026-08-01T12:00:00+00:00")])
    assert found(double) == {("drill-overdue", "region-failover"): "major"}
    assert double["schedule"][0]["state"] == "overdue"


def test_drill_never_performed_and_only_failed_drills_are_blockers():
    never = cadence([req()], [])
    assert found(never) == {("never-drilled", "region-failover"): "blocker"} and never["never_drilled"] == 1
    assert never["schedule"] == [{"scenario": "region-failover", "state": "never-drilled", "last_passed_at": "", "due_at": "", "days_until_due": 0}]
    failed = cadence([req()], [drill(outcome="failed"), drill(outcome="partial", at="2026-09-01T00:00:00+00:00")])
    assert found(failed) == {("no-passing-drill", "region-failover"): "blocker"} and failed["never_drilled"] == 1


def test_drill_failed_after_the_last_pass_is_a_regression_but_cadence_still_counts_the_pass():
    result = cadence([req()], [drill(), drill(outcome="failed", at="2026-10-01T00:00:00+00:00")])
    assert found(result) == {("latest-drill-failed", "region-failover"): "major"} and result["overdue"] == 0
    older_failure = cadence([req()], [drill(outcome="failed", at="2026-07-01T00:00:00+00:00"), drill()])
    assert older_failure["findings"] == []


def test_drill_evidence_is_required_to_count():
    result = cadence([req()], [drill(evidence="")])
    assert found(result) == {("no-passing-drill", "region-failover"): "blocker", ("drill-without-evidence", "region-failover"): "major"}
    relaxed = cadence([req(evidence=False)], [drill(evidence="")])
    assert relaxed["findings"] == []
    mixed = cadence([req()], [drill(at="2026-06-01T00:00:00+00:00"), drill(at="2026-10-01T00:00:00+00:00", evidence="")])
    assert found(mixed) == {("drill-without-evidence", "region-failover"): "major"}
    assert mixed["schedule"][0]["last_passed_at"] == "2026-06-01T00:00:00+00:00"


@pytest.mark.parametrize(("measured", "expected"), [(60, None), (60.5, "major"), (61, "major"), (0, None)])
def test_drill_rto_and_rpo_targets_are_inclusive(measured, expected):
    rto_result = cadence([req(target_rto_minutes=60)], [drill(measured_rto_minutes=measured)])
    assert found(rto_result) == ({("rto-target-missed", "region-failover"): "major"} if expected else {})
    rpo_result = cadence([req(target_rpo_minutes=60)], [drill(measured_rpo_minutes=measured)])
    assert found(rpo_result) == ({("rpo-target-missed", "region-failover"): "major"} if expected else {})


def test_drill_unmeasured_targets_are_minor():
    result = cadence([req(target_rto_minutes=60, target_rpo_minutes=5)], [drill()])
    assert found(result) == {("rto-not-measured", "region-failover"): "minor", ("rpo-not-measured", "region-failover"): "minor"}
    assert result["status"] == "warn" and result["compliant"] == 1


def test_drill_for_an_unknown_scenario_is_reported_so_typos_show():
    result = cadence([req()], [drill(), drill("region-failovr")])
    assert found(result) == {("drill-without-requirement", "region-failovr"): "info"}


def test_drill_schedule_is_sorted_and_counts_each_scenario():
    result = cadence([req("b", 365), req("a", 30, evidence=False)], [drill("b"), drill("a", at="2026-10-19T00:00:00+00:00", evidence="")])
    assert [s["scenario"] for s in result["schedule"]] == ["a", "b"]
    assert (result["scenarios_total"], result["compliant"]) == (2, 2)
    assert result["schedule"][0]["due_at"] == "2026-11-18T00:00:00+00:00"


@pytest.mark.parametrize(("requirements", "drills", "over", "field"), [
    ([req()], [drill(at="2026-10-20T12:00:01+00:00")], {}, "drills"),
    ([req()], [drill(at="2026-10-01T00:00:00.000")], {}, "drills"),
    ([req(), req()], [], {}, "requirements"),
    ([req()], [], {"as_of": "2026-10-20T12:00:00.000"}, "as_of"),
])
def test_drill_invalid_inputs_name_the_field(requirements, drills, over, field):
    invalid("resil_drill_cadence", {"as_of": AS_OF, "requirements": requirements, "drills": drills, **over}, field)


# --- resil_load_shed_check ------------------------------------------------------------------------------------


def klass(name, priority, rps, ms, maxc, reserved=0, depth=10, bounded=True, sheddable=True):
    return {"name": name, "priority": priority, "expected_rps": rps, "p99_service_ms": ms, "max_concurrency": maxc,
            "reserved_concurrency": reserved, "queue_depth": depth, "queue_bounded": bounded, "sheddable": sheddable}


def shed(classes, capacity=100, timeout=2000):
    return ok("resil_load_shed_check", {"capacity_concurrency": capacity, "client_timeout_ms": timeout, "classes": classes})


def checkout(**over):
    return klass("checkout", 1, 200, 100, 60, 30, 10, True, False) | over


def test_load_shed_worked_example_admits_everything():
    result = shed([klass("search", 2, 300, 200, 80, 0, 20), checkout()])
    assert result == {"state": "SUCCESS", "total_needed_concurrency": 80, "capacity_concurrency": 100, "overloaded": False, "status": "pass", "findings": [],
                      "shed_plan": [{"class": "checkout", "needed_concurrency": 20, "admitted_concurrency": 20, "shed_percent": 0.0},
                                    {"class": "search", "needed_concurrency": 60, "admitted_concurrency": 60, "shed_percent": 0.0}]}


def test_load_shed_overload_sheds_the_lowest_priority_first():
    result = shed([klass("search", 2, 300, 200, 80), checkout()], capacity=50)
    assert [(p["class"], p["admitted_concurrency"], p["shed_percent"]) for p in result["shed_plan"]] == [("checkout", 20, 0.0), ("search", 30, 50.0)]
    assert result["overloaded"] is True and found(result) == {("shed-at-nominal-load", "search"): "minor"} and result["status"] == "warn"


def test_load_shed_a_non_sheddable_class_that_does_not_fit_is_a_blocker():
    result = shed([checkout()], capacity=10)
    assert result["shed_plan"][0]["shed_percent"] == 50.0
    assert found(result)[("non-sheddable-class-starved", "checkout")] == "blocker"


def test_load_shed_reservations_are_served_before_extra_demand_of_higher_priority():
    # capacity 40: search reserves 30 and gets them before checkout's non-reserved remainder
    result = shed([klass("search", 2, 300, 100, 30, 30, 5), klass("checkout", 1, 300, 100, 60, 0, 5, True, False)], capacity=40)
    assert {p["class"]: p["admitted_concurrency"] for p in result["shed_plan"]} == {"checkout": 10, "search": 30}
    assert {"non-sheddable-class-starved", "critical-class-unreserved"} <= rules(result)


def test_load_shed_demand_is_capped_by_the_class_concurrency_limit():
    result = shed([klass("bulk", 1, 100, 1000, 40, 0, 0)], capacity=1000)
    assert result["shed_plan"][0] == {"class": "bulk", "needed_concurrency": 100, "admitted_concurrency": 40, "shed_percent": 60.0}


def test_load_shed_littles_law_demand_rounds_up_and_zero_traffic_needs_nothing():
    result = shed([klass("a", 1, 3, 500, 10), klass("b", 2, 0, 100, 10)])
    assert [(p["class"], p["needed_concurrency"]) for p in result["shed_plan"]] == [("a", 2), ("b", 0)]
    assert result["shed_plan"][1]["shed_percent"] == 0.0


def test_load_shed_reservations_above_capacity_are_a_blocker():
    result = shed([klass("a", 1, 10, 100, 30, 30), klass("b", 2, 10, 100, 30, 30)], capacity=50)
    assert found(result)[("reservations-exceed-capacity", "capacity")] == "blocker"
    assert not found(shed([klass("a", 1, 10, 100, 30, 30), klass("b", 2, 10, 100, 30, 20)], capacity=50)).get(("reservations-exceed-capacity", "capacity"))


def test_load_shed_unbounded_queue_is_a_blocker_and_bounded_queue_wait_is_compared_to_the_timeout():
    assert found(shed([klass("a", 1, 10, 100, 10, 1, 0, False), klass("b", 2, 1, 1, 1)])) == {("unbounded-queue", "a"): "blocker"}
    # wait = depth x p99 / max_concurrency = 190 x 100 / 10 = 1900 ms, plus the 100 ms of service: exactly the 2000 ms timeout
    assert shed([klass("a", 1, 1, 100, 10, 1, 190), klass("b", 2, 1, 1, 1)], timeout=2000)["findings"] == []
    slow = shed([klass("a", 1, 1, 100, 10, 1, 191), klass("b", 2, 1, 1, 1)], timeout=2000)
    assert found(slow) == {("queue-wait-exceeds-timeout", "a"): "major"}


def test_load_shed_a_single_priority_cannot_shed_selectively():
    result = shed([klass("a", 1, 1, 100, 10, 1), klass("b", 1, 1, 100, 10, 1)])
    assert found(result) == {("no-priority-differentiation", "classes"): "minor"}


@pytest.mark.parametrize("classes", [
    [klass("a", 1, 1, 100, 5, 6), klass("b", 2, 1, 1, 1)],
    [klass("a", 1, 1, 100, 5), klass("a", 2, 1, 1, 1)],
])
def test_load_shed_invalid_inputs_name_the_field(classes):
    invalid("resil_load_shed_check", {"capacity_concurrency": 10, "client_timeout_ms": 1000, "classes": classes}, "classes")


# --- resil_quorum_safety --------------------------------------------------------------------------------------


def quorum(**over):
    base = {"workload": "etcd", "replicas": 5, "members_per_zone": [2, 2, 1], "unavailable_members": 0, "max_unavailable": "1"}
    return ok("resil_quorum_safety", {**base, **over})


def test_quorum_five_members_over_three_zones_with_a_one_pod_budget_is_safe():
    assert quorum() == {"state": "SUCCESS", "quorum": 3, "fault_tolerance": 2, "zones_occupied": 3, "worst_zone_members": 2, "survives_zone_loss": True,
                        "max_members_per_zone": 2, "min_zones_required": 3, "pdb_disruptions_allowed": 1, "status": "pass", "findings": []}


@pytest.mark.parametrize(("replicas", "quorum_size", "tolerance"), [(1, 1, 0), (2, 2, 0), (3, 2, 1), (4, 3, 1), (5, 3, 2), (6, 4, 2), (7, 4, 3)])
def test_quorum_majority_and_fault_tolerance(replicas, quorum_size, tolerance):
    result = quorum(replicas=replicas, members_per_zone=[replicas], max_unavailable="1")
    assert (result["quorum"], result["fault_tolerance"]) == (quorum_size, tolerance)


def test_quorum_without_a_budget_is_a_major_finding_and_reports_minus_one():
    result = ok("resil_quorum_safety", {"workload": "etcd", "replicas": 3, "members_per_zone": [1, 1, 1], "unavailable_members": 0})
    assert result["pdb_disruptions_allowed"] == -1 and found(result) == {("pdb-missing", "etcd"): "major"}
    single = ok("resil_quorum_safety", {"workload": "etcd", "replicas": 1, "members_per_zone": [1], "unavailable_members": 0})
    assert rules(single) == {"no-fault-tolerance", "zone-loss-breaks-quorum"}


@pytest.mark.parametrize(("budget", "allowed", "expected"), [
    ({"max_unavailable": "1"}, 1, {}),
    ({"max_unavailable": "2"}, 2, {("pdb-allows-quorum-loss", "etcd"): "blocker"}),
    ({"min_available": "2"}, 1, {}),
    ({"min_available": "1"}, 2, {("pdb-allows-quorum-loss", "etcd"): "blocker"}),
    ({"min_available": "3"}, 0, {("pdb-blocks-eviction", "etcd"): "major"}),
    ({"min_available": "5"}, 0, {("pdb-unsatisfiable", "etcd"): "blocker"}),
    ({"max_unavailable": "33%"}, 1, {}),
])
def test_quorum_budget_is_compared_with_the_fault_tolerance(budget, allowed, expected):
    result = ok("resil_quorum_safety", {"workload": "etcd", "replicas": 3, "members_per_zone": [1, 1, 1], "unavailable_members": 0, **budget})
    assert result["pdb_disruptions_allowed"] == allowed and found(result) == expected


def test_quorum_zone_loss_breaks_a_lopsided_placement_and_zone_requirements_are_reported():
    result = quorum(replicas=3, members_per_zone=[2, 1, 0], max_unavailable="1")
    assert result["survives_zone_loss"] is False and result["zones_occupied"] == 2 and result["worst_zone_members"] == 2
    assert found(result) == {("zone-loss-breaks-quorum", "etcd"): "major"}
    two = quorum(replicas=3, members_per_zone=[2, 1], max_unavailable="1")
    assert two["survives_zone_loss"] is False
    assert (two["max_members_per_zone"], two["min_zones_required"]) == (1, 3)
    seven = quorum(replicas=7, members_per_zone=[3, 3, 1], max_unavailable="1")
    assert (seven["max_members_per_zone"], seven["min_zones_required"], seven["survives_zone_loss"]) == (3, 3, True)


def test_quorum_even_member_counts_add_no_tolerance_unless_the_quorum_is_overridden():
    result = quorum(replicas=4, members_per_zone=[1, 1, 1, 1], max_unavailable="1")
    assert found(result) == {("even-member-count", "etcd"): "minor"} and result["fault_tolerance"] == 1
    custom = quorum(replicas=4, members_per_zone=[1, 1, 1, 1], max_unavailable="1", quorum_size=3)
    assert custom["fault_tolerance"] == 1 and found(custom) == {}                 # a majority override is allowed


def test_sub_majority_quorum_blocks_consensus_stores_and_is_allowed_when_consensus_is_false():
    sub = quorum(replicas=4, members_per_zone=[1, 1, 1, 1], max_unavailable="1", quorum_size=2)
    assert sub["fault_tolerance"] == 2 and found(sub) == {("quorum-below-majority", "etcd"): "blocker"} and sub["status"] == "fail"
    kafka = quorum(workload="kafka", replicas=4, members_per_zone=[1, 1, 1, 1], max_unavailable="1", quorum_size=2, consensus=False)
    assert kafka["fault_tolerance"] == 2 and found(kafka) == {}                   # min.insync.replicas: non-consensus, no finding


def test_kafka_in_the_workload_name_does_not_exempt_a_consensus_quorum():
    named = quorum(workload="kafka-metadata-etcd", replicas=4, members_per_zone=[1, 1, 1, 1], max_unavailable="1", quorum_size=2)
    assert found(named) == {("quorum-below-majority", "kafka-metadata-etcd"): "blocker"} and named["status"] == "fail"


def test_etcd_named_quorum_is_allowed_only_when_consensus_is_explicitly_false():
    off = quorum(workload="etcd", replicas=4, members_per_zone=[1, 1, 1, 1], max_unavailable="1", quorum_size=2, consensus=False)
    assert found(off) == {} and off["fault_tolerance"] == 2


def test_member_counts_are_singular_for_one_member_in_findings():
    one = quorum(replicas=3, members_per_zone=[1, 1, 1], unavailable_members=1, max_unavailable="1")
    assert found(one) == {("no-failure-headroom", "etcd"): "major"}
    assert "1 member down: one more loss breaks quorum" in {f["summary"] for f in one["findings"]}
    solo = quorum(replicas=1, members_per_zone=[1], max_unavailable="1")
    summaries = {f["summary"] for f in solo["findings"]}
    assert "1 member with quorum 1 tolerates no failure" in summaries
    assert "the fullest zone holds 1 of 1 member; losing it leaves 0 < quorum 1" in summaries


def test_quorum_no_tolerance_cases():
    two = ok("resil_quorum_safety", {"workload": "zk", "replicas": 2, "members_per_zone": [1, 1], "unavailable_members": 0, "max_unavailable": "1"})
    assert two["min_zones_required"] == -1 and {"no-fault-tolerance", "even-member-count", "pdb-allows-quorum-loss"} <= rules(two)


@pytest.mark.parametrize(("down", "expected"), [(0, {}), (1, {}), (2, {("no-failure-headroom", "etcd"): "major"}), (3, {("quorum-lost", "etcd"): "blocker"})])
def test_quorum_current_failures_against_the_tolerance(down, expected):
    result = quorum(unavailable_members=down)
    assert {k: v for k, v in found(result).items() if k[0] in ("no-failure-headroom", "quorum-lost")} == expected


@pytest.mark.parametrize(("over", "field"), [
    ({"members_per_zone": [2, 2]}, "members_per_zone"),
    ({"quorum_size": 6}, "quorum_size"),
    ({"unavailable_members": 6}, "unavailable_members"),
    ({"min_available": "2", "max_unavailable": "1"}, "min_available"),
    ({"max_unavailable": "x"}, "max_unavailable"),
])
def test_quorum_invalid_inputs_name_the_field(over, field):
    base = {"workload": "etcd", "replicas": 5, "members_per_zone": [2, 2, 1], "unavailable_members": 0}
    invalid("resil_quorum_safety", {**base, **over}, field)


# --- independent review regressions ---------------------------------------------------------------------------


def test_chaos_window_that_leaves_the_calendar_is_invalid_input_not_a_handler_crash():
    # start + duration overflows datetime (year 9999): used to surface as DEPENDENCY_UNAVAILABLE "handler failed"
    invalid("resil_chaos_spec_check", chaos(start="9999-12-31T23:59:59+00:00", duration_minutes=2), "start")


def test_drill_due_date_that_leaves_the_calendar_is_invalid_input_not_a_handler_crash():
    payload = {"as_of": "9999-12-31T23:59:59+00:00", "requirements": [req(days=3650)], "drills": [drill(at="9999-12-30T00:00:00+00:00")]}
    invalid("resil_drill_cadence", payload, "drills")


def test_pdb_single_replica_with_a_budget_that_allows_its_eviction_is_still_a_single_replica():
    # Kubernetes docs (configure-pdb, example 4): one replica with maxUnavailable 30% is still allowed to be disrupted, so the
    # budget protects nothing and the workload stays a single point of failure.
    for budget in ({"max_unavailable": "1"}, {"max_unavailable": "30%"}, {"min_available": "0"}):
        result = pdb(wl("w", 1, **budget))
        assert found(result) == {("single-replica", "w"): "minor"}, budget
        assert result["unsafe"] == 0 and result["blocking"] == 0
    # a budget that refuses the eviction is reported as the blocking budget it is, not as a single replica on top
    assert found(pdb(wl("w", 1, min_available="1"))) == {("pdb-blocks-eviction", "w"): "major"}


def test_load_shed_full_queue_latency_includes_the_service_time_of_the_last_request():
    # a request that joins a full queue waits depth x p99 / max_concurrency and then runs for p99: 1900 + 100 = 2000 ms fits a 2000 ms timeout
    assert shed([klass("a", 1, 1, 100, 10, 1, 190), klass("b", 2, 1, 1, 1)], timeout=2000)["findings"] == []
    late = shed([klass("a", 1, 1, 100, 10, 1, 191), klass("b", 2, 1, 1, 1)], timeout=2000)
    assert found(late) == {("queue-wait-exceeds-timeout", "a"): "major"}
    assert "2010 ms" in late["findings"][0]["summary"]


CANARIES = ["AKIAIOSFODNN7EXAMPLE", "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "ab" * 32,
            "legal winner thank year wave sausage worth useful legal winner thank yellow", "https://svc-user:Sup3rS3cret@db.internal:5432/orders"]


def _assert_no_canary(result: dict, canary: str) -> None:
    blob = json.dumps(result, ensure_ascii=False)
    assert canary not in blob and "Sup3rS3cret" not in blob and canary[:12] not in blob, blob


@pytest.mark.parametrize("canary", CANARIES)
def test_probe_check_never_echoes_a_probe_endpoint_that_may_carry_a_credential(canary):
    # exec commands and URLs are probe endpoints: both can hold tokens, passwords and auth headers
    for kind in ("http", "exec", "tcp", "grpc"):
        endpoint = canary if kind != "exec" else f"curl -H 'Authorization: Bearer {canary}' localhost/health"
        result = probes([{"name": "app", "liveness": {"kind": kind, "endpoint": endpoint}, "readiness": {"kind": kind, "endpoint": endpoint}}])
        assert "liveness-equals-readiness" in rules(result)
        _assert_no_canary(result, canary)


@pytest.mark.parametrize("canary", CANARIES)
def test_chaos_spec_check_never_echoes_free_text_of_freeze_windows_or_metrics(canary):
    windows = [{"start": "2026-10-21T09:00:00+00:00", "end": "2026-10-21T12:00:00+00:00", "reason": f"deploy token {canary}"}]
    metrics = [{"metric": canary, "comparison": "lt", "threshold": 1}]
    for env in (chaos, prod):
        result = ok("resil_chaos_spec_check", env(freeze_windows=windows, steady_state=metrics, abort_conditions=metrics))
        assert "freeze-window" in rules(result)
        _assert_no_canary(result, canary)


@pytest.mark.parametrize("canary", CANARIES)
def test_drill_cadence_never_echoes_evidence_references(canary):
    # evidence_ref is a link or id of a drill record and may be a credentialed URL
    for evidence in (canary, ""):
        result = cadence([req(target_rto_minutes=5)], [drill(evidence=evidence, measured_rto_minutes=50), drill(evidence=canary, at="2026-10-01T00:00:00+00:00", outcome="failed")])
        _assert_no_canary(result, canary)
    stale = cadence([req(days=1)], [drill(evidence=canary)])
    assert "drill-overdue" in rules(stale)
    _assert_no_canary(stale, canary)


@pytest.mark.parametrize("canary", CANARIES)
def test_invalid_budget_specs_never_echo_the_rejected_value(canary):
    for name, payload in (("resil_pdb_check", {"workloads": [wl("w", 3, min_available=canary[:8])]}),
                          ("resil_quorum_safety", {"workload": "etcd", "replicas": 3, "members_per_zone": [1, 1, 1], "unavailable_members": 0,
                                                   "max_unavailable": canary[:8]})):
        result = call(name, payload)
        assert result["state"] == "INVALID_INPUT"
        assert canary[:8] not in json.dumps(result)


@pytest.mark.parametrize(("zones", "down", "flagged"), [
    ([2, 2, 1], 0, False),
    ([2, 2, 1], 1, True),      # 5 members, quorum 3: one down + the 2-member zone lost leaves 2 < 3
    ([2, 2, 1], 2, False),     # at the tolerance no-failure-headroom already says so
    ([3, 2, 2], 1, True),      # 7 members, quorum 4: 7 - 3 - 1 = 3 < 4
    ([2, 2, 2, 1], 1, False),  # 7 - 2 - 1 = 4 >= 4
])
def test_quorum_degraded_cluster_zone_loss_uses_the_members_already_down(zones, down, flagged):
    result = quorum(replicas=sum(zones), members_per_zone=zones, unavailable_members=down)
    assert ("zone-loss-while-degraded" in rules(result)) is flagged
    assert result["survives_zone_loss"] is True            # the healthy-cluster verdict is unchanged
    if flagged:
        assert found(result)[("zone-loss-while-degraded", "etcd")] == "major" and result["status"] == "fail"


def test_finding_text_keeps_the_digits_that_separate_an_exceeded_limit_from_the_limit():
    # 60.01 GiB at 1024 MiB/s = 1.000167 min against a 1 min target: %g rounding printed "1 min exceeds the 1 min target"
    result = ok("resil_restore_rto", rto(data_size_gib=60.01, target_rto_minutes=1))
    assert found(result) == {("rto-exceeds-target", "restore"): "blocker"}
    assert result["findings"][0]["summary"] == "estimated 1.000167 min exceeds the 1 min RTO target"


def _scheduler_outcomes(replicas: int, domains: int, skew: int, min_domains: int | None) -> tuple[int, int]:
    """Independent model of the kube-scheduler hard spread rule: a pod may go to a domain only when its count + 1 - global minimum
    <= maxSkew (the minimum counts as 0 while there are fewer domains than minDomains). Explore every placement order and return
    (the most pods one domain can ever hold, the most pods left Pending)."""
    seen, stack, most, pending = set(), [(0,) * domains], 0, 0
    while stack:
        state = stack.pop()
        if state in seen:
            continue
        seen.add(state)
        floor = 0 if min_domains is not None and min_domains > domains else min(state)
        moves = [i for i in range(domains) if state[i] + 1 - floor <= skew]
        if sum(state) == replicas or not moves:
            most, pending = max(most, max(state)), max(pending, replicas - sum(state))
            continue
        for i in moves:
            stack.append(state[:i] + (state[i] + 1,) + state[i + 1:])
    return most, pending


@pytest.mark.parametrize("min_domains", [None, 2, 4, 5])
@pytest.mark.parametrize("skew", [1, 2, 3])
@pytest.mark.parametrize("domains", [1, 2, 3, 4])
def test_spread_worst_case_matches_an_exhaustive_scheduler_simulation(domains, skew, min_domains):
    for replicas in range(1, 9):
        constraint = {"topology_key": ZONE, "max_skew": skew, "when_unsatisfiable": "DoNotSchedule"}
        if min_domains is not None:
            constraint["min_domains"] = min_domains
        result = ok("resil_spread_check", spread_payload(replicas=replicas, min_available_replicas=0, spread_constraints=[constraint],
                                                         topology_domains=[{"key": ZONE, "count": domains}]))
        assert (result["worst_case_pods_per_zone"], result["unschedulable_replicas"]) == _scheduler_outcomes(replicas, domains, skew, min_domains), \
            (replicas, domains, skew, min_domains)


@pytest.mark.parametrize("replicas", [1, 2, 3, 4, 5, 7, 10, 11, 100])
@pytest.mark.parametrize("percent", [0, 1, 25, 33, 34, 50, 66, 67, 99, 100])
def test_pdb_percentages_round_up_like_the_disruption_controller(replicas, percent):
    # intstr.GetScaledValueFromIntOrPercent(..., roundUp=true): ceil(replicas * percent / 100), exactly, for both fields
    expected_scaled = -(-replicas * percent // 100)
    by_min = pdb(wl("m", replicas, min_available=f"{percent}%"))["budgets"][0]
    assert (by_min["desired_healthy"], by_min["disruptions_allowed"]) == (expected_scaled, replicas - expected_scaled)
    by_max = pdb(wl("x", replicas, max_unavailable=f"{percent}%"))["budgets"][0]
    assert (by_max["desired_healthy"], by_max["disruptions_allowed"]) == (replicas - expected_scaled, expected_scaled)
