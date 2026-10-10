"""Lane tools for A06–A10. File-disjoint from the other Phase 3 batches."""
from __future__ import annotations

from pathlib import Path

from swarm.tools.support import ROOT, respond, script


def a11y_scan(payload: dict) -> dict:
    def work() -> dict:
        issues = script("fe_a11y_check").check_text(payload["html"], "snippet.html")
        return {"state": "SUCCESS", "issue_count": len(issues)}

    return respond(payload, work)


def migration_down(payload: dict) -> dict:
    def work() -> dict:
        reversible = script("data_migration_check").has_down(Path("migration.py"), payload["sql"])
        return {"state": "SUCCESS", "reversible": reversible}

    return respond(payload, work)


def runner_detect(payload: dict) -> dict:
    def work() -> dict:
        if payload["scan"] is not True:
            return {"state": "INVALID_INPUT", "field": "scan", "message": "scan must be true"}
        names = [item["name"] for item in script("qa_gate").detect_runners(ROOT)]
        return {"state": "SUCCESS", "runner_count": len(names), "has_pytest": "pytest" in names}

    return respond(payload, work)


def diff_size(payload: dict) -> dict:
    def work() -> dict:
        over = payload["added"] + payload["deleted"] > payload["max_lines"]
        return {"state": "SUCCESS", "over_budget": over}

    return respond(payload, work)


def line_entropy(payload: dict) -> dict:
    def work() -> dict:
        text = payload["text"]
        if not text:
            return {"state": "INVALID_INPUT", "field": "text", "message": "text must be non-empty"}
        return {"state": "SUCCESS", "entropy": script("sec_gate")._entropy(text)}

    return respond(payload, work)
