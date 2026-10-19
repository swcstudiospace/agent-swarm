"""Behaviour of the v2.1 A01 lane tools: orch_registry_ping, orch_dag_order, orch_budget_class, orch_plan, orch_run,
orch_reconcile, orch_assign.

Each test asserts output values for specific inputs, not only that the call succeeded. The tools are called through
`swarm.tool_registry.call_tool` as A01 and, for the handlers whose logic is not just the registry's schema gate,
directly. `tests/conftest.py` evicts `swarm*` modules between tests, so every swarm import is resolved at call time.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LEGACY = ("orch_registry_ping", "orch_dag_order", "orch_budget_class", "orch_plan", "orch_run", "orch_reconcile",
          "orch_assign")


def call(name: str, payload, caller: str = "A01", **kw) -> dict:
    from swarm import tool_registry

    return tool_registry.call_tool(name, caller, payload, **kw)


def other_agents() -> list[str]:
    from swarm import tool_registry

    return [a["id"] for a in tool_registry.load_document()["agents"] if a["id"] != "A01"]


def _script(stem: str):
    spec = importlib.util.spec_from_file_location(f"_a01_existing_{stem}", ROOT / "scripts" / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def work(tmp_path, monkeypatch):
    """A workspace whose run state lives under tmp_path and whose cwd is the target root."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    return tmp_path


# --- registry: the seven tools are all registered for A01 only ---------------------------------------------------


def test_all_seven_legacy_tools_are_registered_for_a01_only():
    from swarm import tool_registry

    doc = tool_registry.load_document()
    for name in LEGACY:
        tool = tool_registry.find_tool(doc, name)
        assert tool["owner"] == "A01" and tool["permitted_callers"] == ["A01"], name


@pytest.mark.parametrize("name,payload", [
    ("orch_registry_ping", {"token": "t"}),
    ("orch_dag_order", {"nodes": [{"id": "a", "depends_on": []}]}),
    ("orch_budget_class", {"risk": "low"}),
    ("orch_plan", {"brief": "b"}),
    ("orch_run", {}),
    ("orch_reconcile", {"correlation_id": "c"}),
    ("orch_assign", {"task_id": "t"}),
])
def test_every_other_agent_is_refused_and_the_handler_never_runs(work, name, payload):
    for agent in other_agents():
        result = call(name, payload, caller=agent)
        assert result["state"] == "UNAUTHORIZED", (name, agent)
        assert result["caller"] == agent and result["target"] == name
    assert not (work / ".swarm" / "tasks.db").exists()


# --- orch_registry_ping ------------------------------------------------------------------------------------------


def test_ping_echoes_the_token_with_the_owner():
    assert call("orch_registry_ping", {"token": "hello"}) == {"state": "SUCCESS", "token": "hello", "owner": "A01"}


@pytest.mark.parametrize("token", ["x", "x" * 64, "héllo wörld ✓", " spaced ", "a/b\\c\n"])
def test_ping_accepts_tokens_up_to_64_characters_unchanged(token):
    result = call("orch_registry_ping", {"token": token})
    assert result["state"] == "SUCCESS" and result["token"] == token


def test_ping_rejects_an_empty_token_and_one_over_the_limit():
    empty = call("orch_registry_ping", {"token": ""})
    assert empty["state"] == "INVALID_INPUT" and empty["field"] == "token"
    over = call("orch_registry_ping", {"token": "x" * 65})
    assert over["state"] == "INVALID_INPUT" and over["field"] == "token"


@pytest.mark.parametrize("token", [None, 5, ["t"], {"a": 1}, True])
def test_ping_rejects_a_non_string_token_naming_the_field(token):
    result = call("orch_registry_ping", {"token": token})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "token"


def test_ping_requires_the_token_and_refuses_unknown_fields():
    missing = call("orch_registry_ping", {})
    assert missing["state"] == "INVALID_INPUT" and missing["field"] == "token"
    extra = call("orch_registry_ping", {"token": "t", "extra": 1})
    assert extra["state"] == "INVALID_INPUT" and extra["field"] == "extra"
    assert call("orch_registry_ping", {"token": "t", "probe": "bogus"})["field"] == "probe"


def test_ping_probes_reach_each_non_success_state_and_keep_the_token():
    partial = call("orch_registry_ping", {"token": "p", "probe": "partial"})
    assert partial == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5, "token": "p", "owner": "A01"}
    assert call("orch_registry_ping", {"token": "p", "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call("orch_registry_ping", {"token": "p", "probe": "sleep"}, timeout_s=0.05)["state"] == "TIMEOUT"


def test_ping_handler_called_directly_covers_every_probe():
    from swarm.tools.orch_registry_ping import call as ping

    assert ping({"token": "d"}) == {"state": "SUCCESS", "token": "d", "owner": "A01"}
    assert ping({"token": "d", "probe": "partial"})["completed_fraction"] == 0.5
    with pytest.raises(OSError, match="dependency"):
        ping({"token": "d", "probe": "dependency"})
    slept = ping({"token": "d", "probe": "sleep"})
    assert slept["state"] == "TIMEOUT" and slept["token"] == "d" and slept["owner"] == "A01"
    # an unknown probe value is not special-cased by the handler: it behaves as no probe
    assert ping({"token": "d", "probe": "other"})["state"] == "SUCCESS"


def test_ping_writes_no_task_store_state(work):
    for _ in range(2):
        assert call("orch_registry_ping", {"token": "same"})["token"] == "same"
    assert not (work / ".swarm" / "tasks.db").exists()


# --- orch_dag_order ----------------------------------------------------------------------------------------------


def node(node_id: str, *deps: str) -> dict:
    return {"id": node_id, "depends_on": list(deps)}


def order(*nodes: dict) -> dict:
    return call("orch_dag_order", {"nodes": list(nodes)})


def test_dag_order_puts_dependencies_first_whatever_the_input_order():
    assert order(node("b", "a"), node("a")) == {"state": "SUCCESS", "order": "a,b"}
    assert order(node("c", "b"), node("b", "a"), node("a"))["order"] == "a,b,c"


def test_dag_order_keeps_input_order_between_independent_nodes():
    assert order(node("c"), node("a"), node("b"))["order"] == "c,a,b"
    assert order(node("z"), node("y"), node("x", "z", "y"))["order"] == "z,y,x"


def test_dag_order_diamond_releases_the_join_after_both_branches():
    result = order(node("join", "left", "right"), node("left", "root"), node("right", "root"), node("root"))
    assert result["order"] == "root,left,right,join"


def test_dag_order_ready_children_are_merged_back_in_input_position_order():
    # after a, the ready set {b, d, c} is released by input position (d=1, b=2, c=3), not by discovery order
    assert order(node("a"), node("d", "a"), node("b"), node("c", "a"))["order"] == "a,d,b,c"


def test_dag_order_counts_a_repeated_dependency_once():
    assert order(node("a"), node("b", "a", "a", "a"))["order"] == "a,b"


def test_dag_order_ignores_extra_node_keys_and_does_not_mutate_the_input():
    nodes = [{"id": "b", "depends_on": ["a"], "title": "second"}, {"id": "a", "depends_on": [], "title": "first"}]
    snapshot = json.loads(json.dumps(nodes))
    assert call("orch_dag_order", {"nodes": nodes})["order"] == "a,b"
    assert nodes == snapshot


def test_dag_order_handles_a_long_chain_listed_in_reverse():
    n = 400
    nodes = [node(f"n{i}", f"n{i - 1}") if i else node("n0") for i in range(n)]
    nodes.reverse()
    assert order(*nodes)["order"] == ",".join(f"n{i}" for i in range(n))


def test_dag_order_two_node_cycle_is_invalid_and_names_the_cycle():
    result = order(node("a", "b"), node("b", "a"))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "nodes"
    assert "dependency cycle" in result["message"] and "a -> b -> a" in result["message"]
    assert "order" not in result


def test_dag_order_a_cycle_anywhere_fails_the_whole_call_instead_of_returning_a_partial_order():
    result = order(node("ok"), node("b", "c"), node("c", "b"), node("tail", "ok"))
    assert result["state"] == "INVALID_INPUT" and "b -> c -> b" in result["message"] and "order" not in result


def test_dag_order_self_dependency_is_a_cycle():
    result = order(node("a", "a"))
    assert result["state"] == "INVALID_INPUT" and "a -> a" in result["message"]


def test_dag_order_unknown_dependency_names_both_nodes():
    result = order(node("a", "ghost"))
    assert result["state"] == "INVALID_INPUT" and result["field"] == "nodes"
    assert "a" in result["message"] and "ghost" in result["message"]


def test_dag_order_empty_or_non_list_nodes_are_invalid():
    empty = call("orch_dag_order", {"nodes": []})
    assert empty["state"] == "INVALID_INPUT" and empty["field"] == "nodes" and "non-empty" in empty["message"]
    for bad in ("a,b", {"id": "a"}, 3, None):
        result = call("orch_dag_order", {"nodes": bad})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "nodes", bad


@pytest.mark.parametrize("bad_node", [
    {"id": "a"},  # no depends_on
    {"depends_on": []},  # no id
    {"id": 7, "depends_on": []},
    {"id": "a", "depends_on": "b"},
    {"id": "a", "depends_on": None},
    "a",
    7,
    None,
    ["a"],
])
def test_dag_order_each_malformed_node_shape_is_invalid_naming_nodes(bad_node):
    result = order(node("ok"), bad_node)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "nodes"
    assert "string id" in result["message"]


@pytest.mark.parametrize("deps", [[1], ["a", None], [["a"]], [{"id": "a"}]])
def test_dag_order_non_string_dependency_entries_are_invalid(deps):
    result = order(node("a"), {"id": "b", "depends_on": deps})
    assert result["state"] == "INVALID_INPUT" and "depends_on entries must be strings" in result["message"]


def test_dag_order_requires_nodes_and_refuses_unknown_fields():
    missing = call("orch_dag_order", {})
    assert missing["state"] == "INVALID_INPUT" and missing["field"] == "nodes"
    assert call("orch_dag_order", {"nodes": [node("a")], "extra": True})["field"] == "extra"


def test_dag_order_handler_directly_returns_the_comma_joined_order():
    from swarm.tools.batch_01_05 import dag_order

    assert dag_order({"nodes": [node("b", "a"), node("a")]}) == {"state": "SUCCESS", "order": "a,b"}
    assert dag_order({"nodes": []})["field"] == "nodes"


def test_dag_order_matches_the_script_topological_sort_the_planner_uses():
    graph = _script("orch_from_graph")
    nodes = [node("e", "c", "d"), node("d", "b"), node("c", "a"), node("b", "a"), node("a")]
    expected = ",".join(graph._topo(nodes, graph._dependencies(nodes)))
    assert order(*nodes)["order"] == expected == "a,c,b,d,e"


def test_dag_order_a_duplicate_node_id_is_not_reported_as_a_cycle():
    result = order(node("a"), node("a"))
    assert result["state"] == "INVALID_INPUT"
    assert "cycle" not in result["message"] and "duplicate" in result["message"]


# --- orch_budget_class -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("risk,tokens,wall,cost", [
    ("low", 150_000, 900, 3),
    ("medium", 300_000, 1800, 8),
    ("high", 600_000, 3600, 20),
])
def test_budget_class_returns_the_exact_budget_for_each_risk(risk, tokens, wall, cost):
    assert call("orch_budget_class", {"risk": risk}) == {
        "state": "SUCCESS", "max_tokens": tokens, "max_wall_s": wall, "max_cost_usd": cost}


def test_budget_grows_with_risk_in_every_dimension_and_all_values_are_integers():
    budgets = [call("orch_budget_class", {"risk": r}) for r in ("low", "medium", "high")]
    for key in ("max_tokens", "max_wall_s", "max_cost_usd"):
        values = [b[key] for b in budgets]
        assert values == sorted(values) and len(set(values)) == 3
        assert all(type(v) is int for v in values)


def test_budget_class_matches_what_the_planner_script_assigns():
    plan = _script("orch_plan")
    for risk in ("low", "medium", "high"):
        got = call("orch_budget_class", {"risk": risk})
        assert {k: got[k] for k in ("max_tokens", "max_wall_s", "max_cost_usd")} == plan._budget(risk)


@pytest.mark.parametrize("risk", ["urgent", "LOW", "Low", " low", "", "critical", "none"])
def test_budget_class_rejects_anything_but_the_three_exact_risk_names(risk):
    result = call("orch_budget_class", {"risk": risk})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "risk"


@pytest.mark.parametrize("risk", [None, 1, ["low"], True, {"risk": "low"}])
def test_budget_class_rejects_a_non_string_risk(risk):
    result = call("orch_budget_class", {"risk": risk})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "risk"


def test_budget_class_requires_risk_and_refuses_unknown_fields():
    assert call("orch_budget_class", {})["field"] == "risk"
    result = call("orch_budget_class", {"risk": "low", "tokens": 1})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "tokens"


def test_budget_class_handler_directly_and_its_output_keys():
    from swarm.tools.batch_01_05 import budget_class

    assert set(budget_class({"risk": "high"})) == {"state", "max_tokens", "max_wall_s", "max_cost_usd"}
    with pytest.raises(KeyError):
        budget_class({"risk": "bogus"})  # only the registry schema gate turns an unknown class into INVALID_INPUT


# --- orch_plan ---------------------------------------------------------------------------------------------------


def test_plan_defaults_to_a_medium_risk_feature_with_the_medium_gates():
    result = call("orch_plan", {"brief": "add login"})
    assert result["state"] == "SUCCESS"
    assert result["dag"]["pattern"] == "feature" and result["dag"]["risk"] == "medium"
    assert result["dag"]["gates"] == ["review", "quality"]
    assert result["summary"] == "decomposed brief to DAG (pattern=feature)"


@pytest.mark.parametrize("risk,gates", [
    ("low", ["review"]),
    ("medium", ["review", "quality"]),
    ("high", ["review", "quality", "security", "release"]),
])
def test_plan_lists_exactly_the_gates_of_the_risk_class(risk, gates):
    dag = call("orch_plan", {"brief": "b", "risk_class": risk})["dag"]
    assert dag["risk"] == risk and dag["gates"] == gates


def test_plan_gates_come_from_the_task_store_gate_table():
    from swarm.taskstore import GATES_BY_RISK

    for risk in ("low", "medium", "high"):
        assert call("orch_plan", {"brief": "b", "risk_class": risk})["dag"]["gates"] == GATES_BY_RISK[risk]


@pytest.mark.parametrize("pattern", ["feature", "hotfix", "dependency"])
def test_plan_task_count_matches_the_persisting_planners_pattern(pattern):
    planned = len(_script("orch_plan").PATTERNS[pattern])
    dag = call("orch_plan", {"brief": "b", "pattern": pattern})["dag"]
    assert dag["pattern"] == pattern and dag["tasks"] == planned


def test_plan_parallel_pattern_is_echoed_in_dag_and_summary():
    result = call("orch_plan", {"brief": "b", "pattern": "parallel"})
    assert result["dag"]["pattern"] == "parallel" and result["summary"].endswith("(pattern=parallel)")


def test_plan_dag_has_exactly_the_documented_keys():
    dag = call("orch_plan", {"brief": "b"})["dag"]
    assert set(dag) == {"correlation_id", "pattern", "tasks", "risk", "gates"}


def test_plan_gate_list_is_a_copy_not_the_live_gate_table():
    from swarm.taskstore import GATES_BY_RISK

    # identity check only: never mutate the shared table from a test
    assert call("orch_plan", {"brief": "b"})["dag"]["gates"] is not GATES_BY_RISK["medium"]


@pytest.mark.parametrize("brief", ["x", "add login ✓ \u00e9", "line1\nline2", "y" * 100_000])
def test_plan_accepts_any_non_empty_brief_up_to_large_sizes(brief):
    assert call("orch_plan", {"brief": brief})["state"] == "SUCCESS"


@pytest.mark.parametrize("payload", [{}, {"brief": ""}, {"brief": None}, {"brief": 3}, {"brief": ["x"]}])
def test_plan_rejects_a_missing_empty_or_non_string_brief(payload):
    result = call("orch_plan", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "brief"


@pytest.mark.parametrize("extra,field", [
    ({"pattern": "nope"}, "pattern"),
    ({"pattern": "FEATURE"}, "pattern"),
    ({"risk_class": "urgent"}, "risk_class"),
    ({"risk_class": "Medium"}, "risk_class"),
    ({"risk": "low"}, "risk"),
])
def test_plan_rejects_unknown_enum_values_and_fields_naming_them(extra, field):
    result = call("orch_plan", {"brief": "b", **extra})
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_plan_does_not_persist_anything(work):
    call("orch_plan", {"brief": "persist me", "risk_class": "high"})
    assert not (work / ".swarm" / "tasks.db").exists()


def test_plan_handler_directly_applies_defaults_and_rejects_a_blank_brief():
    from swarm.tools.batch_01_05 import plan

    assert plan({"brief": "x"})["dag"]["risk"] == "medium"
    assert plan({"brief": ""}) == {"state": "INVALID_INPUT", "field": "brief", "message": "brief required string"}
    assert plan({"brief": 5})["field"] == "brief"


# --- orch_run ----------------------------------------------------------------------------------------------------


def test_run_never_claims_a_dispatch_it_did_not_perform(work):
    for payload in ({}, {"correlation_id": "C1"}, {"correlation_id": "C1", "max_parallel": 4}):
        result = call("orch_run", payload)
        assert result["state"] == "DEPENDENCY_UNAVAILABLE"
        assert "swarm_run.py" in result["message"]
        assert "dispatched" not in result
    assert not (work / ".swarm" / "tasks.db").exists()


def test_run_rejects_a_wrongly_typed_field_naming_it():
    bad_cap = call("orch_run", {"max_parallel": "3"})
    assert bad_cap["state"] == "INVALID_INPUT" and bad_cap["field"] == "max_parallel"
    bad_corr = call("orch_run", {"correlation_id": 7})
    assert bad_corr["state"] == "INVALID_INPUT" and bad_corr["field"] == "correlation_id"
    assert call("orch_run", {"bogus": 1})["field"] == "bogus"


def test_run_probes_still_reach_partial_and_timeout():
    assert call("orch_run", {"probe": "partial"}) == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert call("orch_run", {"probe": "sleep"}, timeout_s=0.05)["state"] == "TIMEOUT"


def test_run_handler_directly_ignores_its_inputs():
    from swarm.tools.batch_01_05 import run

    first = run({})
    assert first == run({"correlation_id": "x", "max_parallel": 99})
    assert first["state"] == "DEPENDENCY_UNAVAILABLE"


# --- orch_assign -------------------------------------------------------------------------------------------------


def test_assign_reports_the_dependency_unavailable_and_points_at_the_script(work):
    result = call("orch_assign", {"task_id": "T-1"})
    assert result["state"] == "DEPENDENCY_UNAVAILABLE"
    assert "scripts/orch_assign.py" in result["message"]
    assert "assignment_verified" not in result and "assignment" not in result
    assert not (work / ".swarm" / "tasks.db").exists()


def test_assign_ignores_the_optional_agent_id_and_changes_no_task(work):
    from swarm.taskstore import TaskStore

    store = TaskStore()
    store.create(task_id="T-1", correlation_id="C", capability="code.backend", agent_id="A05")
    store.conn.close()
    result = call("orch_assign", {"task_id": "T-1", "agent_id": "A06"})
    assert result["state"] == "DEPENDENCY_UNAVAILABLE"
    store = TaskStore()
    task = store.get("T-1")
    store.conn.close()
    assert task["state"] == "CREATED" and task["agent_id"] == "A05"


@pytest.mark.parametrize("task_id", [None, 5, ["T-1"], {"id": "T-1"}, True])
def test_assign_rejects_a_non_string_task_id(task_id):
    result = call("orch_assign", {"task_id": task_id})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "task_id"


def test_assign_requires_task_id_and_validates_the_other_fields():
    missing = call("orch_assign", {})
    assert missing["state"] == "INVALID_INPUT" and missing["field"] == "task_id"
    assert call("orch_assign", {"task_id": "T", "agent_id": 4})["field"] == "agent_id"
    assert call("orch_assign", {"task_id": "T", "extra": 1})["field"] == "extra"


def test_assign_handler_directly_validates_task_id_before_reporting_the_dependency():
    from swarm.tools.batch_01_05 import assign

    assert assign({})["state"] == "INVALID_INPUT" and assign({})["field"] == "task_id"
    assert assign({"task_id": 9})["field"] == "task_id"
    assert assign({"task_id": "T"})["state"] == "DEPENDENCY_UNAVAILABLE"


def test_assign_probe_paths():
    assert call("orch_assign", {"task_id": "T", "probe": "partial"})["state"] == "PARTIAL_SUCCESS"
    assert call("orch_assign", {"task_id": "T", "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"


# --- orch_reconcile ----------------------------------------------------------------------------------------------

LADDER = ["VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "APPROVED", "DONE"]
FAIL_FINDING = [{"severity": "major", "kind": "k", "summary": "s"}]


def open_store():
    from swarm.taskstore import TaskStore

    return TaskStore()


def add_task(store, tid: str, corr: str, state: str = "IN_REVIEW", *, risk: str = "low", notes: dict | None = None,
             depends_on=None) -> None:
    store.create(task_id=tid, correlation_id=corr, capability="code.backend", risk_class=risk,
                 notes=notes if notes is not None else {"gates": []}, depends_on=depends_on)
    for step in LADDER[: LADDER.index(state) + 1]:
        store.transition(tid, step)


def verdict(store, tid: str, corr: str, outcome: str, gate: str = "review") -> None:
    from swarm.gates import make_verdict

    time.sleep(0.01)  # a verdict counts only when it is newer than the task's last rework
    findings = FAIL_FINDING if outcome == "fail" else []
    store.record_verdict(tid, make_verdict(gate=gate, task_id=tid, agent_id="A09@test", verdict=outcome,
                                           findings=findings, correlation_id=corr))


def events(work: Path, event_type: str) -> list[dict]:
    path = work / ".swarm" / "events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if r["type"] == event_type]


def reconcile(**payload) -> dict:
    return call("orch_reconcile", payload)


def test_reconcile_without_any_correlation_is_invalid_naming_the_field(work):
    result = reconcile()
    assert result["state"] == "INVALID_INPUT" and result["field"] == "correlation_id"
    assert "no latest correlation" in result["message"]


def test_reconcile_an_empty_latest_correlation_file_is_still_missing(work):
    (work / ".swarm").mkdir()
    (work / ".swarm" / "latest_correlation").write_text("  \n", encoding="utf-8")
    assert reconcile()["state"] == "INVALID_INPUT"
    assert reconcile(correlation_id="")["field"] == "correlation_id"


def test_reconcile_an_empty_correlation_reports_zero_outcomes(work):
    assert reconcile(correlation_id="C-none") == {
        "state": "SUCCESS", "correlation_id": "C-none", "reassigned": 0, "aggregated": [],
        "summary": "reconciled C-none: 0 outcome(s)"}


def test_reconcile_defaults_to_the_latest_correlation_file_and_strips_it(work):
    (work / ".swarm").mkdir()
    (work / ".swarm" / "latest_correlation").write_text("C-latest\n", encoding="utf-8")
    result = reconcile()
    assert result["state"] == "SUCCESS" and result["correlation_id"] == "C-latest"
    assert reconcile(correlation_id="C-explicit")["correlation_id"] == "C-explicit"  # an explicit id wins
    assert reconcile(correlation_id="")["correlation_id"] == "C-latest"  # an empty one falls back


def test_reconcile_rejects_a_non_string_correlation_and_unknown_fields(work):
    result = reconcile(correlation_id=5)
    assert result["state"] == "INVALID_INPUT" and result["field"] == "correlation_id"
    assert call("orch_reconcile", {"correlation_id": "c", "task_id": "t"})["field"] == "task_id"


def test_reconcile_approves_an_in_review_task_with_no_required_gate(work):
    store = open_store()
    add_task(store, "T-a", "C1")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["state"] == "SUCCESS" and result["aggregated"] == ["T-a: DONE"]
    assert result["reassigned"] == 0 and result["summary"] == "reconciled C1: 1 outcome(s)"
    store = open_store()
    states = [h["to_state"] for h in store.history("T-a")][-3:]
    store.conn.close()
    assert states == ["IN_REVIEW", "APPROVED", "DONE"]


def test_reconcile_only_touches_in_review_tasks_of_the_named_correlation(work):
    store = open_store()
    add_task(store, "T-in", "C1")
    add_task(store, "T-other-corr", "C2")
    add_task(store, "T-working", "C1", state="IN_PROGRESS")
    add_task(store, "T-done", "C1", state="DONE")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["aggregated"] == ["T-in: DONE"]
    store = open_store()
    got = {t: store.get(t)["state"] for t in ("T-in", "T-other-corr", "T-working", "T-done")}
    store.conn.close()
    assert got == {"T-in": "DONE", "T-other-corr": "IN_REVIEW", "T-working": "IN_PROGRESS", "T-done": "DONE"}


def test_reconcile_reports_one_outcome_per_task_in_creation_order(work):
    store = open_store()
    for tid in ("T-1", "T-2", "T-3"):
        add_task(store, tid, "C1")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["aggregated"] == ["T-1: DONE", "T-2: DONE", "T-3: DONE"]
    assert result["summary"] == "reconciled C1: 3 outcome(s)"


def test_reconcile_a_passing_required_gate_approves_the_task(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})  # low risk: review gate required
    verdict(store, "T-a", "C1", "pass")
    store.conn.close()
    assert reconcile(correlation_id="C1")["aggregated"] == ["T-a: DONE"]


def test_reconcile_a_failing_gate_sends_the_task_back_to_rework_and_counts_it(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    verdict(store, "T-a", "C1", "fail")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["state"] == "SUCCESS" and result["reassigned"] == 1
    assert result["aggregated"] == ["T-a: CHANGES_REQUESTED → rework #1 (['review'])"]
    assert result["summary"] == "reconciled C1: 1 outcome(s)"
    store = open_store()
    task = store.get("T-a")
    store.conn.close()
    assert task["state"] == "IN_PROGRESS" and task["rework_loops"] == 1


def test_reconcile_counts_only_reworks_in_reassigned_not_plain_approvals(work):
    store = open_store()
    add_task(store, "T-ok", "C1")
    add_task(store, "T-bad", "C1", notes={})
    verdict(store, "T-bad", "C1", "fail")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["reassigned"] == 1 and len(result["aggregated"]) == 2
    assert result["aggregated"][0] == "T-ok: DONE"


def test_reconcile_escalates_after_the_rework_cap_and_emits_an_escalation_request(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    verdict(store, "T-a", "C1", "fail")
    store.conn.close()
    notes = [reconcile(correlation_id="C1")["aggregated"][0]]
    for _ in range(2):
        store = open_store()
        store.transition("T-a", "IN_REVIEW")
        verdict(store, "T-a", "C1", "fail")
        store.conn.close()
        notes.append(reconcile(correlation_id="C1")["aggregated"][0])
    assert notes[0].startswith("T-a: CHANGES_REQUESTED → rework #1")
    assert notes[1].startswith("T-a: CHANGES_REQUESTED → rework #2")
    assert notes[2] == "T-a: ESCALATED after 2 rework loops"
    store = open_store()
    state = store.get("T-a")["state"]
    store.conn.close()
    assert state == "ESCALATED"
    (event,) = events(work, "escalation.request")
    assert event["source"] == "A01@orch_reconcile" and event["correlation_id"] == "C1" and event["task_id"] == "T-a"
    assert event["payload"]["reason_code"] == "E-CONTRACT" and event["payload"]["evidence"] == ["review"]
    assert "human review" in event["payload"]["options"]


def test_reconcile_an_escalated_outcome_is_not_counted_as_a_reassignment(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    for _ in range(2):  # burn the two permitted rework loops
        verdict(store, "T-a", "C1", "fail")
        store.conn.close()
        reconcile(correlation_id="C1")
        store = open_store()
        store.transition("T-a", "IN_REVIEW")
    verdict(store, "T-a", "C1", "fail")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["aggregated"] == ["T-a: ESCALATED after 2 rework loops"] and result["reassigned"] == 0


def test_reconcile_a_stale_failing_verdict_from_before_a_rework_is_ignored(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    verdict(store, "T-a", "C1", "fail")
    store.conn.close()
    assert reconcile(correlation_id="C1")["reassigned"] == 1
    store = open_store()
    store.transition("T-a", "IN_REVIEW")  # back for review; the old fail row predates the rework
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["reassigned"] == 0 and "gate stall" in result["aggregated"][0]


def test_reconcile_a_missing_gate_with_no_gate_task_escalates_once_per_distinct_stall(work):
    store = open_store()
    add_task(store, "T-a", "C1", risk="medium", notes={})  # needs review and quality, nothing has issued them
    store.conn.close()
    first = reconcile(correlation_id="C1")
    (note,) = first["aggregated"]
    assert note.startswith("T-a: gate stall [") and note.endswith("→ escalation.request")
    assert "review:" in note and "quality:" in note and first["reassigned"] == 0
    (event,) = events(work, "escalation.request")
    assert event["task_id"] == "T-a" and event["payload"]["options"] == ["re-run gate", "human review", "cancel"]
    again = reconcile(correlation_id="C1")  # the same stall is not re-announced
    assert again["aggregated"] == [] and len(events(work, "escalation.request")) == 1
    store = open_store()
    state = store.get("T-a")["state"]
    store.conn.close()
    assert state == "IN_REVIEW"  # no transition: A01 or a human decides


def test_reconcile_a_live_gate_task_prevents_the_stall_escalation(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    add_task(store, "G-rev", "C1", state="PLANNED", notes={"gates": [], "gate": "review", "gate_for": ["T-a"]})
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["aggregated"] == [] and events(work, "escalation.request") == []


def test_reconcile_holds_a_target_while_its_gate_task_is_still_leased(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    verdict(store, "T-a", "C1", "pass")
    add_task(store, "G-rev", "C1", state="IN_PROGRESS", notes={"gates": [], "gate": "review", "gate_for": ["T-a"]})
    store.conn.close()
    assert reconcile(correlation_id="C1")["aggregated"] == []
    store = open_store()
    state = store.get("T-a")["state"]
    store.conn.close()
    assert state == "IN_REVIEW"


def test_reconcile_a_failed_gate_reopens_a_finished_gate_task_as_a_rerun(work):
    store = open_store()
    add_task(store, "T-a", "C1", notes={})
    add_task(store, "G-rev", "C1", state="DONE", notes={"gates": [], "gate": "review", "gate_for": ["T-a"]})
    verdict(store, "T-a", "C1", "fail")
    store.conn.close()
    result = reconcile(correlation_id="C1")
    assert result["reassigned"] == 1
    store = open_store()
    rerun = store.get("G-rev.r1")
    store.conn.close()
    assert rerun["state"] == "PLANNED" and rerun["title"].endswith(" (rerun)")
    assert rerun["notes_json"]["gate"] == "review" and rerun["notes_json"]["gate_for"] == ["T-a"]


def test_reconcile_is_idempotent_once_everything_is_settled(work):
    store = open_store()
    add_task(store, "T-a", "C1")
    store.conn.close()
    assert reconcile(correlation_id="C1")["aggregated"] == ["T-a: DONE"]
    again = reconcile(correlation_id="C1")
    assert again["aggregated"] == [] and again["summary"] == "reconciled C1: 0 outcome(s)"


def test_reconcile_probes_return_before_touching_the_store(work):
    assert reconcile(probe="partial") == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert reconcile(probe="dependency")["state"] == "DEPENDENCY_UNAVAILABLE"
    assert not (work / ".swarm" / "tasks.db").exists()


def test_reconcile_handler_directly_returns_the_documented_shape(work):
    from swarm.tools.batch_01_05 import reconcile as handler

    result = handler({"correlation_id": "C9"})
    assert set(result) == {"state", "correlation_id", "reassigned", "aggregated", "summary"}
    assert handler({})["state"] == "INVALID_INPUT"


# --- orch_dag_blocked, orch_gate_coverage, orch_rework_bound (LT-A01b) --------------------------------------------

ALL_STATES = ["CREATED", "VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS", "IN_REVIEW", "CHANGES_REQUESTED", "APPROVED",
              "DONE", "BLOCKED", "FAILED", "RETRY", "ESCALATED", "CANCELLED"]


def dtask(task_id: str, state: str, *deps: str) -> dict:
    return {"id": task_id, "state": state, "depends_on": list(deps)}


def blocked(*tasks: dict) -> dict:
    return call("orch_dag_blocked", {"tasks": list(tasks)})


def test_dag_blocked_is_registered_for_a01_only_and_refuses_every_other_agent():
    from swarm import tool_registry

    tool = tool_registry.find_tool(tool_registry.load_document(), "orch_dag_blocked")
    assert tool["owner"] == "A01" and tool["permitted_callers"] == ["A01"]
    for agent in other_agents():
        result = call("orch_dag_blocked", {"tasks": [dtask("a", "PLANNED")]}, caller=agent)
        assert result == {"state": "UNAUTHORIZED", "caller": agent, "target": "orch_dag_blocked",
                          "reason": "caller is not permitted"}


def test_dag_blocked_task_without_dependencies_is_ready_in_planned_and_retry():
    result = blocked(dtask("b", "RETRY"), dtask("a", "PLANNED"))
    assert result == {"state": "SUCCESS", "ready": ["a", "b"], "waiting": [], "blocked": [], "unresolved": []}


def test_dag_blocked_dependencies_in_review_approved_or_done_all_count_as_met():
    result = blocked(dtask("a", "DONE"), dtask("b", "APPROVED"), dtask("c", "IN_REVIEW"),
                     dtask("x", "PLANNED", "a", "b", "c"), dtask("y", "RETRY", "c"))
    assert result["ready"] == ["x", "y"] and result["waiting"] == [] and result["blocked"] == []


@pytest.mark.parametrize("state", [s for s in ALL_STATES if s not in ("PLANNED", "RETRY")])
def test_dag_blocked_only_planned_and_retry_tasks_are_classified(state):
    # the task is neither ready nor waiting nor blocked, whatever its dependencies look like
    result = blocked(dtask("x", state, "gone"))
    assert result == {"state": "SUCCESS", "ready": [], "waiting": [], "blocked": [], "unresolved": []}


@pytest.mark.parametrize("dep_state", ["CREATED", "VALIDATED", "PLANNED", "CLAIMED", "IN_PROGRESS",
                                       "CHANGES_REQUESTED", "BLOCKED", "FAILED", "RETRY"])
def test_dag_blocked_an_unmet_but_not_dead_dependency_makes_the_task_wait(dep_state):
    result = blocked(dtask("d", dep_state), dtask("x", "PLANNED", "d"))
    assert result["waiting"] == ["x"] and result["blocked"] == []
    assert result["ready"] == (["d"] if dep_state in ("PLANNED", "RETRY") else [])


def test_dag_blocked_one_unmet_dependency_is_enough_to_wait():
    result = blocked(dtask("a", "DONE"), dtask("b", "IN_PROGRESS"), dtask("x", "PLANNED", "a", "b"))
    assert result["ready"] == [] and result["waiting"] == ["x"]


def test_dag_blocked_a_dependency_missing_from_the_plan_blocks_with_the_sorted_missing_ids():
    result = blocked(dtask("a", "DONE"), dtask("x", "PLANNED", "z", "a", "m"))
    assert result["blocked"] == [{"id": "x", "reason": "missing_dependency", "causes": ["m", "z"]}]
    assert result["ready"] == [] and result["waiting"] == [] and result["unresolved"] == []


def test_dag_blocked_escalated_or_cancelled_dependencies_block_as_dead():
    result = blocked(dtask("a", "DONE"), dtask("e", "ESCALATED"), dtask("c", "CANCELLED"),
                     dtask("x", "PLANNED", "c", "a", "e"), dtask("y", "RETRY", "e"))
    assert result["blocked"] == [{"id": "x", "reason": "dead_dependency", "causes": ["c", "e"]},
                                 {"id": "y", "reason": "dead_dependency", "causes": ["e"]}]
    assert result["ready"] == [] and result["waiting"] == []


def test_dag_blocked_a_dead_dependency_outranks_a_merely_unmet_one():
    result = blocked(dtask("e", "ESCALATED"), dtask("p", "IN_PROGRESS"), dtask("x", "PLANNED", "p", "e"))
    assert result["blocked"] == [{"id": "x", "reason": "dead_dependency", "causes": ["e"]}]
    assert result["waiting"] == []


def test_dag_blocked_a_missing_dependency_outranks_a_dead_one():
    result = blocked(dtask("e", "CANCELLED"), dtask("x", "PLANNED", "e", "zz"))
    assert result["blocked"] == [{"id": "x", "reason": "missing_dependency", "causes": ["zz"]}]


def test_dag_blocked_cycle_members_and_their_dependents_are_blocked_and_unresolved():
    result = blocked(dtask("a", "PLANNED", "b"), dtask("b", "PLANNED", "a"), dtask("c", "PLANNED", "a"),
                     dtask("d", "PLANNED"), dtask("e", "PLANNED", "d"))
    assert result["ready"] == ["d"] and result["waiting"] == ["e"]
    assert result["blocked"] == [{"id": "a", "reason": "cycle", "causes": ["b"]},
                                 {"id": "b", "reason": "cycle", "causes": ["a"]},
                                 {"id": "c", "reason": "cycle", "causes": ["a"]}]
    assert result["unresolved"] == ["a", "b", "c"]


def test_dag_blocked_a_task_depending_on_itself_is_a_cycle():
    result = blocked(dtask("a", "PLANNED", "a"), dtask("b", "DONE"))
    assert result["blocked"] == [{"id": "a", "reason": "cycle", "causes": ["a"]}]
    assert result["ready"] == [] and result["unresolved"] == ["a"]


def test_dag_blocked_a_cycle_outranks_missing_and_dead_dependencies():
    result = blocked(dtask("a", "PLANNED", "b", "gone"), dtask("b", "PLANNED", "a"), dtask("e", "ESCALATED"),
                     dtask("c", "RETRY", "a", "e"))
    assert [(row["id"], row["reason"]) for row in result["blocked"]] == [("a", "cycle"), ("b", "cycle"), ("c", "cycle")]
    assert result["blocked"][0]["causes"] == ["b"]


def test_dag_blocked_unresolved_includes_non_dispatchable_cycle_members_without_classifying_them():
    result = blocked(dtask("a", "DONE", "b"), dtask("b", "DONE", "a"), dtask("x", "PLANNED"))
    assert result == {"state": "SUCCESS", "ready": ["x"], "waiting": [], "blocked": [], "unresolved": ["a", "b"]}


def test_dag_blocked_a_diamond_is_not_a_cycle_and_waits_for_both_branches():
    result = blocked(dtask("root", "DONE"), dtask("l", "PLANNED", "root"), dtask("r", "IN_PROGRESS", "root"),
                     dtask("join", "PLANNED", "l", "r"))
    assert result == {"state": "SUCCESS", "ready": ["l"], "waiting": ["join"], "blocked": [], "unresolved": []}


def test_dag_blocked_repeated_dependency_ids_are_treated_as_one_dependency():
    result = blocked(dtask("a", "DONE"), dtask("x", "PLANNED", "a", "a", "a"), dtask("y", "PLANNED", "gone", "gone"))
    assert result["ready"] == ["x"]
    assert result["blocked"] == [{"id": "y", "reason": "missing_dependency", "causes": ["gone"]}]


def test_dag_blocked_output_is_sorted_by_id_and_does_not_depend_on_input_order():
    tasks = [dtask("c", "PLANNED"), dtask("a", "PLANNED", "c"), dtask("b", "PLANNED", "q"),
             dtask("d", "PLANNED", "e"), dtask("e", "PLANNED", "d"), dtask("f", "DONE")]
    first = call("orch_dag_blocked", {"tasks": tasks})
    assert first["ready"] == ["c"] and first["waiting"] == ["a"]
    assert [row["id"] for row in first["blocked"]] == ["b", "d", "e"]
    assert call("orch_dag_blocked", {"tasks": list(reversed(tasks))}) == first
    assert call("orch_dag_blocked", {"tasks": tasks}) == first


def test_dag_blocked_does_not_mutate_the_payload():
    payload = {"tasks": [dtask("b", "PLANNED", "a", "a"), dtask("a", "DONE")]}
    snapshot = json.dumps(payload)
    call("orch_dag_blocked", payload)
    assert json.dumps(payload) == snapshot


def test_dag_blocked_duplicate_task_ids_are_invalid_input_naming_tasks_and_the_sorted_duplicates():
    result = blocked(dtask("b", "PLANNED"), dtask("a", "PLANNED"), dtask("b", "DONE"), dtask("a", "DONE"),
                     dtask("c", "DONE"))
    assert result == {"state": "INVALID_INPUT", "field": "tasks", "message": "duplicate task ids: a, b"}


@pytest.mark.parametrize("payload,field", [
    ({}, "tasks"),
    ({"tasks": []}, "tasks"),
    ({"tasks": "a"}, "tasks"),
    ({"tasks": None}, "tasks"),
    ({"tasks": [{"id": "a", "state": "PLANNED"}]}, "tasks"),
    ({"tasks": [{"id": "a", "depends_on": []}]}, "tasks"),
    ({"tasks": [{"state": "PLANNED", "depends_on": []}]}, "tasks"),
    ({"tasks": [dtask("", "PLANNED")]}, "tasks"),
    ({"tasks": [dtask("a" * 65, "PLANNED")]}, "tasks"),
    ({"tasks": [dtask("a", "planned")]}, "tasks"),
    ({"tasks": [dtask("a", "PLANNED", "")]}, "tasks"),
    ({"tasks": [dtask("a", "PLANNED", "b" * 65)]}, "tasks"),
    ({"tasks": [dtask("a", "PLANNED", *[f"d{i}" for i in range(51)])]}, "tasks"),
    ({"tasks": [{**dtask("a", "PLANNED"), "extra": 1}]}, "tasks"),
    ({"tasks": [dtask("a", "PLANNED")], "extra": 1}, "extra"),
    ({"tasks": [dtask("a", "PLANNED")], "probe": "other"}, "probe"),
])
def test_dag_blocked_malformed_input_is_rejected_naming_the_field(payload, field):
    result = call("orch_dag_blocked", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_dag_blocked_non_object_payload_is_invalid_input_naming_payload():
    result = call("orch_dag_blocked", ["tasks"])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "payload"


def test_dag_blocked_schema_limits_are_inclusive_at_the_boundary():
    ok_deps = [f"d{i}" for i in range(50)]
    result = blocked(*[dtask(dep, "DONE") for dep in ok_deps], dtask("x", "PLANNED", *ok_deps), dtask("a" * 64, "PLANNED"))
    assert result["ready"] == sorted(["x", "a" * 64]) and result["blocked"] == []
    # 200 tasks pass, 201 do not
    chain = [dtask("t000", "DONE")] + [dtask(f"t{i:03d}", "PLANNED", f"t{i - 1:03d}") for i in range(1, 200)]
    assert call("orch_dag_blocked", {"tasks": chain})["state"] == "SUCCESS"
    too_many = chain + [dtask("t200", "PLANNED", "t199")]
    result = call("orch_dag_blocked", {"tasks": too_many})
    assert result["state"] == "INVALID_INPUT" and result["field"] == "tasks"


def test_dag_blocked_a_200_task_chain_and_a_200_task_cycle_finish_within_the_tool_timeout():
    chain = [dtask("t000", "DONE")] + [dtask(f"t{i:03d}", "PLANNED", f"t{i - 1:03d}") for i in range(1, 200)]
    started = time.monotonic()
    result = call("orch_dag_blocked", {"tasks": chain})
    assert result["ready"] == ["t001"] and len(result["waiting"]) == 198 and result["unresolved"] == []
    ring = [dtask(f"r{i:03d}", "PLANNED", f"r{(i + 1) % 200:03d}") for i in range(200)]
    result = call("orch_dag_blocked", {"tasks": ring})
    assert len(result["blocked"]) == 200 and result["ready"] == [] and len(result["unresolved"]) == 200
    assert time.monotonic() - started < 2.0


def test_dag_blocked_probes_short_circuit_without_classifying():
    plan = [dtask("a", "PLANNED")]
    assert call("orch_dag_blocked", {"tasks": plan, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS",
                                                                           "completed_fraction": 0.5}
    assert call("orch_dag_blocked", {"tasks": plan, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call("orch_dag_blocked", {"tasks": plan, "probe": "sleep"})["state"] == "TIMEOUT"


def rule(risk: str, *gates: str) -> dict:
    return {"risk": risk, "gates": list(gates)}


def verdicts(*pairs: tuple[str, str]) -> list[dict]:
    return [{"gate": gate, "verdict": verdict} for gate, verdict in pairs]


def coverage(rules: list[dict], risk: str, results: list[dict]) -> dict:
    return call("orch_gate_coverage", {"rules": rules, "risk": risk, "results": results})


MED = [rule("low", "review"), rule("medium", "review", "quality"), rule("high", "review", "quality", "security")]


def test_gate_coverage_is_registered_for_a01_only_and_refuses_every_other_agent():
    from swarm import tool_registry

    tool = tool_registry.find_tool(tool_registry.load_document(), "orch_gate_coverage")
    assert tool["owner"] == "A01" and tool["permitted_callers"] == ["A01"]
    for agent in other_agents():
        result = call("orch_gate_coverage", {"rules": MED, "risk": "low", "results": []}, caller=agent)
        assert result["state"] == "UNAUTHORIZED" and result["caller"] == agent
        assert result["target"] == "orch_gate_coverage"


def test_gate_coverage_every_required_gate_passing_is_covered():
    result = coverage(MED, "medium", verdicts(("review", "pass"), ("quality", "pass")))
    assert result == {"state": "SUCCESS", "covered": True, "passed": ["review", "quality"], "waived": [], "failed": [],
                      "missing": [], "extra": []}


def test_gate_coverage_a_waived_gate_counts_as_covered_and_is_listed_separately():
    result = coverage(MED, "high", verdicts(("review", "pass"), ("quality", "waive"), ("security", "pass")))
    assert result["covered"] is True and result["passed"] == ["review", "security"] and result["waived"] == ["quality"]


def test_gate_coverage_a_failed_gate_leaves_the_task_uncovered():
    result = coverage(MED, "medium", verdicts(("review", "pass"), ("quality", "fail")))
    assert result == {"state": "SUCCESS", "covered": False, "passed": ["review"], "waived": [], "failed": ["quality"],
                      "missing": [], "extra": []}


def test_gate_coverage_a_gate_without_any_verdict_is_missing_and_uncovered():
    result = coverage(MED, "high", verdicts(("review", "pass")))
    assert result["covered"] is False and result["missing"] == ["quality", "security"] and result["passed"] == ["review"]


def test_gate_coverage_with_no_results_every_required_gate_is_missing():
    result = coverage(MED, "high", [])
    assert result == {"state": "SUCCESS", "covered": False, "passed": [], "waived": [], "failed": [],
                      "missing": ["review", "quality", "security"], "extra": []}


def test_gate_coverage_the_latest_verdict_per_gate_wins_a_rerun_pass_clears_an_earlier_fail():
    result = coverage(MED, "low", verdicts(("review", "fail"), ("review", "pass")))
    assert result["covered"] is True and result["passed"] == ["review"] and result["failed"] == []


def test_gate_coverage_a_later_fail_overrides_an_earlier_pass_and_waive():
    assert coverage(MED, "low", verdicts(("review", "pass"), ("review", "fail")))["failed"] == ["review"]
    result = coverage(MED, "low", verdicts(("review", "waive"), ("review", "fail")))
    assert result["covered"] is False and result["waived"] == [] and result["failed"] == ["review"]
    result = coverage(MED, "low", verdicts(("review", "fail"), ("review", "waive")))
    assert result["covered"] is True and result["waived"] == ["review"]


def test_gate_coverage_verdicts_for_gates_the_risk_does_not_require_are_extra_and_never_block():
    result = coverage(MED, "low", verdicts(("zeta", "fail"), ("review", "pass"), ("alpha", "pass"), ("zeta", "pass")))
    assert result["covered"] is True and result["extra"] == ["alpha", "zeta"] and result["failed"] == []


def test_gate_coverage_required_gates_keep_rule_order_and_a_repeated_gate_is_counted_once():
    rules = [rule("r", "security", "review", "security", "quality", "review")]
    result = coverage(rules, "r", verdicts(("quality", "pass"), ("review", "pass")))
    assert result["passed"] == ["review", "quality"] and result["missing"] == ["security"]
    assert result["covered"] is False


def test_gate_coverage_selects_the_rule_for_the_requested_risk_and_ignores_the_others():
    results = verdicts(("review", "pass"))
    assert coverage(MED, "low", results)["covered"] is True
    assert coverage(MED, "medium", results)["missing"] == ["quality"]
    assert coverage(MED, "high", results)["missing"] == ["quality", "security"]


def test_gate_coverage_risk_matching_is_exact_and_case_sensitive():
    result = coverage(MED, "Low", [])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "risk"
    assert result["message"] == "no rule for risk 'Low'"


def test_gate_coverage_a_risk_with_no_rule_is_invalid_input_naming_risk():
    result = coverage(MED, "critical", verdicts(("review", "pass")))
    assert result == {"state": "INVALID_INPUT", "field": "risk", "message": "no rule for risk 'critical'"}


def test_gate_coverage_duplicate_risk_rules_are_invalid_input_naming_rules_even_for_an_unknown_risk():
    expected = {"state": "INVALID_INPUT", "field": "rules", "message": "duplicate risk class"}
    assert coverage([rule("low", "review"), rule("low", "quality")], "low", []) == expected
    assert coverage([rule("low", "review"), rule("low", "review")], "nope", []) == expected


@pytest.mark.parametrize("payload,field", [
    ({}, "rules"),
    ({"rules": [], "risk": "low", "results": []}, "rules"),
    ({"rules": "low", "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("low")], "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("", "a")], "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("x" * 33, "a")], "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("low", "")], "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("low", "g" * 65)], "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("low", *[f"g{i}" for i in range(21)])], "risk": "low", "results": []}, "rules"),
    ({"rules": [{"risk": "low", "gates": ["a"], "x": 1}], "risk": "low", "results": []}, "rules"),
    ({"rules": [{"gates": ["a"]}], "risk": "low", "results": []}, "rules"),
    ({"rules": [rule("low", "a")], "results": []}, "risk"),
    ({"rules": [rule("low", "a")], "risk": "", "results": []}, "risk"),
    ({"rules": [rule("low", "a")], "risk": "x" * 33, "results": []}, "risk"),
    ({"rules": [rule("low", "a")], "risk": 3, "results": []}, "risk"),
    ({"rules": [rule("low", "a")], "risk": "low"}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [{"gate": "a", "verdict": "PASS"}]}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [{"gate": "a", "verdict": "ok"}]}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [{"gate": "", "verdict": "pass"}]}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [{"gate": "g" * 65, "verdict": "pass"}]}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [{"gate": "a"}]}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [{"gate": "a", "verdict": "pass", "x": 1}]}, "results"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [], "extra": 1}, "extra"),
    ({"rules": [rule("low", "a")], "risk": "low", "results": [], "probe": "x"}, "probe"),
])
def test_gate_coverage_malformed_input_is_rejected_naming_the_field(payload, field):
    result = call("orch_gate_coverage", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result


def test_gate_coverage_schema_limits_are_inclusive_at_the_boundary():
    gates = [f"g{i:02d}" for i in range(20)]
    rules = [rule(f"r{i:02d}", "review") for i in range(19)] + [rule("x" * 32, *gates)]
    results = verdicts(*[(gate, "pass") for gate in gates])
    assert coverage(rules, "x" * 32, results)["covered"] is True
    assert len(rules) == 20
    assert coverage(rules + [rule("r99", "review")], "x" * 32, results)["field"] == "rules"  # 21 rules
    long_gate = "g" * 64
    assert coverage([rule("low", long_gate)], "low", verdicts((long_gate, "pass")))["passed"] == [long_gate]


def test_gate_coverage_accepts_200_results_and_rejects_201_naming_results():
    rows = verdicts(*[("review", "fail" if i % 2 else "pass") for i in range(200)])
    result = coverage(MED, "low", rows)
    assert result["state"] == "SUCCESS" and result["covered"] is False and result["failed"] == ["review"]
    too_many = coverage(MED, "low", rows + verdicts(("review", "pass")))
    assert too_many["state"] == "INVALID_INPUT" and too_many["field"] == "results"


def test_gate_coverage_does_not_mutate_the_payload_and_is_deterministic():
    payload = {"rules": [rule("r", "b", "a", "b")], "risk": "r", "results": verdicts(("c", "pass"), ("a", "fail"))}
    snapshot = json.dumps(payload)
    first = call("orch_gate_coverage", payload)
    assert call("orch_gate_coverage", payload) == first and json.dumps(payload) == snapshot
    assert first["failed"] == ["a"] and first["missing"] == ["b"] and first["extra"] == ["c"]


def test_gate_coverage_probes_short_circuit_without_evaluating_the_rules():
    base = {"rules": MED, "risk": "nope", "results": []}  # would be INVALID_INPUT without the probe
    assert call("orch_gate_coverage", {**base, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS",
                                                                       "completed_fraction": 0.5}
    assert call("orch_gate_coverage", {**base, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call("orch_gate_coverage", {**base, "probe": "sleep"})["state"] == "TIMEOUT"


def bound(rework_loops: int, blocking_findings: int, **extra) -> dict:
    return call("orch_rework_bound", {"rework_loops": rework_loops, "blocking_findings": blocking_findings, **extra})


def test_rework_bound_is_registered_for_a01_only_and_refuses_every_other_agent(work):
    from swarm import tool_registry

    tool = tool_registry.find_tool(tool_registry.load_document(), "orch_rework_bound")
    assert tool["owner"] == "A01" and tool["permitted_callers"] == ["A01"]
    for agent in other_agents():
        result = call("orch_rework_bound", {"rework_loops": 0, "blocking_findings": 1}, caller=agent)
        assert result == {"state": "UNAUTHORIZED", "caller": agent, "target": "orch_rework_bound",
                          "reason": "caller is not permitted"}


@pytest.mark.parametrize("loops,remaining", [(0, 2), (1, 1), (2, 0), (3, 0), (100, 0)])
def test_rework_bound_no_blocking_findings_passes_and_leaves_the_counter_untouched(loops, remaining):
    assert bound(loops, 0) == {"state": "SUCCESS", "decision": "PASS", "rework_loops": loops,
                               "loops_remaining": remaining}


@pytest.mark.parametrize("loops,decision,after,remaining", [
    (0, "REWORK", 1, 1),
    (1, "REWORK", 2, 0),
    (2, "ESCALATED", 2, 0),
    (3, "ESCALATED", 3, 0),
    (100, "ESCALATED", 100, 0),
])
def test_rework_bound_default_cap_is_two_reworks_then_escalation(loops, decision, after, remaining):
    assert bound(loops, 1) == {"state": "SUCCESS", "decision": decision, "rework_loops": after,
                               "loops_remaining": remaining}


@pytest.mark.parametrize("cap,loops,decision,after,remaining", [
    (1, 0, "REWORK", 1, 0),
    (1, 1, "ESCALATED", 1, 0),
    (3, 2, "REWORK", 3, 0),
    (3, 3, "ESCALATED", 3, 0),
    (10, 9, "REWORK", 10, 0),
    (10, 10, "ESCALATED", 10, 0),
    (10, 0, "REWORK", 1, 9),
    (2, 5, "ESCALATED", 5, 0),  # a counter already past the cap escalates and never reports negative loops left
])
def test_rework_bound_max_loops_moves_the_boundary(cap, loops, decision, after, remaining):
    result = bound(loops, 4, max_loops=cap)
    assert result == {"state": "SUCCESS", "decision": decision, "rework_loops": after, "loops_remaining": remaining}


def test_rework_bound_an_explicit_cap_of_two_matches_the_default():
    for loops in range(0, 5):
        assert bound(loops, 1, max_loops=2) == bound(loops, 1)


@pytest.mark.parametrize("findings", [1, 2, 9999, 10000])
def test_rework_bound_the_decision_depends_only_on_whether_findings_exist_not_how_many(findings):
    assert bound(1, findings)["decision"] == "REWORK" and bound(2, findings)["decision"] == "ESCALATED"


@pytest.mark.parametrize("cap", list(range(1, 11)))
def test_rework_bound_feeding_the_answer_back_grants_exactly_cap_reworks_then_escalates(cap):
    loops, reworks = 0, 0
    for _ in range(cap + 3):
        result = bound(loops, 3, max_loops=cap)
        if result["decision"] == "ESCALATED":
            break
        assert result["decision"] == "REWORK"
        reworks += 1
        loops = result["rework_loops"]
    assert reworks == cap and result["decision"] == "ESCALATED" and result["rework_loops"] == cap
    assert result["loops_remaining"] == 0


@pytest.mark.parametrize("payload,field", [
    ({}, "rework_loops"),
    ({"rework_loops": 0}, "blocking_findings"),
    ({"blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": -1, "blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": 101, "blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": "1", "blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": 1.5, "blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": True, "blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": None, "blocking_findings": 0}, "rework_loops"),
    ({"rework_loops": 0, "blocking_findings": -1}, "blocking_findings"),
    ({"rework_loops": 0, "blocking_findings": 10001}, "blocking_findings"),
    ({"rework_loops": 0, "blocking_findings": False}, "blocking_findings"),
    ({"rework_loops": 0, "blocking_findings": "2"}, "blocking_findings"),
    ({"rework_loops": 0, "blocking_findings": 1, "max_loops": 0}, "max_loops"),
    ({"rework_loops": 0, "blocking_findings": 1, "max_loops": 11}, "max_loops"),
    ({"rework_loops": 0, "blocking_findings": 1, "max_loops": None}, "max_loops"),
    ({"rework_loops": 0, "blocking_findings": 1, "max_loops": 2.0}, "max_loops"),
    ({"rework_loops": 0, "blocking_findings": 1, "extra": 1}, "extra"),
    ({"rework_loops": 0, "blocking_findings": 1, "probe": "x"}, "probe"),
])
def test_rework_bound_malformed_input_is_rejected_naming_the_field(payload, field):
    result = call("orch_rework_bound", payload)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field, result


def test_rework_bound_schema_limits_are_inclusive_at_the_boundary():
    assert bound(100, 0)["rework_loops"] == 100
    assert bound(0, 10000)["decision"] == "REWORK"
    assert bound(0, 1, max_loops=1)["decision"] == "REWORK" and bound(0, 1, max_loops=10)["loops_remaining"] == 9


def test_rework_bound_is_deterministic_writes_nothing_and_leaves_the_payload_alone(work):
    payload = {"rework_loops": 1, "blocking_findings": 2, "max_loops": 3}
    snapshot = json.dumps(payload)
    first = call("orch_rework_bound", payload)
    assert first == {"state": "SUCCESS", "decision": "REWORK", "rework_loops": 2, "loops_remaining": 1}
    assert call("orch_rework_bound", payload) == first and json.dumps(payload) == snapshot
    assert not (work / ".swarm").exists()


def test_rework_bound_probes_short_circuit_before_the_cap_is_applied():
    base = {"rework_loops": 0, "blocking_findings": 1}
    assert call("orch_rework_bound", {**base, "probe": "partial"}) == {"state": "PARTIAL_SUCCESS",
                                                                      "completed_fraction": 0.5}
    assert call("orch_rework_bound", {**base, "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call("orch_rework_bound", {**base, "probe": "sleep"})["state"] == "TIMEOUT"
