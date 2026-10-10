"""Shared tools promoted in the Phase 4 dedup review. Each one is already called from more than one lane."""
from __future__ import annotations

from swarm.gates import derive_verdict
from swarm.tools.support import respond, script, target_root

_SEVERITIES = {"info", "minor", "major", "critical", "blocker"}


def _scan(payload: dict) -> dict | None:
    if payload["scan"] is not True:
        return {"state": "INVALID_INPUT", "field": "scan", "message": "scan must be true"}
    return None


def verdict(payload: dict) -> dict:
    def work() -> dict:
        severities = payload["severities"]
        if not isinstance(severities, list):
            return {"state": "INVALID_INPUT", "field": "severities", "message": "severities must be a list"}
        findings = []
        for severity in severities:
            if not isinstance(severity, str) or severity not in _SEVERITIES:
                return {"state": "INVALID_INPUT", "field": "severities", "message": f"unknown severity {severity!r}"}
            findings.append({"severity": severity})
        return {"state": "SUCCESS", "verdict": derive_verdict(findings)}

    return respond(payload, work)


def toolchain(payload: dict) -> dict:
    def work() -> dict:
        refused = _scan(payload)
        if refused:
            return refused
        flags = script("code_checks").detect_toolchains(target_root())
        return {"state": "SUCCESS", "python": bool(flags["python"]), "js": bool(flags["js"])}

    return respond(payload, work)


def check_plan(payload: dict) -> dict:
    def work() -> dict:
        refused = _scan(payload)
        if refused:
            return refused
        checks = script("code_checks")
        root = target_root()
        flags = checks.detect_toolchains(root)
        names = [entry["name"] for entry in checks.plan(root, flags, None)]
        return {"state": "SUCCESS", "names": ",".join(names)}

    return respond(payload, work)


def git_head(payload: dict) -> dict:
    def work() -> dict:
        refused = _scan(payload)
        if refused:
            return refused
        info = script("devops_build_record").git_info(target_root())
        if not info.get("sha"):
            raise OSError(info.get("git") or "git head unavailable")
        return {"state": "SUCCESS", "sha": info["sha"], "branch": info["branch"] or ""}

    return respond(payload, work)
