"""Behaviour tests for the v2.2 A08 lane tools: qa_tier_plan, qa_finding_shape_check, qa_defect_dedupe.

Calls go through swarm.tool_registry.call_tool against the loaded registry plus this unit's intake fragment
(entries not already merged), so the tests pass before and after `_registry_intake.py --apply`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tool_registry import call_tool
from swarm.tools.a08_tools import finding_shape_check

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a08-qa.tools.json"
HANDLERS = {"qa_tier_plan": "tier_plan", "qa_finding_shape_check": "finding_shape_check", "qa_defect_dedupe": "defect_dedupe"}
NEW_TOOLS = tuple(HANDLERS)


def _merged_document() -> dict:
    document = copy.deepcopy(tool_registry.load_document())
    entries = json.loads(FRAGMENT.read_text(encoding="utf-8")) if FRAGMENT.exists() else []
    agent = next(item for item in document["agents"] if item["id"] == "A08")
    present = {tool["name"] for tool in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(entry) for entry in entries if entry["name"] not in present)
    return document


DOCUMENT = _merged_document()


def call(name: str, payload: dict, caller: str = "A08") -> dict:
    return call_tool(name, caller, payload, data=DOCUMENT)


def run(name: str, payload: dict) -> dict:
    result = call(name, payload)
    assert result["state"] == "SUCCESS", result
    return result


# ---- registry ---------------------------------------------------------------------------------


def test_fragment_entries_validate_in_a_copy_of_the_registry(tmp_path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(DOCUMENT), encoding="utf-8")
    assert tool_registry.validate_registry(path) == []


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_new_tools_are_owner_only_read_and_idempotent(name):
    entry = tool_registry.find_tool(DOCUMENT, name)
    assert entry is not None
    assert entry["owner"] == "A08" and entry["permitted_callers"] == ["A08"]
    assert entry["side_effect_tier"] == "READ" and entry["idempotency"] == "idempotent"
    assert entry["handler"] == "swarm.tools.a08_tools:" + HANDLERS[name]
    assert entry["entrypoint"] == "swarm/tools/a08_tools.py"
    assert "|" not in entry["purpose"]


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_other_agents_are_unauthorized_and_probes_are_terminal(name):
    entry = tool_registry.find_tool(DOCUMENT, name)
    examples = entry["examples"]
    assert call(name, examples["success"], caller=examples["unauthorized_caller"])["state"] == "UNAUTHORIZED"
    assert call(name, examples["success"])["state"] == "SUCCESS"
    assert call(name, examples["invalid_input"])["state"] == "INVALID_INPUT"
    assert call(name, examples["dependency"])["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call(name, examples["timeout"])["state"] == "TIMEOUT"
    assert call(name, examples["partial"])["state"] == "PARTIAL_SUCCESS"


# ---- qa_tier_plan -----------------------------------------------------------------------------

ALL_PASS = {"unit": "pass", "integration": "pass", "e2e": "pass", "perf": "pass"}


def test_tier_plan_low_requires_unit_only_and_passes_with_optional_skips():
    result = run("qa_tier_plan", {"risk_class": "low", "runs": {"unit": "pass", "integration": "skipped:infra"}})
    assert result["required"] == ["unit"]
    assert result["mode"] == "full"
    assert result["skipped_optional"] == ["integration", "e2e", "perf"]
    assert result["missing_required"] == [] and result["failed"] == []
    assert result["verdict"] == "pass"


def test_tier_plan_medium_fails_when_integration_is_missing():
    result = run("qa_tier_plan", {"risk_class": "medium", "runs": {"unit": "pass", "integration": "skipped:infra"}})
    assert result["required"] == ["unit", "integration"]
    assert result["missing_required"] == ["integration"]
    assert result["verdict"] == "fail"


def test_tier_plan_medium_ignores_skipped_e2e_and_perf():
    runs = {"unit": "pass", "integration": "pass", "e2e": "skipped:infra", "perf": "skipped:tool-missing"}
    result = run("qa_tier_plan", {"risk_class": "medium", "runs": runs})
    assert result["skipped_optional"] == ["e2e", "perf"]
    assert result["verdict"] == "pass"


def test_tier_plan_high_requires_all_four_and_names_each_missing_tier():
    runs = {"unit": "pass", "integration": "pass", "e2e": "skipped:infra", "perf": "skipped:infra"}
    result = run("qa_tier_plan", {"risk_class": "high", "runs": runs})
    assert result["required"] == ["unit", "integration", "e2e", "perf"]
    assert result["missing_required"] == ["e2e", "perf"]
    assert result["verdict"] == "fail"
    assert run("qa_tier_plan", {"risk_class": "high", "runs": ALL_PASS})["verdict"] == "pass"


def test_tier_plan_absent_required_tier_counts_as_not_selected_and_missing():
    result = run("qa_tier_plan", {"risk_class": "medium", "runs": {"unit": "pass"}})
    assert result["missing_required"] == ["integration"]
    assert result["verdict"] == "fail"


def test_tier_plan_failed_tier_fails_even_when_optional():
    runs = {"unit": "pass", "integration": "pass", "e2e": "fail"}
    result = run("qa_tier_plan", {"risk_class": "low", "runs": runs})
    assert result["failed"] == ["e2e"]
    assert result["verdict"] == "fail"


def test_tier_plan_degraded_mode_requires_only_the_unit_smoke_tier():
    runs = {"unit": "pass", "integration": "skipped:infra", "e2e": "skipped:infra", "perf": "skipped:infra"}
    result = run("qa_tier_plan", {"risk_class": "high", "runs": runs, "degraded": True})
    assert result["mode"] == "smoke"
    assert result["required"] == ["unit"]
    assert result["missing_required"] == []
    assert result["skipped_optional"] == ["integration", "e2e", "perf"]
    assert result["verdict"] == "pass"
    broken = run("qa_tier_plan", {"risk_class": "high", "runs": {"unit": "fail"}, "degraded": True})
    assert broken["verdict"] == "fail" and broken["failed"] == ["unit"]


def test_tier_plan_purpose_says_degraded_mode_is_not_in_the_gate_script():
    """Skill reviewers: `degraded` has no counterpart in scripts/qa_gate.py, but the purpose read as if the gate honoured it."""
    entry = next(tool for tool in DOCUMENT["agents"][next(i for i, a in enumerate(DOCUMENT["agents"]) if a["id"] == "A08")]["registered_tools"] if tool["name"] == "qa_tier_plan")
    assert "qa_gate.py has no degraded mode" in entry["purpose"]
    assert "qa_gate.py" in entry["input_schema"]["properties"]["degraded"]["description"]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"risk_class": "extreme", "runs": {}}, "risk_class"),
        ({"risk_class": "low"}, "runs"),
        ({"risk_class": "low", "runs": {"smoke": "pass"}}, "runs"),
        ({"risk_class": "low", "runs": {"unit": "green"}}, "runs"),
        ({"risk_class": "low", "runs": {"unit": "pass"}, "degraded": "yes"}, "degraded"),
        ({"risk_class": "low", "runs": {}, "stray": 1}, "stray"),
    ],
)
def test_tier_plan_rejects_malformed_input_naming_the_field(payload, field):
    result = call("qa_tier_plan", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


# ---- qa_finding_shape_check -------------------------------------------------------------------

GOOD = {"id": "QF-001", "severity": "major", "kind": "functional", "ac_ref": "AC-1", "evidence": "test_ping: 500"}


def test_finding_shape_accepts_the_prompt_example_shape():
    findings = [GOOD, {"id": "QF-002", "severity": "minor", "kind": "coverage", "ac_ref": None, "evidence": ""}]
    result = run("qa_finding_shape_check", {"findings": findings})
    assert result == {"state": "SUCCESS", "checked": 2, "violations": [], "conforming": True}


def test_finding_shape_empty_list_is_conforming():
    result = run("qa_finding_shape_check", {"findings": []})
    assert result["checked"] == 0 and result["conforming"] is True


def test_finding_shape_flags_duplicate_ids_on_the_second_occurrence():
    result = run("qa_finding_shape_check", {"findings": [GOOD, dict(GOOD)]})
    assert result["conforming"] is False
    assert [(v["index"], v["field"]) for v in result["violations"]] == [(1, "id")]
    assert "duplicate" in result["violations"][0]["message"]


@pytest.mark.parametrize("bad_id", ["", "QF-", "qf-1", "F-1", "QF-1a", "QF-001 "])
def test_finding_shape_flags_ids_that_are_not_qf_numbers(bad_id):
    finding = {**GOOD, "id": bad_id}
    result = run("qa_finding_shape_check", {"findings": [finding]})
    fields = [v["field"] for v in result["violations"]]
    # trailing whitespace is stripped before matching, every other value is rejected
    assert fields == ([] if bad_id.strip() == "QF-001" else ["id"])


def test_finding_shape_defaults_to_semantic_kind_are_rejected():
    finding = {"id": "QF-003", "severity": "minor", "kind": "semantic"}
    result = run("qa_finding_shape_check", {"findings": [finding]})
    assert [v["field"] for v in result["violations"]] == ["kind"]


def test_finding_shape_script_tier_kinds_are_flagged():
    finding = {"id": "QF-004", "severity": "major", "kind": "e2e", "evidence": "no harness"}
    result = run("qa_finding_shape_check", {"findings": [finding]})
    assert [v["field"] for v in result["violations"]] == ["kind"]


def test_finding_shape_functional_needs_ac_ref_but_perf_does_not():
    functional = {"id": "QF-005", "severity": "minor", "kind": "functional"}
    perf = {"id": "QF-006", "severity": "minor", "kind": "perf"}
    result = run("qa_finding_shape_check", {"findings": [functional, perf]})
    assert [(v["id"], v["field"]) for v in result["violations"]] == [("QF-005", "ac_ref")]
    blank = run("qa_finding_shape_check", {"findings": [{**functional, "ac_ref": "  "}]})
    assert [v["field"] for v in blank["violations"]] == ["ac_ref"]


def test_finding_shape_evidence_is_required_from_major_up_not_for_minor():
    major = {"id": "QF-007", "severity": "major", "kind": "perf"}
    critical = {"id": "QF-008", "severity": "critical", "kind": "flake", "evidence": " "}
    minor = {"id": "QF-009", "severity": "minor", "kind": "perf"}
    result = run("qa_finding_shape_check", {"findings": [major, critical, minor]})
    assert [(v["id"], v["field"]) for v in result["violations"]] == [("QF-007", "evidence"), ("QF-008", "evidence")]


def test_finding_shape_unknown_severity_is_flagged_and_reports_every_problem():
    finding = {"id": "bogus", "severity": "blocker", "kind": "nope"}
    result = run("qa_finding_shape_check", {"findings": [finding]})
    assert sorted(v["field"] for v in result["violations"]) == ["id", "kind", "severity"]


def test_finding_shape_non_object_member_is_rejected_by_schema_and_flagged_by_the_handler():
    result = call("qa_finding_shape_check", {"findings": [GOOD, "QF-002"]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "findings"
    direct = finding_shape_check({"findings": [GOOD, "QF-002"]})
    assert [(v["index"], v["field"]) for v in direct["violations"]] == [(1, "finding")]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "findings"),
        ({"findings": "QF-001"}, "findings"),
        ({"findings": [GOOD] * 501}, "findings"),
        ({"findings": [], "extra": 1}, "extra"),
    ],
)
def test_finding_shape_rejects_malformed_or_oversized_input(payload, field):
    result = call("qa_finding_shape_check", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


# ---- qa_defect_dedupe -------------------------------------------------------------------------


def defect(defect_id: str, title: str = "Order total is wrong", severity: str = "major", **extra) -> dict:
    base = {"defect_id": defect_id, "suspected_component": "orders-service", "ac_ref": "AC-1", "title": title,
            "severity": severity}
    return {**base, **extra}


def test_defect_dedupe_merges_same_component_ac_and_normalized_title():
    defects = [defect("DEF-1"), defect("DEF-2", "  order TOTAL is wrong!! "), defect("DEF-3", "Other problem")]
    result = run("qa_defect_dedupe", {"defects": defects})
    assert result["unique_count"] == 2
    assert [g["members"] for g in result["groups"]] == [["DEF-1", "DEF-2"], ["DEF-3"]]
    assert result["duplicate_ids"] == ["DEF-2"]


def test_defect_dedupe_canonical_is_the_most_severe_ties_keep_the_first():
    defects = [defect("DEF-1", severity="minor"), defect("DEF-2", severity="critical"), defect("DEF-3", severity="critical")]
    group = run("qa_defect_dedupe", {"defects": defects})["groups"][0]
    assert group["canonical"] == "DEF-2" and group["max_severity"] == "critical"
    assert group["members"] == ["DEF-1", "DEF-2", "DEF-3"]


def test_defect_dedupe_explicit_key_overrides_the_derived_one():
    defects = [defect("DEF-1", "Alpha", dedupe_key="K-1"), defect("DEF-2", "Beta", ac_ref="AC-9", dedupe_key=" K-1 ")]
    result = run("qa_defect_dedupe", {"defects": defects})
    assert result["unique_count"] == 1 and result["groups"][0]["key"] == "K-1"
    assert result["duplicate_ids"] == ["DEF-2"]


def test_defect_dedupe_derived_key_is_stable_and_differs_by_component_and_ac():
    base = run("qa_defect_dedupe", {"defects": [defect("DEF-1")]})["groups"][0]["key"]
    again = run("qa_defect_dedupe", {"defects": [defect("DEF-9")]})["groups"][0]["key"]
    other_component = run("qa_defect_dedupe", {"defects": [defect("DEF-1", suspected_component="billing")]})["groups"][0]["key"]
    other_ac = run("qa_defect_dedupe", {"defects": [defect("DEF-1", ac_ref="AC-2")]})["groups"][0]["key"]
    assert base == again and len(base) == 16
    assert len({base, other_component, other_ac}) == 3


def test_defect_dedupe_component_case_and_ac_case_do_not_split_a_group():
    defects = [defect("DEF-1"), defect("DEF-2", suspected_component="ORDERS-Service", ac_ref="ac-1")]
    assert run("qa_defect_dedupe", {"defects": defects})["unique_count"] == 1


def test_defect_dedupe_empty_input_has_no_groups():
    result = run("qa_defect_dedupe", {"defects": []})
    assert result == {"state": "SUCCESS", "groups": [], "duplicate_ids": [], "unique_count": 0}


def test_defect_dedupe_empty_or_punctuation_only_title_is_invalid_input():
    assert call("qa_defect_dedupe", {"defects": [defect("DEF-1", "")]})["state"] == "INVALID_INPUT"
    result = call("qa_defect_dedupe", {"defects": [defect("DEF-1", " !!! ")]})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "defects"


def test_defect_dedupe_duplicate_defect_id_and_blank_key_are_invalid():
    result = call("qa_defect_dedupe", {"defects": [defect("DEF-1"), defect("DEF-1", "Another")]})
    assert result["state"] == "INVALID_INPUT" and "DEF-1" in result["message"]
    blank = call("qa_defect_dedupe", {"defects": [defect("DEF-1", dedupe_key="   ")]})
    assert blank["state"] == "INVALID_INPUT" and blank["field"] == "defects"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"defects": [{"defect_id": "DEF-1"}]},
        {"defects": [defect("DEF-1", severity="blocker")]},
        {"defects": [defect("DEF-1", title="x" * 301)]},
        {"defects": [defect(f"DEF-{i}") for i in range(1001)]},
    ],
)
def test_defect_dedupe_rejects_malformed_or_oversized_input(payload):
    result = call("qa_defect_dedupe", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "defects"


def test_defect_dedupe_spec_example_defect_report_shape():
    spec_example = {"defect_id": "DEF-210", "severity": "major", "ac_ref": "AC-1",
                    "suspected_component": "orders-service", "title": "Checkout fails", "dedupe_key": "run-9812-checkout"}
    result = run("qa_defect_dedupe", {"defects": [spec_example]})
    assert result["groups"] == [{"key": "run-9812-checkout", "canonical": "DEF-210", "members": ["DEF-210"], "max_severity": "major"}]
