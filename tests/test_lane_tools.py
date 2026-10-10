"""Behavioral checks for the Phase 3 lane tools. Generated tests only assert the terminal state."""
import json
from pathlib import Path

from swarm.tool_registry import call_tool

ROOT = Path(__file__).resolve().parent.parent


def test_every_agent_has_one_to_eight_lane_tools():
    data = json.loads((ROOT / "agents.json").read_text(encoding="utf-8"))
    assert len(data["agents"]) == 15
    for agent in data["agents"]:
        tools = agent["registered_tools"]
        assert 1 <= len(tools) <= 8, agent["id"]
        assert all(tool["owner"] == agent["id"] for tool in tools)


def test_dag_order_is_dependency_order():
    result = call_tool(
        "orch_dag_order", "A01",
        {"nodes": [{"id": "a", "depends_on": []}, {"id": "b", "depends_on": ["a"]}]},
    )
    assert result == {"state": "SUCCESS", "order": "a,b"}


def test_budget_class_matches_orch_plan_low():
    result = call_tool("orch_budget_class", "A01", {"risk": "low"})
    assert result["state"] == "SUCCESS"
    assert result["max_wall_s"] == 900
    assert result["max_cost_usd"] == 3


def test_gwt_check_counts_a_complete_criterion():
    text = "AC-1 Given a user When they ping Then they see pong"
    result = call_tool("req_gwt_check", "A02", {"text": text})
    assert result == {"state": "SUCCESS", "criteria": 1, "missing_gwt": 0}


def test_adr_slug_and_contract_ops():
    assert call_tool("arch_adr_slug", "A03", {"title": "Use Postgres"})["slug"] == "use-postgres"
    ops = call_tool("arch_contract_ops", "A03", {"document": {"openapi": "3.0.3", "paths": {"/ping": {"get": {}}}}})
    assert ops == {"state": "SUCCESS", "operations": 1}


def test_contrast_and_route_and_a11y():
    assert call_tool("ux_contrast", "A04", {"fg": "#000000", "bg": "#ffffff"})["ratio"] == 21.0
    assert call_tool("be_route_norm", "A05", {"path": "/users/:id/"})["normalized"] == "/users/{}"
    assert call_tool("fe_a11y_scan", "A06", {"html": "<button>Save</button>"})["issue_count"] == 0


def test_migration_review_security_and_runners():
    assert call_tool("data_down_present", "A07", {"sql": "def downgrade():\n    pass"})["reversible"] is True
    runners = call_tool("qa_runner_detect", "A08", {"scan": True})
    assert runners["has_pytest"] is True and runners["runner_count"] >= 1
    assert call_tool("rev_diff_size", "A09", {"added": 10, "deleted": 2, "max_lines": 400})["over_budget"] is False
    entropy = call_tool("sec_line_entropy", "A10", {"text": "aabbccdd"})
    assert entropy["state"] == "SUCCESS" and entropy["entropy"] > 0


def test_devops_release_obs_maint_docs():
    assert call_tool("devops_ci_kind", "A11", {"rel": ".github/workflows/ci.yml"})["kind"] == "gha"
    systems = call_tool("devops_build_systems", "A11", {"scan": True})
    assert systems["state"] == "SUCCESS" and "python-pyproject" in systems["systems"]
    plan = call_tool("rel_canary_steps", "A12", {"release_id": "r1", "risk": "low"})
    assert plan["steps_pct"] == "5,25,50,100" and plan["approval"] == "L2:auto"
    burn = call_tool("obs_error_burn", "A13", {"good": 90, "total": 100, "objective": 0.99})
    assert burn["sli"] == 0.9 and abs(burn["burn_rate"] - 10) < 1e-9
    assert call_tool("maint_spec_pin", "A14", {"spec": "==1.2.3"})["pin"] == "pinned"
    headings = call_tool("docs_heading_order", "A15", {"markdown": "# Title\n\n## Section"})
    assert headings == {"state": "SUCCESS", "h1": 1, "skips": 0}
