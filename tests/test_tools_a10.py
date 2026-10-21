"""v2.2 A10 tools: sec_finding_dedup, sec_allowlist_check, sec_sbom_diff (behaviour + registry shape)."""
import copy
import json
from pathlib import Path

import pytest

from swarm import gates, tool_registry
from swarm.tools import a10_tools

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a10-security.tools.json"
NEW_TOOLS = ("sec_finding_dedup", "sec_allowlist_check", "sec_sbom_diff")


def f(fid, tool="semgrep", rule="r1", severity="major", cwe="CWE-89", location="a.py:1"):
    out = {"id": fid, "tool": tool, "rule": rule, "severity": severity}
    if cwe is not None:
        out["cwe"] = cwe
    if location is not None:
        out["location"] = location
    return out


def dedup(findings, **extra):
    return a10_tools.finding_dedup({"findings": findings, **extra})


# ---- sec_finding_dedup ---------------------------------------------------------------------------

def test_dedup_severity_scale_matches_the_gate():
    assert a10_tools._GATE_SEVERITIES == tuple(gates.SEVERITIES)


def test_dedup_same_cwe_and_location_merges_with_max_severity():
    result = dedup([f("A", "semgrep", severity="major"), f("B", "codeql", severity="critical")])
    assert result["group_count"] == 1 and result["finding_count"] == 2
    group = result["groups"][0]
    assert group["key"] == "CWE-89@a.py:1"
    assert group["severity"] == "critical"
    assert group["tools"] == ["codeql", "semgrep"] and group["ids"] == ["A", "B"]


def test_dedup_differing_severities_are_a_conflict_naming_each_tool():
    result = dedup([f("A", "semgrep", severity="major"), f("B", "codeql", severity="blocker")])
    assert result["conflicts"] == [{
        "key": "CWE-89@a.py:1",
        "severity": "blocker",
        "by_tool": [{"tool": "codeql", "severity": "blocker"}, {"tool": "semgrep", "severity": "major"}],
    }]


def test_dedup_equal_severities_are_not_a_conflict():
    result = dedup([f("A", "semgrep"), f("B", "codeql")])
    assert result["conflicts"] == []


def test_dedup_missing_cwe_or_location_never_merges():
    result = dedup([f("A", cwe=None), f("B", cwe=None), f("C", location=None), f("D", location="")])
    assert result["group_count"] == 4
    assert {g["key"] for g in result["groups"]} == {"unkeyed:A", "unkeyed:B", "unkeyed:C", "unkeyed:D"}


def test_dedup_cwe_case_and_whitespace_are_normalised():
    result = dedup([f("A", cwe="cwe-89"), f("B", cwe=" CWE-89 ", location=" a.py:1 ")])
    assert result["group_count"] == 1


def test_dedup_different_location_or_cwe_stays_separate():
    result = dedup([f("A"), f("B", location="a.py:2"), f("C", cwe="CWE-79")])
    assert result["group_count"] == 3


def test_dedup_is_order_independent_and_sorted_by_severity_then_key():
    items = [f("A", severity="minor", location="z"), f("B", severity="critical", location="b"),
             f("C", severity="critical", location="a"), f("D", severity="info", location="m")]
    forward = dedup(items)
    backward = dedup(list(reversed(items)))
    assert forward == backward
    assert [g["key"] for g in forward["groups"]] == ["CWE-89@a", "CWE-89@b", "CWE-89@z", "CWE-89@m"]


def test_dedup_group_by_rule_batches_a_flood():
    findings = [f(f"F{i}", rule="CVE-2024-0001", location=f"img{i}", severity="major") for i in range(40)]
    result = dedup(findings, group_by="rule")
    assert result["group_by"] == "rule" and result["group_count"] == 1
    assert len(result["groups"][0]["ids"]) == 40


def test_dedup_one_tool_with_two_severities_in_a_group_counts_its_highest_only():
    result = dedup([f("A", "semgrep", severity="minor"), f("B", "semgrep", severity="major")])
    assert result["groups"][0]["severity"] == "major"
    assert result["conflicts"] == []


def test_dedup_empty_list_is_a_valid_empty_result():
    assert dedup([]) == {"state": "SUCCESS", "group_by": "cwe_location", "finding_count": 0,
                         "group_count": 0, "groups": [], "conflicts": []}


@pytest.mark.parametrize("bad, field", [
    ([f("A", severity="high")], "findings"),
    ([f("A"), f("A")], "findings"),
    ([f("  ")], "findings"),
    ([f("A", tool=" ")], "findings"),
    ([f("A", rule=" ")], "findings"),
])
def test_dedup_malformed_findings_name_the_field(bad, field):
    result = dedup(bad)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_dedup_rejects_unknown_group_by():
    assert dedup([f("A")], group_by="file")["field"] == "group_by"


def test_dedup_probe_is_answered_before_the_work():
    assert dedup([], probe="partial")["state"] == "PARTIAL_SUCCESS"
    with pytest.raises(OSError):
        dedup([f("A", severity="bogus")], probe="dependency")


# ---- sec_allowlist_check -------------------------------------------------------------------------

def allow(entries, finding_ids=(), as_of="2026-10-19"):
    return a10_tools.allowlist_check({"entries": entries, "finding_ids": list(finding_ids), "as_of": as_of})


def test_allowlist_expiry_before_on_and_after_as_of():
    entries = [
        {"id": "before", "justification": "j", "expires": "2026-10-18"},
        {"id": "on", "justification": "j", "expires": "2026-10-19"},
        {"id": "after", "justification": "j", "expires": "2026-10-20"},
    ]
    result = allow(entries)
    assert result["expired"] == ["before", "on"]
    assert result["active"] == ["after"]


def test_allowlist_blank_or_missing_justification_is_unjustified_and_invalid():
    entries = [{"id": "a", "justification": ""}, {"id": "b", "justification": "   "}, {"id": "c"},
               {"id": "d", "justification": "ok", "expires": "2027-01-01"}]
    result = allow(entries, ["a", "b", "c", "d"])
    assert result["unjustified"] == ["a", "b", "c"]
    assert result["valid"] is False
    assert result["suppressed_ids"] == ["d"]


def test_allowlist_unjustified_wins_over_expiry():
    result = allow([{"id": "a", "justification": "", "expires": "2020-01-01"}])
    assert result["unjustified"] == ["a"] and result["expired"] == []


def test_allowlist_suppresses_only_active_entries_for_present_findings():
    entries = [{"id": "keep", "justification": "j", "expires": "2027-01-01"},
               {"id": "old", "justification": "j", "expires": "2026-01-01"},
               {"id": "gone", "justification": "j", "expires": "2027-01-01"}]
    result = allow(entries, ["keep", "old", "other"])
    assert result["suppressed_ids"] == ["keep"]
    assert result["unused"] == ["gone"]
    assert result["valid"] is True


def test_allowlist_entry_without_expiry_is_active_and_flagged():
    result = allow([{"id": "a", "justification": "j"}, {"id": "b", "justification": "j", "expires": ""}], ["a", "b"])
    assert result["active"] == ["a", "b"] and result["no_expiry"] == ["a", "b"]
    assert result["suppressed_ids"] == ["a", "b"]


@pytest.mark.parametrize("bad_day", ["2026-13-01", "2026-1-5", "tomorrow", "2026-02-30"])
def test_allowlist_bad_expires_date_is_invalid_input(bad_day):
    result = allow([{"id": "a", "justification": "j", "expires": bad_day}])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "entries"


@pytest.mark.parametrize("bad_day", ["2026-10-1", "19/10/2026", "2026-02-30"])
def test_allowlist_bad_as_of_is_invalid_input(bad_day):
    result = allow([], as_of=bad_day)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "as_of"


def test_allowlist_duplicate_ids_are_ambiguous():
    result = allow([{"id": "a", "justification": "j"}, {"id": "a", "justification": "k"}])
    assert result["state"] == "INVALID_INPUT" and "duplicate" in result["message"]


def test_allowlist_empty_entries_is_valid_and_suppresses_nothing():
    result = allow([], ["x"])
    assert result["valid"] is True and result["suppressed_ids"] == [] and result["entry_count"] == 0


def test_allowlist_echoes_the_as_of_date_it_used():
    assert allow([])["as_of"] == "2026-10-19"


# ---- sec_sbom_diff -------------------------------------------------------------------------------

def diff(before, after):
    return a10_tools.sbom_diff({"before": before, "after": after})


def c(name, version=None):
    return {"name": name} if version is None else {"name": name, "version": version}


def test_sbom_diff_identical_lists_have_no_drift():
    items = [c("a", "1.0.0"), c("b", "2.0.0")]
    result = diff(items, list(reversed(items)))
    assert result["drift"] is False
    assert (result["added"], result["removed"], result["changed"], result["newly_unpinned"]) == ([], [], [], [])


def test_sbom_diff_reports_added_removed_and_changed():
    result = diff([c("a", "1.0.0"), c("gone", "1.0.0"), c("b", "2.0.0")], [c("a", "1.1.0"), c("b", "2.0.0"), c("new", "0.1.0")])
    assert result["added"] == [{"name": "new", "version": "0.1.0"}]
    assert result["removed"] == [{"name": "gone", "version": "1.0.0"}]
    assert result["changed"] == [{"name": "a", "from": "1.0.0", "to": "1.1.0"}]
    assert result["drift"] is True
    assert (result["before_count"], result["after_count"]) == (3, 3)


def test_sbom_diff_a_rename_is_remove_plus_add():
    result = diff([c("old-name", "1.0.0")], [c("new-name", "1.0.0")])
    assert [x["name"] for x in result["removed"]] == ["old-name"]
    assert [x["name"] for x in result["added"]] == ["new-name"]


def test_sbom_diff_pinned_to_range_is_newly_unpinned():
    result = diff([c("flask", "3.0.0")], [c("flask", "^3.0.0")])
    assert result["newly_unpinned"] == ["flask"]
    assert result["changed"] == [{"name": "flask", "from": "3.0.0", "to": "^3.0.0"}]


def test_sbom_diff_missing_version_after_pinned_is_newly_unpinned():
    assert diff([c("x", "1.2.3")], [c("x")])["newly_unpinned"] == ["x"]


def test_sbom_diff_unpinned_before_and_after_is_not_new():
    assert diff([c("x", "latest")], [c("x", "*")])["newly_unpinned"] == []


def test_sbom_diff_added_unpinned_component_counts_as_newly_unpinned():
    assert diff([], [c("fresh", "latest")])["newly_unpinned"] == ["fresh"]


def test_sbom_diff_prerelease_and_build_metadata_stay_pinned():
    result = diff([c("x", "1.0.0")], [c("x", "1.0.0-rc.1+build5")])
    assert result["newly_unpinned"] == []


def test_sbom_diff_whitespace_in_names_and_versions_is_ignored():
    assert diff([c(" a ", "1.0.0 ")], [c("a", "1.0.0")])["drift"] is False


def test_sbom_diff_both_lists_may_be_empty():
    result = diff([], [])
    assert result["drift"] is False and result["before_count"] == 0


@pytest.mark.parametrize("before, after, field", [
    ([c("a"), c("a")], [], "before"),
    ([], [c("a", "1"), c(" a ", "2")], "after"),
    ([c("  ")], [], "before"),
    ([], [c("")], "after"),
])
def test_sbom_diff_blank_or_duplicate_names_name_the_list(before, after, field):
    result = diff(before, after)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


# ---- registry shape ------------------------------------------------------------------------------

def _fragment():
    """This unit's new tool entries: the intake fragment before the merge, the merged registry entries after it."""
    if FRAGMENT.is_file():
        return json.loads(FRAGMENT.read_text(encoding="utf-8"))
    agent = next(a for a in tool_registry.load_document()["agents"] if a["id"] == "A10")
    held = {t["name"]: t for t in agent["registered_tools"]}
    return [held[name] for name in NEW_TOOLS if name in held]


def _registry_with_fragment():
    document = copy.deepcopy(tool_registry.load_document())
    agent = next(a for a in document["agents"] if a["id"] == "A10")
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(t) for t in _fragment() if t["name"] not in present)
    return document


def test_fragment_entries_validate_in_the_registry(tmp_path):
    document = _registry_with_fragment()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    assert tool_registry.validate_registry(path, root=ROOT) == []


def test_new_tools_are_exactly_the_three_read_tools():
    entries = _fragment()
    assert tuple(t["name"] for t in entries) == NEW_TOOLS
    for tool in entries:
        assert tool["owner"] == "A10" and tool["permitted_callers"] == ["A10"]
        assert tool["side_effect_tier"] == "READ" and tool["idempotency"] == "idempotent"
        assert tool["handler"] == f"swarm.tools.a10_tools:{tool['name'][4:]}"
        assert "|" not in tool["purpose"]


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_fragment_examples_run_through_call_tool(name):
    document = _registry_with_fragment()
    tool = tool_registry.find_tool(document, name)
    examples = tool["examples"]

    def call(caller, payload, **kw):
        return tool_registry.call_tool(name, caller, payload, data=document, **kw)

    assert call("A10", examples["success"])["state"] == "SUCCESS"
    assert call("A10", examples["invalid_input"])["state"] == "INVALID_INPUT"
    assert call(examples["unauthorized_caller"], examples["success"])["state"] == "UNAUTHORIZED"
    assert call("A10", examples["dependency"])["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call("A10", examples["timeout"], timeout_s=0.05)["state"] == "TIMEOUT"
    partial = call("A10", examples["partial"])
    assert partial["state"] == "PARTIAL_SUCCESS" and partial["completed_fraction"] == 0.5


def test_success_examples_exercise_the_rules_they_advertise():
    by_name = {t["name"]: t["examples"]["success"] for t in _fragment()}
    dedup_result = a10_tools.finding_dedup(by_name["sec_finding_dedup"])
    assert dedup_result["group_count"] == 2 and len(dedup_result["conflicts"]) == 1
    allow_result = a10_tools.allowlist_check(by_name["sec_allowlist_check"])
    assert allow_result["suppressed_ids"] == ["SF-1a2b"] and allow_result["expired"] == ["SF-3c4d"]
    assert allow_result["unjustified"] == ["SF-5e6f"]
    diff_result = a10_tools.sbom_diff(by_name["sec_sbom_diff"])
    assert diff_result["newly_unpinned"] == ["flask"] and diff_result["drift"] is True
