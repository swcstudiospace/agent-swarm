"""Phase 4 shared pool: multi-caller tools, no name collisions."""
import json
from pathlib import Path

from swarm.tool_registry import call_tool

ROOT = Path(__file__).resolve().parent.parent


def _registry():
    return json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))


def test_shared_tools_have_two_or_more_callers_and_unique_names():
    data = _registry()
    names = []
    for agent in data["agents"]:
        names.extend(tool["name"] for tool in agent["registered_tools"])
    shared = data["shared_tools"]
    assert shared, "expected a non-empty shared pool"
    for tool in shared:
        assert tool["owner"] == "shared"
        assert tool["name"].startswith("shared_")
        assert len(tool["permitted_callers"]) >= 2
        names.append(tool["name"])
    assert len(names) == len(set(names))


def test_shared_verdict_matches_gate_rule():
    assert call_tool("shared_verdict", "A08", {"severities": ["info"]})["verdict"] == "pass"
    assert call_tool("shared_verdict", "A10", {"severities": ["major"]})["verdict"] == "fail"


def test_shared_toolchain_and_check_plan_see_this_repo():
    flags = call_tool("shared_toolchain", "A05", {"scan": True})
    assert flags["python"] is True
    plan = call_tool("shared_check_plan", "A06", {"scan": True})
    assert plan["state"] == "SUCCESS" and "pytest" in plan["names"]


def test_shared_git_head_is_a_sha():
    head = call_tool("shared_git_head", "A11", {"scan": True})
    assert head["state"] == "SUCCESS"
    assert len(head["sha"]) == 40
    assert head["branch"]
