"""Behaviour of the v2.2 A01 tools: orch_dispatch_batch and orch_budget_guard.

The tools are read from the loaded registry document, plus the intake fragment while it is still unmerged, so the
suite passes before and after `scripts/_registry_intake.py --apply`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.blast_radius import take_agent_slot
from swarm.tool_registry import call_tool

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a01-orchestrator.tools.json"
NEW_TOOLS = ("orch_dispatch_batch", "orch_budget_guard")


def _document() -> dict:
    """The loaded registry with the fragment's entries added when they are not merged yet."""
    doc = copy.deepcopy(tool_registry.load_document())
    agent = next(a for a in doc["agents"] if a["id"] == "A01")
    present = {t["name"] for t in agent["registered_tools"]}
    if FRAGMENT.exists():
        for entry in json.loads(FRAGMENT.read_text(encoding="utf-8")):
            if entry["name"] not in present:
                agent["registered_tools"].append(entry)
    return doc


@pytest.fixture(scope="module")
def doc() -> dict:
    return _document()


def dispatch(doc: dict, **kw) -> dict:
    return call_tool("orch_dispatch_batch", "A01", kw, data=doc)


def guard(doc: dict, **kw) -> dict:
    return call_tool("orch_budget_guard", "A01", kw, data=doc)


def task(tid: str, agent: str, priority: int | None = None) -> dict:
    row = {"id": tid, "agent_id": agent}
    if priority is not None:
        row["priority"] = priority
    return row


BUDGET = {"max_tokens": 300000, "max_wall_s": 1800, "max_cost_usd": 8}


def usage(tokens=0, wall_s=0, cost_usd=0) -> dict:
    return {"tokens": tokens, "wall_s": wall_s, "cost_usd": cost_usd}


# --- registry ---------------------------------------------------------------------------------------------------


def test_fragment_entries_validate_in_a_copy_of_the_registry(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    merged_path = tmp_path / "merged.json"
    baseline_path.write_text(json.dumps(tool_registry.load_document()), encoding="utf-8")
    merged_path.write_text(json.dumps(_document()), encoding="utf-8")
    baseline = tool_registry.validate_registry(baseline_path)
    merged = tool_registry.validate_registry(merged_path)
    assert merged == baseline  # adding the A01 entries introduces no error
    assert not [e for e in merged if any(name in e for name in NEW_TOOLS)]


def test_registry_ping_stays_first_and_new_tools_follow(doc):
    names = [t["name"] for t in next(a for a in doc["agents"] if a["id"] == "A01")["registered_tools"]]
    assert names[0] == "orch_registry_ping"
    assert all(name in names for name in NEW_TOOLS)
    assert names.index("orch_dispatch_batch") > names.index("orch_rework_bound")


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_contract_examples_resolve_to_their_states(doc, name):
    tool = next(t for a in doc["agents"] for t in a["registered_tools"] if t["name"] == name)
    ex = tool["examples"]
    assert tool["permitted_callers"] == ["A01"] and tool["side_effect_tier"] == "READ"
    assert call_tool(name, "A01", ex["success"], data=doc)["state"] == "SUCCESS"
    assert call_tool(name, "A01", ex["invalid_input"], data=doc)["state"] == "INVALID_INPUT"
    assert call_tool(name, ex["unauthorized_caller"], ex["success"], data=doc)["state"] == "UNAUTHORIZED"
    assert call_tool(name, "A01", ex["dependency"], data=doc)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call_tool(name, "A01", ex["timeout"], data=doc)["state"] == "TIMEOUT"
    assert call_tool(name, "A01", ex["partial"], data=doc)["state"] == "PARTIAL_SUCCESS"


# --- orch_dispatch_batch ----------------------------------------------------------------------------------------


def test_dispatch_starts_everything_under_the_ceilings(doc):
    out = dispatch(doc, ready=[task("a", "A05"), task("b", "A06")], running={}, ceilings={"A05": 2, "A06": 2}, global_cap=4)
    assert out["state"] == "SUCCESS"
    assert out["start"] == ["a", "b"] and out["deferred"] == []
    assert out["slots"] == {"A05": 1, "A06": 1} and out["global_free"] == 2


def test_dispatch_same_agent_runs_up_to_its_ceiling(doc):
    """Blast-radius lanes: one agent may take several slots, never more than its max_parallel (prompts/A01 blast_radius)."""
    ready = [task(f"lane{i}", "A05") for i in range(4)]
    out = dispatch(doc, ready=ready, running={}, ceilings={"A05": 3}, global_cap=8)
    assert out["start"] == ["lane0", "lane1", "lane2"]
    assert out["deferred"] == [{"id": "lane3", "reason": "agent_ceiling"}]
    assert out["slots"] == {"A05": 0}


def test_dispatch_counts_tasks_already_running(doc):
    out = dispatch(doc, ready=[task("x", "A05"), task("y", "A05")], running={"A05": 2}, ceilings={"A05": 3}, global_cap=8)
    assert out["start"] == ["x"]
    assert out["deferred"] == [{"id": "y", "reason": "agent_ceiling"}]


def test_dispatch_global_cap_includes_running_of_every_agent(doc):
    out = dispatch(
        doc,
        ready=[task("a", "A05"), task("b", "A06"), task("c", "A07")],
        running={"A09": 3},  # an agent with no ready task still occupies the global cap
        ceilings={"A05": 2, "A06": 2, "A07": 2},
        global_cap=4,
    )
    assert out["start"] == ["a"]
    assert out["deferred"] == [{"id": "b", "reason": "global_cap"}, {"id": "c", "reason": "global_cap"}]
    assert out["global_free"] == 0


def test_dispatch_full_global_cap_starts_nothing(doc):
    out = dispatch(doc, ready=[task("a", "A05")], running={"A05": 4}, ceilings={"A05": 8}, global_cap=4)
    assert out["start"] == [] and out["deferred"] == [{"id": "a", "reason": "global_cap"}]
    assert out["global_free"] == 0


def test_dispatch_agent_ceiling_wins_when_both_limits_bind(doc):
    out = dispatch(doc, ready=[task("a", "A05")], running={"A05": 2, "A06": 2}, ceilings={"A05": 2}, global_cap=4)
    assert out["deferred"] == [{"id": "a", "reason": "agent_ceiling"}]


def test_dispatch_orders_by_priority_then_input_order(doc):
    ready = [task("low", "A05", -1), task("first", "A05"), task("second", "A05"), task("urgent", "A05", 9)]
    out = dispatch(doc, ready=ready, running={}, ceilings={"A05": 3}, global_cap=8)
    assert out["start"] == ["urgent", "first", "second"]
    assert out["deferred"] == [{"id": "low", "reason": "agent_ceiling"}]


def test_dispatch_empty_ready_is_a_no_op(doc):
    out = dispatch(doc, ready=[], running={"A05": 1}, ceilings={"A05": 2}, global_cap=4)
    assert out == {"state": "SUCCESS", "start": [], "deferred": [], "slots": {"A05": 1}, "global_free": 3}


def test_dispatch_matches_take_agent_slot_for_the_agent_rule(doc):
    ready = [task(f"t{i}", agent) for i, agent in enumerate("ABABAAB")]
    ceilings = {"A": 2, "B": 1}
    counts: dict[str, int] = {}
    expected = [t["id"] for t in ready if take_agent_slot(counts, t["agent_id"], ceilings[t["agent_id"]])]
    out = dispatch(doc, ready=ready, running={}, ceilings=ceilings, global_cap=32)
    assert out["start"] == expected


@pytest.mark.parametrize(
    "kw, field",
    [
        ({"ready": [task("a", "A05")], "running": {}, "ceilings": {}, "global_cap": 2}, "ceilings"),  # missing ceiling
        ({"ready": [task("a", "A05")], "running": {}, "ceilings": {"A05": 0}, "global_cap": 2}, "ceilings"),
        ({"ready": [task("a", "A05")], "running": {"A05": -1}, "ceilings": {"A05": 2}, "global_cap": 2}, "running"),
        ({"ready": [task("a", "A05")], "running": {"A05": "1"}, "ceilings": {"A05": 2}, "global_cap": 2}, "running"),
        ({"ready": [task("a", "A05"), task("a", "A06")], "running": {}, "ceilings": {"A05": 1, "A06": 1}, "global_cap": 2}, "ready"),
        ({"ready": [{"id": "a"}], "running": {}, "ceilings": {"A05": 1}, "global_cap": 2}, "ready"),  # item without agent_id
        ({"ready": [task("a", "A05")], "running": {}, "ceilings": {"A05": 1}, "global_cap": 0}, "global_cap"),
        ({"ready": [task("a", "A05")], "running": {}, "ceilings": {"A05": 1}, "global_cap": 33}, "global_cap"),
        ({"ready": "a", "running": {}, "ceilings": {"A05": 1}, "global_cap": 2}, "ready"),
        ({"ready": [task("a", "A05")], "running": {}, "ceilings": {"A05": 1}, "global_cap": True}, "global_cap"),
    ],
)
def test_dispatch_rejects_malformed_input_naming_the_field(doc, kw, field):
    out = dispatch(doc, **kw)
    assert out["state"] == "INVALID_INPUT" and out["field"] == field


def test_dispatch_rejects_oversized_ready_list(doc):
    ready = [task(f"t{i}", "A05") for i in range(501)]
    out = dispatch(doc, ready=ready, running={}, ceilings={"A05": 1}, global_cap=4)
    assert out["state"] == "INVALID_INPUT" and out["field"] == "ready"


def test_dispatch_does_not_mutate_the_payload(doc):
    running = {"A05": 1}
    ready = [task("a", "A05")]
    dispatch(doc, ready=ready, running=running, ceilings={"A05": 3}, global_cap=4)
    assert running == {"A05": 1} and ready == [task("a", "A05")]


# --- orch_budget_guard ------------------------------------------------------------------------------------------


def test_guard_ok_below_the_warn_line(doc):
    out = guard(doc, budget=BUDGET, usage=usage(120000, 600, 2.5), prior_breaches=0)
    assert out["decision"] == "OK" and out["next_action"] == "continue"
    assert out["breached"] == [] and out["breaches_after"] == 0
    assert out["ratios"] == {"tokens": 0.4, "wall_s": 0.3333, "cost_usd": 0.3125}


def test_guard_warns_at_exactly_eighty_percent(doc):
    out = guard(doc, budget=BUDGET, usage=usage(tokens=240000), prior_breaches=0)
    assert out["decision"] == "WARN" and out["next_action"] == "note_usage"
    assert out["breached"] == [] and out["breaches_after"] == 0


def test_guard_just_below_eighty_percent_is_ok(doc):
    assert guard(doc, budget=BUDGET, usage=usage(tokens=239999), prior_breaches=0)["decision"] == "OK"


def test_guard_hard_stops_at_exactly_one_hundred_percent(doc):
    out = guard(doc, budget=BUDGET, usage=usage(wall_s=1800), prior_breaches=0)
    assert out["decision"] == "HARD_STOP" and out["next_action"] == "requeue_once_annotated"
    assert out["breached"] == ["wall_s"] and out["breaches_after"] == 1


def test_guard_second_breach_escalates(doc):
    out = guard(doc, budget=BUDGET, usage=usage(tokens=310000, cost_usd=9), prior_breaches=1)
    assert out["decision"] == "ESCALATED" and out["next_action"] == "escalate"
    assert out["breached"] == ["tokens", "cost_usd"] and out["breaches_after"] == 2


def test_guard_prior_breach_alone_does_not_escalate_a_healthy_run(doc):
    out = guard(doc, budget=BUDGET, usage=usage(tokens=1000), prior_breaches=1)
    assert out["decision"] == "OK" and out["breaches_after"] == 1


def test_guard_custom_warn_line(doc):
    low = guard(doc, budget=BUDGET, usage=usage(tokens=150000), prior_breaches=0, warn_at=0.5)
    assert low["decision"] == "WARN"
    high = guard(doc, budget=BUDGET, usage=usage(tokens=240000), prior_breaches=0, warn_at=0.9)
    assert high["decision"] == "OK"


def test_guard_accepts_the_orch_budget_class_numbers(doc):
    """Medium risk budget as orch_plan assigns it (scripts/orch_plan.py _budget)."""
    budget = call_tool("orch_budget_class", "A01", {"risk": "medium"}, data=doc)
    limits = {k: budget[k] for k in ("max_tokens", "max_wall_s", "max_cost_usd")}
    out = guard(doc, budget=limits, usage=usage(tokens=limits["max_tokens"] + 1), prior_breaches=0)
    assert out["decision"] == "HARD_STOP" and out["breached"] == ["tokens"]


@pytest.mark.parametrize(
    "kw, field",
    [
        ({"budget": {**BUDGET, "max_tokens": 0}, "usage": usage(), "prior_breaches": 0}, "max_tokens"),
        ({"budget": {**BUDGET, "max_cost_usd": -1}, "usage": usage(), "prior_breaches": 0}, "max_cost_usd"),
        ({"budget": BUDGET, "usage": usage(tokens=-1), "prior_breaches": 0}, "usage"),
        ({"budget": BUDGET, "usage": {"tokens": 1, "wall_s": 1}, "prior_breaches": 0}, "usage"),
        ({"budget": BUDGET, "usage": usage(), "prior_breaches": -1}, "prior_breaches"),
        ({"budget": BUDGET, "usage": usage()}, "prior_breaches"),
        ({"budget": BUDGET, "usage": usage(), "prior_breaches": 0, "warn_at": 1}, "warn_at"),
        ({"budget": BUDGET, "usage": usage(), "prior_breaches": 0, "warn_at": 0}, "warn_at"),
        ({"budget": BUDGET, "usage": usage(tokens="many"), "prior_breaches": 0}, "usage"),
    ],
)
def test_guard_rejects_malformed_input_naming_the_field(doc, kw, field):
    out = guard(doc, **kw)
    assert out["state"] == "INVALID_INPUT"
    assert field in out["field"] or field in out["message"]


def test_guard_negative_budget_is_refused_by_the_handler_too():
    from swarm.tools import a01_tools

    out = a01_tools.budget_guard({"budget": {**BUDGET, "max_wall_s": 0}, "usage": usage(), "prior_breaches": 0})
    assert out["state"] == "INVALID_INPUT" and out["field"] == "budget"


def test_guard_worked_example_from_the_spec_hard_stop_then_escalate(doc):
    """03-agents/A01 section 3 rule 5: hard-stop at 100 %, requeue once annotated, second breach is ESCALATED."""
    first = guard(doc, budget=BUDGET, usage=usage(tokens=300000), prior_breaches=0)
    assert (first["decision"], first["breaches_after"]) == ("HARD_STOP", 1)
    second = guard(doc, budget=BUDGET, usage=usage(tokens=300000), prior_breaches=first["breaches_after"])
    assert (second["decision"], second["next_action"]) == ("ESCALATED", "escalate")


# --- review fix-ups (RV-C) --------------------------------------------------------------------------------------


def test_guard_nan_limit_is_invalid_input_not_continue(doc):
    """RV-C P2: max_wall_s=NaN with wall_s=5000 returned decision OK / continue and emitted NaN, not valid JSON."""
    out = guard(
        doc,
        budget={"max_tokens": 300000, "max_wall_s": float("nan"), "max_cost_usd": 8},
        usage=usage(1, 5000, 1),
        prior_breaches=1,
    )
    assert out["state"] == "INVALID_INPUT" and out["field"] == "budget"


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_guard_non_finite_usage_or_warn_at_is_invalid_input(doc, bad):
    """RV-C P2: non-finite numbers pass the registry validator; Infinity reached the output as ratios.tokens."""
    assert guard(doc, budget=BUDGET, usage=usage(tokens=bad), prior_breaches=0)["state"] == "INVALID_INPUT"
    assert guard(doc, budget=BUDGET, usage=usage(), prior_breaches=0, warn_at=bad)["state"] == "INVALID_INPUT"


def test_guard_overflowing_ratio_is_invalid_input(doc):
    """A finite usage over a tiny finite limit overflows to Infinity in the ratio; that cannot be reported as a number."""
    out = guard(doc, budget={**BUDGET, "max_cost_usd": 1e-300}, usage=usage(cost_usd=1e300), prior_breaches=0)
    assert out["state"] == "INVALID_INPUT" and out["field"] == "usage"


def test_guard_ratio_just_below_the_budget_never_shows_one(doc):
    """RV-C P3: 299999.99 of 300000 tokens showed ratios.tokens 1.0 with breached [] (a contradiction)."""
    out = guard(doc, budget=BUDGET, usage=usage(tokens=299999.99), prior_breaches=0)
    assert out["breached"] == [] and out["decision"] == "WARN"
    assert out["ratios"]["tokens"] == 0.9999
    at = guard(doc, budget=BUDGET, usage=usage(tokens=300000), prior_breaches=0)
    assert at["breached"] == ["tokens"] and at["ratios"]["tokens"] == 1.0
