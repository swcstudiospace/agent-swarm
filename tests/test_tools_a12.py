"""A12 release-manager tools added in v2.2: gate freshness, freeze decision, resume point, guardrail breaches.

Every call goes through the registry call path (`call_tool`) on a deep copy of the registry document that also
carries this fragment's entries, so the tests pass before and after registry/intake is merged into agents.json.
"""
import copy
import json
from pathlib import Path

import pytest

from swarm.tool_registry import call_tool, load_document, validate_registry

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a12-release.tools.json"
NEW_TOOLS = ("rel_gate_freshness", "rel_freeze_decision", "rel_resume_point", "rel_guardrail_breaches")


def _merged() -> dict:
    document = copy.deepcopy(load_document())
    release = next(agent for agent in document["agents"] if agent["id"] == "A12")
    present = {tool["name"] for tool in release["registered_tools"]}
    if FRAGMENT.is_file():
        release["registered_tools"].extend(
            copy.deepcopy(entry) for entry in json.loads(FRAGMENT.read_text(encoding="utf-8")) if entry["name"] not in present
        )
    return document


@pytest.fixture(scope="module")
def registry() -> dict:
    return _merged()


def call(registry, name, payload, caller="A12"):
    return call_tool(name, caller, payload, data=registry)


def test_new_entries_pass_the_registry_validator(registry, tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    assert validate_registry(path) == []
    names = {tool["name"] for tool in next(a for a in registry["agents"] if a["id"] == "A12")["registered_tools"]}
    assert set(NEW_TOOLS) <= names


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_registry_examples_follow_the_contract(registry, name):
    tool = next(t for _c, t in ((None, t) for a in registry["agents"] for t in a["registered_tools"]) if t["name"] == name)
    examples = tool["examples"]
    assert tool["side_effect_tier"] == "READ"
    assert call(registry, name, examples["success"])["state"] == "SUCCESS"
    assert call(registry, name, examples["invalid_input"])["state"] == "INVALID_INPUT"
    assert call(registry, name, examples["success"], caller=examples["unauthorized_caller"])["state"] == "UNAUTHORIZED"
    assert call(registry, name, examples["dependency"])["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call(registry, name, examples["timeout"])["state"] == "TIMEOUT"
    assert call(registry, name, examples["partial"])["state"] == "PARTIAL_SUCCESS"


def test_only_the_gate_freshness_tool_is_open_to_the_security_gate(registry):
    callers = {t["name"]: t["permitted_callers"] for a in registry["agents"] for t in a["registered_tools"] if t["name"] in NEW_TOOLS}
    assert callers["rel_gate_freshness"] == ["A12", "A10"]
    assert all(callers[name] == ["A12"] for name in NEW_TOOLS if name != "rel_gate_freshness")
    assert call(registry, "rel_gate_freshness", _freshness(), caller="A10")["state"] == "SUCCESS"
    assert call(registry, "rel_freeze_decision", {"alerts": [], "release_id": "R"}, caller="A10")["state"] == "UNAUTHORIZED"


# --- rel_gate_freshness ------------------------------------------------------------------------------------

NOW = "2026-08-30T09:41:00Z"


def _freshness(**over):
    payload = {
        "verdicts": [{"gate": "security", "verdict": "pass", "issued_at": "2026-08-29T09:41:00Z"}],
        "max_age_h": {"security": 24},
        "now": NOW,
    }
    payload.update(over)
    return payload


def _verdict(gate, verdict, issued_at):
    return {"gate": gate, "verdict": verdict, "issued_at": issued_at}


def test_security_verdict_exactly_24h_old_is_still_fresh(registry):
    result = call(registry, "rel_gate_freshness", _freshness())
    assert result == {"state": "SUCCESS", "fresh": "security", "expired": "", "unwaived": "", "failed": "", "ok": True}


def test_security_verdict_one_second_over_24h_is_expired(registry):
    verdicts = [_verdict("security", "pass", "2026-08-29T09:40:59Z")]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts))
    assert result["expired"] == "security" and result["fresh"] == "" and result["ok"] is False


def test_each_gate_uses_its_own_maximum_age(registry):
    verdicts = [_verdict("review", "pass", "2026-08-30T01:41:00Z"), _verdict("security", "pass", "2026-08-30T01:41:00Z")]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, max_age_h={"review": 4, "security": 24}))
    assert (result["fresh"], result["expired"]) == ("security", "review")


def test_waive_needs_a_recorded_approval_id(registry):
    verdicts = [_verdict("quality", "waive", "2026-08-30T08:00:00Z")]
    bare = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, max_age_h={"quality": 24}))
    assert bare["unwaived"] == "quality" and bare["ok"] is False
    waived = call(registry, "rel_gate_freshness", _freshness(
        verdicts=verdicts, max_age_h={"quality": 24}, waivers=[{"gate": "quality", "approval_id": "human:pm#17"}]))
    assert waived["unwaived"] == "" and waived["ok"] is True


def test_waiver_for_another_gate_does_not_cover_the_waive(registry):
    verdicts = [_verdict("quality", "waive", "2026-08-30T08:00:00Z")]
    result = call(registry, "rel_gate_freshness", _freshness(
        verdicts=verdicts, max_age_h={"quality": 24}, waivers=[{"gate": "review", "approval_id": "human:pm#17"}]))
    assert result["unwaived"] == "quality"


def test_failed_verdict_is_reported_and_blocks_ok(registry):
    verdicts = [_verdict("review", "fail", "2026-08-30T08:00:00Z"), _verdict("security", "pass", "2026-08-30T08:00:00Z")]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, max_age_h={"review": 24, "security": 24}))
    assert result["failed"] == "review" and result["fresh"] == "review,security" and result["ok"] is False


def test_epoch_seconds_from_a_signed_envelope_are_accepted(registry):
    # 2026-08-29T09:41:00Z is 1788000060 seconds after the epoch
    verdicts = [_verdict("security", "pass", "1788000060")]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, now="1788086460.0"))
    assert result["fresh"] == "security"
    late = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, now="1788086461"))
    assert late["expired"] == "security"


@pytest.mark.parametrize(
    "over, field",
    [
        ({"now": "yesterday"}, "now"),
        ({"now": "2026-08-30T09:41:00"}, "now"),
        ({"verdicts": [_verdict("security", "pass", "not-a-date")]}, "verdicts"),
        ({"verdicts": [_verdict("security", "pass", "2026-08-31T00:00:00Z")]}, "verdicts"),
        ({"verdicts": [_verdict("security", "pass", NOW), _verdict("security", "fail", NOW)]}, "verdicts"),
        ({"verdicts": [_verdict("a,b", "pass", NOW)], "max_age_h": {"a,b": 24}}, "verdicts"),
        ({"max_age_h": {}}, "max_age_h"),
        ({"max_age_h": {"security": 0}}, "max_age_h"),
        ({"max_age_h": {"security": "24"}}, "max_age_h"),
        ({"waivers": [{"gate": "security", "approval_id": "  "}]}, "waivers"),
        ({"verdicts": []}, "verdicts"),
        ({"verdicts": [_verdict("security", "approve", NOW)]}, "verdicts"),
    ],
)
def test_freshness_rejects_malformed_input_and_names_the_field(registry, over, field):
    result = call(registry, "rel_gate_freshness", _freshness(**over))
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_freshness_rejects_an_oversized_verdict_list(registry):
    verdicts = [_verdict(f"g{i}", "pass", NOW) for i in range(51)]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "verdicts"


def test_verdict_issued_earlier_in_the_same_second_is_not_after_now(registry):
    """Reviewer repro (RV-E 6): issued_at '1760000000.6' with now '1760000000' returned
    INVALID_INPUT 'issued_at is after now' (gates.py stamps time.time(), `date +%s` truncates)."""
    verdicts = [_verdict("security", "pass", "1760000000.6")]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, now="1760000000"))
    assert result["state"] == "SUCCESS" and result["fresh"] == "security" and result["ok"] is True


def test_verdict_more_than_a_second_after_now_is_still_invalid(registry):
    verdicts = [_verdict("security", "pass", "1760000002")]
    result = call(registry, "rel_gate_freshness", _freshness(verdicts=verdicts, now="1760000000"))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "verdicts"


@pytest.mark.parametrize("hours", [1e300, 87601, 1e308])
def test_huge_max_age_is_invalid_input_not_a_dependency_error(registry, hours):
    """Reviewer repro (RV-E 7): max_age_h 1e300 returned DEPENDENCY_UNAVAILABLE 'Python int too large'."""
    result = call(registry, "rel_gate_freshness", _freshness(max_age_h={"security": hours}))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "max_age_h"


def test_max_age_of_ten_years_is_accepted(registry):
    result = call(registry, "rel_gate_freshness", _freshness(max_age_h={"security": 87600}))
    assert result["state"] == "SUCCESS" and result["fresh"] == "security"


# --- rel_freeze_decision -----------------------------------------------------------------------------------


def _alert(ident, severity, release_id=None):
    alert = {"id": ident, "severity": severity}
    if release_id is not None:
        alert["release_id"] = release_id
    return alert


def _freeze(alerts, **over):
    payload = {"alerts": alerts, "release_id": "REL-118"}
    payload.update(over)
    return payload


def test_severity_two_alert_naming_the_release_freezes(registry):
    result = call(registry, "rel_freeze_decision", _freeze([_alert("INC-77", 2, "REL-118")]))
    assert result["freeze"] is True and result["matched"] == "INC-77" and result["worst_sev"] == 2
    assert result["unfreeze_allowed"] is False and "no unfreeze requested" in result["reason"]


def test_severity_three_does_not_freeze(registry):
    result = call(registry, "rel_freeze_decision", _freeze([_alert("INC-78", 3, "REL-118"), _alert("INC-79", 4)]))
    assert result["freeze"] is False and result["matched"] == "" and result["worst_sev"] == 3


def test_no_alerts_means_no_freeze_and_worst_severity_zero(registry):
    result = call(registry, "rel_freeze_decision", _freeze([]))
    assert result["freeze"] is False and result["worst_sev"] == 0


def test_alert_without_a_release_is_swarm_wide_and_matches(registry):
    result = call(registry, "rel_freeze_decision", _freeze([_alert("INC-1", 1)]))
    assert result["freeze"] is True and result["matched"] == "INC-1" and result["worst_sev"] == 1


def test_alert_naming_another_release_still_freezes_the_swarm_but_is_not_matched(registry):
    result = call(registry, "rel_freeze_decision", _freeze([_alert("INC-9", 2, "REL-999")]))
    assert result["freeze"] is True and result["matched"] == ""
    assert "other releases" in result["reason"]


def test_worst_severity_is_the_lowest_number_across_all_alerts(registry):
    result = call(registry, "rel_freeze_decision", _freeze([_alert("A", 3, "REL-118"), _alert("B", 1, "REL-5"), _alert("C", 2, "REL-118")]))
    assert result["worst_sev"] == 1 and result["matched"] == "C"


@pytest.mark.parametrize(
    "frozen_by, unfreeze_by, allowed",
    [
        ("A13", "A13", True),
        ("A13", "A12", False),
        ("A13", "human:oncall", True),
        ("human:alice", "A13", False),
        ("human:alice", "human:bob", True),
        ("A12@local", "A13", True),
        ("A12@local", "A12", False),
        ("A13", "A05", False),
        ("A13", "human:", False),
    ],
)
def test_only_the_declaring_authority_may_unfreeze(registry, frozen_by, unfreeze_by, allowed):
    result = call(registry, "rel_freeze_decision", _freeze([_alert("INC-77", 2)], frozen_by=frozen_by, unfreeze_by=unfreeze_by))
    assert result["state"] == "SUCCESS" and result["unfreeze_allowed"] is allowed
    assert result["freeze"] is True  # an open sev<=2 alert is reported regardless of who asks


def test_unfreeze_request_needs_the_declaring_authority_named(registry):
    result = call(registry, "rel_freeze_decision", _freeze([], unfreeze_by="A13"))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "frozen_by"


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"alerts": [{"id": "x", "severity": 0}], "release_id": "R"}, "alerts"),
        ({"alerts": [{"id": "x", "severity": "2"}], "release_id": "R"}, "alerts"),
        ({"alerts": [{"severity": 2}], "release_id": "R"}, "alerts"),
        ({"alerts": [{"id": "a,b", "severity": 2}], "release_id": "R"}, "alerts"),
        ({"alerts": []}, "release_id"),
        ({"alerts": [], "release_id": ""}, "release_id"),
        ({"alerts": "INC-1", "release_id": "R"}, "alerts"),
    ],
)
def test_freeze_rejects_malformed_input_and_names_the_field(registry, payload, field):
    result = call(registry, "rel_freeze_decision", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_freeze_rejects_more_than_200_alerts(registry):
    result = call(registry, "rel_freeze_decision", _freeze([_alert(f"I{n}", 5) for n in range(201)]))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "alerts"


# --- rel_resume_point --------------------------------------------------------------------------------------

STEPS = [5, 25, 50, 100]


def _resume(confirmed, breach=False, in_flight=None, steps=STEPS):
    payload = {"steps_pct": steps, "confirmed": confirmed, "breach": breach}
    if in_flight is not None:
        payload["in_flight"] = in_flight
    return payload


@pytest.mark.parametrize(
    "payload, action, next_pct",
    [
        (_resume([]), "resume", 5),
        (_resume([5]), "resume", 25),
        (_resume([5, 25], in_flight=50), "resume", 50),
        (_resume([5, 25, 50]), "resume", 100),
        (_resume([5, 25, 50, 100]), "complete", 100),
        (_resume([5, 25], breach=True), "rollback", 0),
        (_resume([5, 25, 50, 100], breach=True), "rollback", 0),
        (_resume([]), "resume", 5),
    ],
)
def test_resume_point_table(registry, payload, action, next_pct):
    result = call(registry, "rel_resume_point", payload)
    assert (result["state"], result["action"], result["next_pct"]) == ("SUCCESS", action, next_pct)
    assert result["reason"]


def test_breach_beats_an_in_flight_step(registry):
    result = call(registry, "rel_resume_point", _resume([5], breach=True, in_flight=25))
    assert result["action"] == "rollback" and "breach" in result["reason"]


@pytest.mark.parametrize(
    "confirmed, in_flight",
    [
        ([25], None),
        ([5, 50], None),
        ([25, 5], None),
        ([5, 5], None),
        ([5, 30], None),
        ([5, 25, 50, 100, 100], None),
        ([5], 50),
        ([5, 25], 25),
        ([5, 25, 50, 100], 100),
    ],
)
def test_out_of_order_or_inconsistent_state_rolls_back_cleanly(registry, confirmed, in_flight):
    result = call(registry, "rel_resume_point", _resume(confirmed, in_flight=in_flight))
    assert result["action"] == "rollback" and result["next_pct"] == 0


@pytest.mark.parametrize(
    "payload, field",
    [
        (_resume([], steps=[5, 5, 100]), "steps_pct"),
        (_resume([], steps=[25, 5, 100]), "steps_pct"),
        (_resume([], steps=[5, 25, 50]), "steps_pct"),
        (_resume([], steps=[]), "steps_pct"),
        (_resume([], steps=[0, 100]), "steps_pct"),
        (_resume([], steps=[5, 101]), "steps_pct"),
        ({"steps_pct": STEPS, "confirmed": []}, "breach"),
        ({"steps_pct": STEPS, "confirmed": [], "breach": "no"}, "breach"),
        ({"steps_pct": STEPS, "confirmed": "5", "breach": False}, "confirmed"),
        (_resume([], in_flight=0), "in_flight"),
    ],
)
def test_resume_rejects_malformed_input_and_names_the_field(registry, payload, field):
    result = call(registry, "rel_resume_point", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_resume_rejects_more_than_20_steps(registry):
    result = call(registry, "rel_resume_point", _resume([], steps=list(range(1, 22)) + [100]))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "steps_pct"


# --- rel_guardrail_breaches --------------------------------------------------------------------------------

MEDIUM = {"error_rate_max": 0.005, "p95_ms_max": 320, "slo_burn_max": 2.0}


def _guard(observed, limits=MEDIUM):
    return {"observed": observed, "limits": limits}


def test_medium_sample_inside_every_limit_has_no_breach(registry):
    result = call(registry, "rel_guardrail_breaches", _guard({"error_rate": 0.004, "p95_ms": 300, "slo_burn": 1.5}))
    assert result == {"state": "SUCCESS", "breaches": "", "breached": False, "missing_metrics": ""}


def test_a_value_equal_to_its_limit_is_not_a_breach(registry):
    result = call(registry, "rel_guardrail_breaches", _guard({"error_rate": 0.005, "p95_ms": 320, "slo_burn": 2.0}))
    assert result["breached"] is False


def test_p95_breach_is_found_even_when_error_rate_and_burn_are_fine(registry):
    result = call(registry, "rel_guardrail_breaches", _guard({"error_rate": 0.001, "p95_ms": 321, "slo_burn": 0.5}))
    assert result["breaches"] == "p95_ms" and result["breached"] is True


def test_every_breached_metric_is_listed_in_a_fixed_order(registry):
    result = call(registry, "rel_guardrail_breaches", _guard({"error_rate": 0.02, "p95_ms": 900, "slo_burn": 14}))
    assert result["breaches"] == "error_rate,p95_ms,slo_burn"


def test_the_risk_class_limits_decide_the_breach(registry):
    sample = {"error_rate": 0.003, "p95_ms": 260, "slo_burn": 1.2}
    high = {"error_rate_max": 0.002, "p95_ms_max": 250, "slo_burn_max": 1.0}
    low = {"error_rate_max": 0.01, "p95_ms_max": 500, "slo_burn_max": 6.0}
    assert call(registry, "rel_guardrail_breaches", _guard(sample, low))["breached"] is False
    assert call(registry, "rel_guardrail_breaches", _guard(sample, high))["breaches"] == "error_rate,p95_ms,slo_burn"


def test_missing_metrics_are_reported_and_never_read_as_healthy(registry):
    result = call(registry, "rel_guardrail_breaches", _guard({"error_rate": 0.001}))
    assert result["missing_metrics"] == "p95_ms,slo_burn" and result["breached"] is False
    assert call(registry, "rel_guardrail_breaches", _guard({}))["missing_metrics"] == "error_rate,p95_ms,slo_burn"


def test_a_breach_and_a_missing_metric_are_both_reported(registry):
    result = call(registry, "rel_guardrail_breaches", _guard({"error_rate": 0.5, "slo_burn": 0.1}))
    assert result["breaches"] == "error_rate" and result["missing_metrics"] == "p95_ms"


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"observed": {"error_rate": -0.1}, "limits": MEDIUM}, "observed"),
        ({"observed": {"error_rate": 1.5}, "limits": MEDIUM}, "observed"),
        ({"observed": {"error_rate": float("nan")}, "limits": MEDIUM}, "observed"),
        ({"observed": {"p95_ms": float("inf")}, "limits": MEDIUM}, "observed"),
        ({"observed": {"p95_ms": "300"}, "limits": MEDIUM}, "observed"),
        ({"observed": {"latency": 3}, "limits": MEDIUM}, "observed"),
        ({"observed": {}, "limits": {"error_rate_max": 0.005, "p95_ms_max": 320}}, "limits"),
        ({"observed": {}, "limits": {**MEDIUM, "p95_ms_max": -1}}, "limits"),
        ({"observed": {}}, "limits"),
        ({"limits": MEDIUM}, "observed"),
    ],
)
def test_guardrails_reject_malformed_input_and_names_the_field(registry, payload, field):
    result = call(registry, "rel_guardrail_breaches", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field
