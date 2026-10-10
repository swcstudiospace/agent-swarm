"""Lane tools for A01–A05. File-disjoint from the other Phase 3 batches."""
from __future__ import annotations

from swarm.errors import SwarmError
from swarm.tools.support import respond, script


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
            if not all(word in lowered for word in ("given", "when", "then")):
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
        try:
            ratio = script("ux_tokens").contrast(payload["fg"], payload["bg"])
        except ValueError as exc:
            return {"state": "INVALID_INPUT", "field": "fg", "message": str(exc)}
        return {"state": "SUCCESS", "ratio": ratio}

    return respond(payload, work)


def route_norm(payload: dict) -> dict:
    def work() -> dict:
        return {"state": "SUCCESS", "normalized": script("be_contract_conformance").norm_path(payload["path"])}

    return respond(payload, work)
