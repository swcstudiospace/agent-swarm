"""Behavior of the v2.2 A09 tools: rev_risk_tier, rev_verdict_shape, rev_rule_dispute_flag.

Handlers are pure, so most cases call them directly. The last tests check the intake fragment against the live
registry checker, on a copy that only adds the entries the document does not hold yet (valid before and after the merge).
"""
import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tools import a09_tools

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a09-reviewer.tools.json"
NAMES = ("rev_risk_tier", "rev_verdict_shape", "rev_rule_dispute_flag")


# ---- rev_risk_tier -----------------------------------------------------------------------------------------------
def tier(**over):
    body = {"categories": [], "diff_lines": 0, "blocking_count": 0, "max_severity": "none", "co_sign_required": False}
    body.update(over)
    return a09_tools.risk_tier(body)


def test_risk_tier_clean_small_diff_is_low():
    result = tier(diff_lines=20, max_severity="minor")
    assert result == {"state": "SUCCESS", "risk_tier": "low", "reasons": ["no_risk_trigger"]}


@pytest.mark.parametrize("lines,expected", [(0, "low"), (99, "low"), (100, "medium"), (101, "medium")])
def test_risk_tier_diff_size_boundary_matches_auto_approve_threshold(lines, expected):
    assert tier(diff_lines=lines)["risk_tier"] == expected
    # the same boundary as auto_approve_check: below 100 lines may auto-approve, 100 may not
    auto = a09_tools.auto_approve_check({"added": lines, "deleted": 0, "changed_paths": [], "sast_findings": 0,
                                         "tests_present": True, "first_pass_pct": 95})
    assert auto["auto_approve"] is (expected == "low")


@pytest.mark.parametrize("category", ["contracts", "migrations"])
def test_risk_tier_non_cosign_category_is_medium(category):
    result = tier(categories=[category])
    assert result["risk_tier"] == "medium" and result["reasons"] == [f"category:{category}"]


@pytest.mark.parametrize("category", ["auth", "payments", "pii", "iac"])
def test_risk_tier_cosign_category_is_high_when_cosign_required(category):
    result = tier(categories=[category], co_sign_required=True)
    assert result["risk_tier"] == "high"
    assert result["reasons"] == ["co_sign_required", f"category:{category}"]


def test_risk_tier_cosign_category_without_cosign_is_rejected():
    # rev_sensitive_paths always sets co_sign_required for these; a caller that dropped it would under-rate the PR
    result = tier(categories=["payments"], co_sign_required=False)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "co_sign_required"


@pytest.mark.parametrize("severity,count,expected", [
    ("none", 0, "low"), ("info", 0, "low"), ("minor", 0, "low"),
    ("major", 1, "medium"), ("critical", 1, "high"), ("blocker", 3, "high"),
])
def test_risk_tier_severity_ladder(severity, count, expected):
    assert tier(max_severity=severity, blocking_count=count)["risk_tier"] == expected


def test_risk_tier_blocking_count_alone_is_medium():
    result = tier(max_severity="major", blocking_count=2)
    assert result["risk_tier"] == "medium" and result["reasons"] == ["blocking_count:2>0"]


def test_risk_tier_reasons_list_every_trigger_high_first():
    result = tier(categories=["auth", "contracts"], diff_lines=150, blocking_count=1, max_severity="blocker",
                  co_sign_required=True)
    assert result["risk_tier"] == "high"
    assert result["reasons"] == ["co_sign_required", "max_severity:blocker", "category:auth", "category:contracts",
                                 "diff_lines:150>=100", "blocking_count:1>0"]


def test_risk_tier_prompt_sequence_one_diff_is_medium_by_size():
    # prompts/A09-reviewer.md Sequence 1: added 120 + deleted 30 with no blocking finding
    assert tier(diff_lines=150, max_severity="info")["risk_tier"] == "medium"


def test_risk_tier_duplicate_categories_count_once():
    assert tier(categories=["contracts", "contracts"])["reasons"] == ["category:contracts"]


def test_risk_tier_unknown_category_is_invalid_input():
    result = tier(categories=["secrets"])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "categories"


@pytest.mark.parametrize("severity,count,field", [("major", 0, "blocking_count"), ("minor", 1, "max_severity")])
def test_risk_tier_inconsistent_severity_and_blocking_count(severity, count, field):
    result = tier(max_severity=severity, blocking_count=count)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


# ---- rev_verdict_shape -------------------------------------------------------------------------------------------
def golden(**over):
    block = {
        "gate": "review", "task_id": "T-884", "correlation_id": "C-884", "pr": "512", "verdict": "fail",
        "blocking": [{"file": "svc/orders/handler.go", "line": 88, "rule": "ARCH.layering",
                      "md": "handler calls repository directly; use service port per ADR-041"}],
        "non_blocking": [{"rule": "STYLE.naming", "suggestion_md": "Rename tmp."}],
        "risk_tier": "medium", "co_sign_required": False, "mode": "rules+semantic", "expires_s": 172800,
    }
    block.update(over)
    return block


def shape(block):
    return a09_tools.verdict_shape({"block": block})


def fields(result):
    return [error["field"] for error in result["errors"]]


def test_verdict_shape_prompt_block_is_valid():
    assert shape(golden()) == {"state": "SUCCESS", "valid": True, "errors": [], "normalized_verdict": "fail"}


def test_verdict_shape_pass_with_empty_lists_is_valid():
    result = shape(golden(verdict="pass", blocking=[], non_blocking=[], risk_tier="low"))
    assert result["valid"] is True and result["normalized_verdict"] == "pass"


@pytest.mark.parametrize("alias,expected", [
    ("pass", "pass"), ("approve", "pass"), ("fail", "fail"), ("request_changes", "fail"), ("block", "fail"),
])
def test_verdict_shape_alias_map(alias, expected):
    block = golden(verdict=alias)
    if expected == "pass":
        block["blocking"] = []
    result = shape(block)
    assert result["valid"] is True and result["normalized_verdict"] == expected


@pytest.mark.parametrize("bad", ["APPROVE", "approved", "", 3, None])
def test_verdict_shape_unknown_verdict(bad):
    result = shape(golden(verdict=bad))
    assert result["valid"] is False and result["normalized_verdict"] == "none"
    assert "verdict" in fields(result)


@pytest.mark.parametrize("key", [
    "gate", "task_id", "correlation_id", "verdict", "blocking", "non_blocking", "risk_tier", "mode",
    "co_sign_required", "expires_s",
])
def test_verdict_shape_each_missing_field_is_reported(key):
    block = golden()
    del block[key]
    result = shape(block)
    assert result["valid"] is False
    assert {"field": key, "reason": "missing"} in result["errors"]


@pytest.mark.parametrize("key", ["file", "line", "rule", "md"])
def test_verdict_shape_blocking_item_needs_file_line_rule_md(key):
    block = golden()
    del block["blocking"][0][key]
    assert {"field": f"blocking[0].{key}", "reason": "missing"} in shape(block)["errors"]


@pytest.mark.parametrize("key", ["rule", "suggestion_md"])
def test_verdict_shape_non_blocking_item_needs_rule_and_suggestion(key):
    block = golden()
    del block["non_blocking"][0][key]
    assert {"field": f"non_blocking[0].{key}", "reason": "missing"} in shape(block)["errors"]


@pytest.mark.parametrize("over,field", [
    ({"gate": "security"}, "gate"),
    ({"task_id": ""}, "task_id"),
    ({"correlation_id": 7}, "correlation_id"),
    ({"pr": 512}, "pr"),
    ({"risk_tier": "critical"}, "risk_tier"),
    ({"mode": "semantic"}, "mode"),
    ({"co_sign_required": "false"}, "co_sign_required"),
    ({"co_sign_required": 0}, "co_sign_required"),
    ({"expires_s": "172800"}, "expires_s"),
    ({"expires_s": True}, "expires_s"),
    ({"blocking": "none"}, "blocking"),
    ({"non_blocking": {}}, "non_blocking"),
])
def test_verdict_shape_wrong_types_and_values(over, field):
    result = shape(golden(**over))
    assert result["valid"] is False and field in fields(result)


@pytest.mark.parametrize("seconds,ok", [(1, True), (172800, True), (172801, False), (0, False), (-5, False)])
def test_verdict_shape_expiry_boundary(seconds, ok):
    assert shape(golden(expires_s=seconds))["valid"] is ok


@pytest.mark.parametrize("line,ok", [(1, True), (0, False), (-1, False), (True, False), ("88", False), (8.5, False)])
def test_verdict_shape_blocking_line_must_be_positive_integer(line, ok):
    block = golden()
    block["blocking"][0]["line"] = line
    assert shape(block)["valid"] is ok


def test_verdict_shape_pass_cannot_carry_blocking_items():
    result = shape(golden(verdict="approve"))
    assert result["valid"] is False and "verdict" in fields(result)


def test_verdict_shape_fail_needs_a_blocking_item():
    result = shape(golden(blocking=[]))
    assert result["valid"] is False and {"field": "blocking", "reason": "a fail verdict needs at least one blocking item"} in result["errors"]


def test_verdict_shape_reports_every_error_not_just_the_first():
    result = shape({"gate": "review"})
    assert result["valid"] is False and len(result["errors"]) >= 8


def test_verdict_shape_non_object_item_is_reported():
    block = golden(blocking=["file.py:1"])
    assert {"field": "blocking[0]", "reason": "expected an object"} in shape(block)["errors"]


def test_verdict_shape_oversized_block_is_invalid_input():
    item = {"rule": "STYLE.naming", "suggestion_md": "x"}
    result = shape(golden(non_blocking=[item] * 2001))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "block.non_blocking"
    assert shape(golden(non_blocking=[item] * 2000))["valid"] is True


def test_verdict_shape_extra_fields_are_allowed():
    assert shape(golden(summary_md="ok"))["valid"] is True


# ---- rev_rule_dispute_flag ---------------------------------------------------------------------------------------
def dispute(rule="STYLE.naming", pr="512", producer="A05"):
    return {"rule": rule, "pr": pr, "producer": producer}


def flag(disputes, **extra):
    return a09_tools.rule_dispute_flag({"disputes": disputes, **extra})


def test_dispute_flag_one_dispute_is_not_flagged_two_are():
    one = flag([dispute()])
    assert one == {"state": "SUCCESS", "flagged_rules": [], "flagged_count": 0, "max_disputes": 1}
    two = flag([dispute(pr="512"), dispute(pr="519")])
    assert two["flagged_count"] == 1 and two["max_disputes"] == 2
    assert two["flagged_rules"] == [{"rule": "STYLE.naming", "producer": "A05", "dispute_count": 2, "prs": ["512", "519"]}]


def test_dispute_flag_same_rule_across_producers_is_not_merged():
    result = flag([dispute(producer="A05", pr="1"), dispute(producer="A06", pr="2")])
    assert result["flagged_count"] == 0 and result["max_disputes"] == 1


def test_dispute_flag_each_producer_is_flagged_separately():
    items = [dispute(producer=p, pr=pr) for p in ("A05", "A06") for pr in ("1", "2")]
    result = flag(items)
    assert [(r["producer"], r["dispute_count"]) for r in result["flagged_rules"]] == [("A05", 2), ("A06", 2)]
    assert result["flagged_count"] == 2


def test_dispute_flag_duplicate_rule_pr_producer_counts_once():
    result = flag([dispute(), dispute(), dispute()])
    assert result["flagged_count"] == 0 and result["max_disputes"] == 1


def test_dispute_flag_threshold_is_configurable():
    items = [dispute(pr=str(n)) for n in range(3)]
    assert flag(items, threshold=3)["flagged_count"] == 1
    assert flag(items, threshold=4)["flagged_count"] == 0
    assert flag([dispute()], threshold=1)["flagged_count"] == 1


def test_dispute_flag_different_rules_are_counted_apart():
    items = [dispute(rule="A.one", pr="1"), dispute(rule="B.two", pr="2"), dispute(rule="A.one", pr="3")]
    result = flag(items)
    assert [r["rule"] for r in result["flagged_rules"]] == ["A.one"]
    assert result["max_disputes"] == 2


def test_dispute_flag_empty_list_flags_nothing():
    assert flag([]) == {"state": "SUCCESS", "flagged_rules": [], "flagged_count": 0, "max_disputes": 0}


def test_dispute_flag_output_is_ordered_and_input_order_independent():
    items = [dispute(rule="Z.last", pr="9"), dispute(rule="A.first", pr="2"), dispute(rule="A.first", pr="1"),
             dispute(rule="Z.last", pr="3")]
    result = flag(items)
    assert [r["rule"] for r in result["flagged_rules"]] == ["A.first", "Z.last"]
    assert result["flagged_rules"][0]["prs"] == ["1", "2"]
    assert flag(list(reversed(items))) == result


def test_dispute_flag_blank_field_is_invalid_input():
    result = flag([dispute(rule="   ")])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "disputes"


def test_dispute_flag_rule_ids_are_exact_not_case_folded():
    result = flag([dispute(rule="STYLE.naming", pr="1"), dispute(rule="style.naming", pr="2")])
    assert result["flagged_count"] == 0


# ---- registry contract -------------------------------------------------------------------------------------------
def fragment():
    """This unit's new tool entries: the intake fragment before the merge, the merged registry entries after it."""
    if FRAGMENT.is_file():
        return json.loads(FRAGMENT.read_text(encoding="utf-8"))
    a09 = next(agent for agent in tool_registry.load_document()["agents"] if agent["id"] == "A09")
    held = {tool["name"]: tool for tool in a09["registered_tools"]}
    return [held[name] for name in NAMES if name in held]


def merged_copy():
    data = copy.deepcopy(tool_registry.load_document())
    a09 = next(agent for agent in data["agents"] if agent["id"] == "A09")
    held = {tool["name"] for _container, tool in tool_registry.iter_entries(data)}
    a09["registered_tools"].extend(copy.deepcopy(tool) for tool in fragment() if tool["name"] not in held)
    return data


def test_new_tools_are_the_three_read_tools_owned_by_a09():
    tools = fragment()
    assert [t["name"] for t in tools] == list(NAMES)
    for tool in tools:
        assert tool["owner"] == "A09" and tool["permitted_callers"] == ["A09"]
        assert tool["side_effect_tier"] == "READ" and tool["idempotency"] == "idempotent"
        assert tool["handler"] == f"swarm.tools.a09_tools:{tool['handler'].split(':')[1]}"
        assert set(tool["examples"]) == {"success", "invalid_input", "unauthorized_caller", "dependency", "timeout", "partial"}
        assert "probe" in tool["input_schema"]["properties"] and "|" not in tool["purpose"]


def test_merged_registry_copy_validates_without_errors(tmp_path):
    data = merged_copy()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    problems = [e for e in tool_registry.validate_registry(path, root=ROOT) if any(n in e for n in NAMES) or "A09" in e]
    assert problems == []


def test_every_example_behaves_as_declared_through_call_tool():
    data = merged_copy()
    by_name = {t["name"]: t for t in fragment()}
    for name, tool in by_name.items():
        ex = tool["examples"]
        assert tool_registry.call_tool(name, "A09", ex["success"], data=data)["state"] == "SUCCESS", name
        assert tool_registry.call_tool(name, "A09", ex["invalid_input"], data=data)["state"] == "INVALID_INPUT", name
        assert tool_registry.call_tool(name, ex["unauthorized_caller"], ex["success"], data=data)["state"] == "UNAUTHORIZED", name
        assert tool_registry.call_tool(name, "A09", ex["dependency"], data=data)["state"] == "DEPENDENCY_UNAVAILABLE", name
        assert tool_registry.call_tool(name, "A09", ex["partial"], data=data)["state"] == "PARTIAL_SUCCESS", name


def test_call_tool_rejects_values_outside_the_schemas():
    data = merged_copy()
    bad_tier = {"categories": ["secrets"], "diff_lines": 1, "blocking_count": 0, "max_severity": "none", "co_sign_required": False}
    assert tool_registry.call_tool("rev_risk_tier", "A09", bad_tier, data=data)["state"] == "INVALID_INPUT"
    assert tool_registry.call_tool("rev_risk_tier", "A09", {**bad_tier, "categories": [], "diff_lines": -1}, data=data)["state"] == "INVALID_INPUT"
    assert tool_registry.call_tool("rev_rule_dispute_flag", "A09", {"disputes": [], "threshold": 0}, data=data)["state"] == "INVALID_INPUT"
    assert tool_registry.call_tool("rev_verdict_shape", "A09", {"block": "nope"}, data=data)["state"] == "INVALID_INPUT"
