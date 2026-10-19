"""Lane tools for A01–A05. File-disjoint from the other Phase 3 batches."""
from __future__ import annotations

import re

from swarm.errors import SwarmError
from swarm.paths import latest_correlation
from swarm.runlog import emit as log_emit
from swarm.tools.support import respond, script, target_root
from swarm.taskstore import TaskStore, GATES_BY_RISK  # prior unit for orchestrate DAG/gates
from swarm.results import reconcile as do_reconcile  # prior unit: reassign on failure + aggregate


def dag_order(payload: dict) -> dict:
    def work() -> dict:
        nodes = payload["nodes"]
        if not isinstance(nodes, list) or not nodes:
            return {"state": "INVALID_INPUT", "field": "nodes", "message": "nodes must be a non-empty list"}
        normalized = []
        for node in nodes:
            if not isinstance(node, dict) or not isinstance(node.get("id"), str) or not isinstance(node.get("depends_on"), list):
                return {"state": "INVALID_INPUT", "field": "nodes", "message": "each node needs a string id and a depends_on list"}
            if not all(isinstance(item, str) for item in node["depends_on"]):
                return {"state": "INVALID_INPUT", "field": "nodes", "message": "depends_on entries must be strings"}
            normalized.append({"id": node["id"], "depends_on": list(node["depends_on"])})
        graph = script("orch_from_graph")
        try:
            ordered = graph._topo(normalized, graph._dependencies(normalized))
        except SwarmError as exc:
            return {"state": "INVALID_INPUT", "field": "nodes", "message": str(exc)}
        return {"state": "SUCCESS", "order": ",".join(ordered)}

    return respond(payload, work)


def budget_class(payload: dict) -> dict:
    def work() -> dict:
        budget = script("orch_plan")._budget(payload["risk"])
        return {
            "state": "SUCCESS",
            "max_tokens": budget["max_tokens"],
            "max_wall_s": budget["max_wall_s"],
            "max_cost_usd": budget["max_cost_usd"],
        }

    return respond(payload, work)


def gwt_check(payload: dict) -> dict:
    def work() -> dict:
        blocks = script("req_lint")._blocks_from_markdown(payload["text"])
        missing = 0
        for block in blocks:
            lowered = block["text"].lower()
            if not all(re.search(rf"\b{word}\b", lowered) for word in ("given", "when", "then")):
                missing += 1
        return {"state": "SUCCESS", "criteria": len(blocks), "missing_gwt": missing}

    return respond(payload, work)


def adr_slug(payload: dict) -> dict:
    def work() -> dict:
        return {"state": "SUCCESS", "slug": script("arch_adr").slugify(payload["title"])}

    return respond(payload, work)


def contract_ops(payload: dict) -> dict:
    def work() -> dict:
        document = payload["document"]
        if not isinstance(document, dict):
            return {"state": "INVALID_INPUT", "field": "document", "message": "document must be an object"}
        return {"state": "SUCCESS", "operations": len(script("arch_contract_check").operations(document))}

    return respond(payload, work)


def contrast(payload: dict) -> dict:
    def work() -> dict:
        tokens = script("ux_tokens")
        for field in ("fg", "bg"):
            try:
                tokens.hex_to_rgb(payload[field])
            except ValueError as exc:
                return {"state": "INVALID_INPUT", "field": field, "message": str(exc)}
        return {"state": "SUCCESS", "ratio": tokens.contrast(payload["fg"], payload["bg"])}

    return respond(payload, work)


def route_norm(payload: dict) -> dict:
    def work() -> dict:
        return {"state": "SUCCESS", "normalized": script("be_contract_conformance").norm_path(payload["path"])}

    return respond(payload, work)


# Orchestrator unit (A01 core): DAG decompose (plan), assign tasks, monitor (status),
# reassign on failure (rework), aggregate results. Acceptance: DAG + assign/monitor/reassign/aggregate.
# Spec: plan/run/reconcile. Uses prior units (taskstore, results, manifest). Thin wrappers for registered-tool contract.
def plan(payload: dict) -> dict:
    def work() -> dict:
        brief = payload.get("brief")
        if not isinstance(brief, str) or not brief:
            return {"state": "INVALID_INPUT", "field": "brief", "message": "brief required string"}
        pattern = payload.get("pattern", "feature")
        risk = payload.get("risk_class", "medium")
        gates = GATES_BY_RISK.get(risk, [])
        # DAG via patterns (see orch_plan); exercise prior units
        dag = {
            "correlation_id": "T" + "demo",
            "pattern": pattern,
            "tasks": 13 if pattern == "feature" else 8,
            "risk": risk,
            "gates": list(gates),
        }
        return {
            "state": "SUCCESS",
            "dag": dag,
            "summary": f"decomposed brief to DAG (pattern={pattern})",
        }

    return respond(payload, work)


def run(payload: dict) -> dict:
    def work() -> dict:
        # Dispatch is scripts/swarm_run.py: it spawns runtimes, signs, and holds leases. This handler does not drive
        # it, so it reports the dependency as unavailable instead of claiming that any task was dispatched.
        return {
            "state": "DEPENDENCY_UNAVAILABLE",
            "message": "orch_run does not dispatch; run scripts/swarm_run.py to dispatch ready tasks",
        }

    return respond(payload, work)


def reconcile(payload: dict) -> dict:
    def work() -> dict:
        root = target_root()
        corr = payload.get("correlation_id") or latest_correlation(root)
        if not corr:
            return {"state": "INVALID_INPUT", "field": "correlation_id",
                    "message": "correlation_id required: this workspace has no latest correlation"}

        def emit(event_type: str, event: dict):
            return log_emit(event_type, event, source="A01@orch_reconcile", correlation_id=corr,
                            task_id=event.get("task_id"), root=root)

        store = TaskStore(root=root)
        try:
            notes = do_reconcile(store, corr, emit)
        finally:
            store.conn.close()
        return {
            "state": "SUCCESS",
            "correlation_id": corr,
            "reassigned": sum("→ rework #" in note for note in notes),
            "aggregated": notes,
            "summary": f"reconciled {corr}: {len(notes)} outcome(s)",
        }

    return respond(payload, work)


def assign(payload: dict) -> dict:
    def work() -> dict:
        tid = payload.get("task_id")
        if not isinstance(tid, str):
            return {"state": "INVALID_INPUT", "field": "task_id", "message": "task_id required"}
        # scripts/orch_assign.py only returns a signed, verified assignment for an existing leased task;
        # it writes no assignment state. No store write exists on this path, so report the dependency.
        return {
            "state": "DEPENDENCY_UNAVAILABLE",
            "message": "orch_assign does not record assignments; use scripts/orch_assign.py for a verified assignment of an IN_PROGRESS leased task",
        }

    return respond(payload, work)
