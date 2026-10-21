"""Behaviour checks for the v2.2 A13 tools: obs_mitigation_gate and obs_escalation_due."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm.tool_registry import call_tool, load_document, validate_registry
from swarm.tools.a13_tools import escalation_due, mitigation_gate

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a13-observability.tools.json"
NEW_TOOLS = ["obs_mitigation_gate", "obs_escalation_due"]
WHITELIST = ["flag-off:checkout-v2", "rollback:latest-release", "scale-out:staging"]


def _fragment_entries() -> list[dict]:
    """This unit's new tool entries: the intake fragment before the merge, the merged registry entries after it."""
    if FRAGMENT.is_file():
        return json.loads(FRAGMENT.read_text(encoding="utf-8"))
    agent = next(a for a in load_document()["agents"] if a["id"] == "A13")
    return [t for t in agent["registered_tools"] if t["name"] in NEW_TOOLS]


def _merged() -> dict:
    """Loaded registry plus this fragment's entries (only the ones not already present)."""
    document = copy.deepcopy(load_document())
    agent = next(a for a in document["agents"] if a["id"] == "A13")
    present = {t["name"] for t in agent["registered_tools"]}
    for entry in _fragment_entries():
        if entry["name"] not in present:
            agent["registered_tools"].append(copy.deepcopy(entry))
    return document


DOC = _merged()


def call(name, caller, payload):
    return call_tool(name, caller, payload, data=DOC)


def gate(**over):
    payload = {"playbook": "flag-off:checkout-v2", "whitelist": WHITELIST, "environment": "canary", "auto_count": 0}
    payload.update(over)
    return mitigation_gate(payload)


def due(**over):
    payload = {"sev": 2, "declared_at": "2026-10-15T10:00:00Z", "now": "2026-10-15T10:05:00Z"}
    payload.update(over)
    return escalation_due(payload)


# --- obs_mitigation_gate -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "over, reason, allowed",
    [
        ({}, "ok", True),
        ({"environment": "staging"}, "ok", True),
        ({"playbook": "restart:everything"}, "not-whitelisted", False),
        ({"whitelist": []}, "not-whitelisted", False),
        ({"environment": "prod"}, "prod-needs-approval", False),
        ({"environment": "prod", "full_prod_with_a12": False}, "prod-needs-approval", False),
        ({"environment": "prod", "full_prod_with_a12": True}, "ok", True),
        ({"auto_count": 3}, "cap-reached", False),
    ],
)
def test_gate_reasons(over, reason, allowed):
    result = gate(**over)
    assert result["state"] == "SUCCESS"
    assert (result["reason"], result["allowed"]) == (reason, allowed)


def test_gate_cap_boundary_is_three_automatic_mitigations():
    assert gate(auto_count=2) == {"state": "SUCCESS", "allowed": True, "reason": "ok", "remaining_auto": 1}
    assert gate(auto_count=3) == {"state": "SUCCESS", "allowed": False, "reason": "cap-reached", "remaining_auto": 0}


def test_gate_over_the_cap_never_goes_negative():
    result = gate(auto_count=9)
    assert result["reason"] == "cap-reached" and result["remaining_auto"] == 0


def test_gate_custom_max_auto():
    assert gate(auto_count=3, max_auto=5)["remaining_auto"] == 2
    assert gate(auto_count=5, max_auto=5)["reason"] == "cap-reached"


def test_gate_whitelist_checked_before_environment_and_cap():
    result = gate(playbook="drop-table", environment="prod", auto_count=7)
    assert result["reason"] == "not-whitelisted"


def test_gate_prod_approval_checked_before_cap():
    assert gate(environment="prod", auto_count=3)["reason"] == "prod-needs-approval"


def test_gate_whitelist_match_is_exact():
    assert gate(playbook="flag-off:checkout")["reason"] == "not-whitelisted"
    assert gate(playbook="FLAG-OFF:CHECKOUT-V2")["reason"] == "not-whitelisted"


def test_gate_malformed_input_names_the_field():
    for bad, field in (
        ({"environment": "dev"}, "environment"),
        ({"auto_count": -1}, "auto_count"),
        ({"whitelist": "flag-off"}, "whitelist"),
        ({"playbook": ""}, "playbook"),
        ({"max_auto": 0}, "max_auto"),
        ({"full_prod_with_a12": "yes"}, "full_prod_with_a12"),
    ):
        payload = {"playbook": "p", "whitelist": ["p"], "environment": "canary", "auto_count": 0, **bad}
        result = call("obs_mitigation_gate", "A13", payload)
        assert result["state"] == "INVALID_INPUT" and field in json.dumps(result), bad


def test_gate_empty_payload_and_unauthorized_caller():
    assert call("obs_mitigation_gate", "A13", {})["state"] == "INVALID_INPUT"
    assert call("obs_mitigation_gate", "A12", {"playbook": "p", "whitelist": ["p"], "environment": "canary", "auto_count": 0})["state"] == "UNAUTHORIZED"


def test_gate_oversized_whitelist_still_decides():
    big = [f"playbook-{i}" for i in range(5000)] + ["flag-off:checkout-v2"]
    assert gate(whitelist=big)["allowed"] is True


# --- obs_escalation_due --------------------------------------------------------------------------


def test_due_boundary_is_exactly_600_seconds():
    assert due(now="2026-10-15T10:09:59Z") == {"state": "SUCCESS", "elapsed_s": 599.0, "due": False, "remaining_s": 1.0, "reason": "waiting"}
    at = due(now="2026-10-15T10:10:00Z")
    assert (at["elapsed_s"], at["due"], at["remaining_s"], at["reason"]) == (600.0, True, 0.0, "escalate")
    assert due(now="2026-10-15T10:25:00Z")["due"] is True


def test_due_sev1_and_sev2_escalate_sev3_never():
    late = {"now": "2026-10-15T12:00:00Z"}
    assert due(sev=1, **late)["due"] is True
    assert due(sev=2, **late)["due"] is True
    low = due(sev=3, **late)
    assert low["due"] is False and low["reason"] == "sev-below-threshold" and low["remaining_s"] == 0.0


def test_due_stabilized_incident_does_not_escalate():
    result = due(now="2026-10-15T10:30:00Z", stabilized_at="2026-10-15T10:08:00Z")
    assert result["due"] is False and result["reason"] == "stabilized" and result["elapsed_s"] == 480.0


def test_due_custom_max_minutes():
    assert due(max_minutes=3)["due"] is True
    assert due(max_minutes=30)["remaining_s"] == 1500.0


def test_due_offsets_are_compared_as_instants():
    result = due(declared_at="2026-10-15T12:00:00+02:00", now="2026-10-15T10:10:00Z")
    assert result["elapsed_s"] == 600.0 and result["due"] is True


def test_due_timestamp_without_timezone_is_invalid_input():
    for field in ("declared_at", "now"):
        bad = {"sev": 2, "declared_at": "2026-10-15T10:00:00Z", "now": "2026-10-15T10:05:00Z", field: "2026-10-15T10:00:00"}
        result = escalation_due(bad)
        assert result["state"] == "INVALID_INPUT" and result["field"] == field
    result = due(stabilized_at="2026-10-15T10:02:00")
    assert result["state"] == "INVALID_INPUT" and result["field"] == "stabilized_at"


def test_due_ordering_errors_name_the_field():
    assert due(now="2026-10-15T09:00:00Z")["field"] == "now"
    assert due(stabilized_at="2026-10-15T09:00:00Z")["field"] == "stabilized_at"
    assert due(stabilized_at="2026-10-15T10:06:00Z")["field"] == "stabilized_at"
    assert due(declared_at="not-a-time")["field"] == "declared_at"


def test_due_registry_rejects_malformed_and_unauthorized():
    good = {"sev": 2, "declared_at": "2026-10-15T10:00:00Z", "now": "2026-10-15T10:05:00Z"}
    assert call("obs_escalation_due", "A13", {})["state"] == "INVALID_INPUT"
    assert call("obs_escalation_due", "A13", {**good, "sev": 0})["state"] == "INVALID_INPUT"
    assert call("obs_escalation_due", "A13", {**good, "max_minutes": 0})["state"] == "INVALID_INPUT"
    assert call("obs_escalation_due", "A13", {**good, "declared_at": ""})["state"] == "INVALID_INPUT"
    assert call("obs_escalation_due", "A01", good)["state"] == "UNAUTHORIZED"


def test_due_registry_bounds_max_minutes():
    """Reviewer repro (RV-E 7): max_minutes 1e308 returned remaining_s inf, which is not valid JSON."""
    good = {"sev": 2, "declared_at": "2026-10-15T10:00:00Z", "now": "2026-10-15T10:05:00Z"}
    assert call("obs_escalation_due", "A13", {**good, "max_minutes": 1e308})["state"] == "INVALID_INPUT"
    assert call("obs_escalation_due", "A13", {**good, "max_minutes": 525601})["state"] == "INVALID_INPUT"
    ok = call("obs_escalation_due", "A13", {**good, "max_minutes": 525600})
    assert ok["state"] == "SUCCESS" and ok["remaining_s"] == 525600 * 60 - 300.0


def test_due_spec_example_sev2_unstabilized_after_ten_minutes():
    """agents A13 section 4: sev >= 2 without stabilization for 10 min escalates with a timeline bundle."""
    result = due(declared_at="2026-10-15T10:00:00Z", now="2026-10-15T10:11:30Z")
    assert result["due"] is True and result["elapsed_s"] == 690.0


# --- registry fragment -------------------------------------------------------------------------


def test_fragment_entries_validate_in_a_copy_of_the_registry(tmp_path):
    entries = _fragment_entries()
    assert [e["name"] for e in entries] == NEW_TOOLS
    document = _merged()
    copy_path = tmp_path / "agents.json"
    copy_path.write_text(json.dumps(document), encoding="utf-8")
    assert validate_registry(copy_path) == []
