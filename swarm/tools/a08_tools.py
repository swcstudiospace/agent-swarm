"""Lane tools for A08 (Test Engineer): deterministic quality-gate arithmetic and classification.

Mission: decide flake quarantine, criteria coverage, mutation and escape thresholds, suite
runtime budget, skip and rerun budgets, and test-name conventions from data A08 already holds.

Tools: qa_flaky_classify, qa_criteria_coverage, qa_mutation_gate, qa_runtime_p95,
qa_skip_ratio, qa_defect_escape_rate, qa_retry_budget, qa_name_convention.
"""
from __future__ import annotations

import re

from swarm.tools.support import respond

FLAKE_WINDOW = 10
FLAKE_QUARANTINE_FAILS = 3
MUTATION_THRESHOLD_PCT = 70.0
RUNTIME_BUDGET_S = 2700.0
DETECTION_MIN_PCT = 90.0
ESCAPE_MAX_PCT = 5.0
NAME_MAX_LENGTH = 80
_SNAKE_CASE = re.compile(r"[a-z0-9_]+")


def _invalid(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def _first_duplicate(values):
    seen: set = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2)


def _flake_state(outcomes: list[str]) -> str:
    """Last FLAKE_WINDOW non-skipped runs. Fails with passes present are flaky, not broken."""
    runs = [outcome for outcome in outcomes if outcome != "skip"][-FLAKE_WINDOW:]
    if not runs:
        return "unobserved"
    fails = runs.count("fail")
    if fails == 0:
        return "stable"
    if "pass" not in runs:
        return "consistent_fail"
    return "quarantine" if fails >= FLAKE_QUARANTINE_FAILS else "watch"


def flaky_classify(payload: dict) -> dict:
    def work() -> dict:
        histories = payload["histories"]
        duplicate = _first_duplicate(item["test"] for item in histories)
        if duplicate is not None:
            return _invalid("histories", f"duplicate test {duplicate}")
        buckets: dict[str, list[str]] = {
            "quarantine": [],
            "watch": [],
            "consistent_fail": [],
            "unobserved": [],
            "stable": [],
        }
        for item in histories:
            buckets[_flake_state(item["outcomes"])].append(item["test"])
        return {
            "state": "SUCCESS",
            "quarantine": buckets["quarantine"],
            "watch": buckets["watch"],
            "consistent_fail": buckets["consistent_fail"],
            "unobserved": buckets["unobserved"],
            "stable_count": len(buckets["stable"]),
            "evaluated": len(histories),
        }

    return respond(payload, work)


def criteria_coverage(payload: dict) -> dict:
    def work() -> dict:
        criteria = payload["criteria"]
        tests = payload["tests"]
        duplicate = _first_duplicate(criteria)
        if duplicate is not None:
            return _invalid("criteria", f"duplicate criterion {duplicate}")
        duplicate = _first_duplicate(test["name"] for test in tests)
        if duplicate is not None:
            return _invalid("tests", f"duplicate test {duplicate}")
        known = set(criteria)
        covered = {ref for test in tests for ref in test["ac_refs"] if ref in known}
        return {
            "state": "SUCCESS",
            "criteria_count": len(criteria),
            "covered_count": len(covered),
            "coverage_pct": _pct(len(covered), len(criteria)),
            "uncovered": [criterion for criterion in criteria if criterion not in covered],
            "orphan_tests": [test["name"] for test in tests if not known.intersection(test["ac_refs"])],
        }

    return respond(payload, work)


def mutation_gate(payload: dict) -> dict:
    def work() -> dict:
        killed = payload["killed"]
        survived = payload["survived"]
        timeout = payload.get("timeout", 0)
        scored = killed + timeout + survived
        if scored == 0:
            return _invalid("killed", "no scored mutants")
        threshold = payload.get("threshold_pct", MUTATION_THRESHOLD_PCT)
        # Timeouts count as detected (Stryker convention); uncovered mutants are reported as survived.
        raw = 100.0 * (killed + timeout) / scored
        return {
            "state": "SUCCESS",
            "scored_mutants": scored,
            "score_pct": round(raw, 2),
            "threshold_pct": float(threshold),
            "meets_threshold": raw >= threshold,
        }

    return respond(payload, work)


def runtime_p95(payload: dict) -> dict:
    def work() -> dict:
        durations = sorted(payload["durations_s"])
        count = len(durations)
        rank = (95 * count + 99) // 100  # nearest-rank: ceil(0.95 * count), integer arithmetic
        p95 = durations[rank - 1]
        budget = payload.get("budget_s", RUNTIME_BUDGET_S)
        return {
            "state": "SUCCESS",
            "sample_count": count,
            "p95_s": float(p95),
            "mean_s": round(sum(durations) / count, 3),
            "max_s": float(durations[-1]),
            "budget_s": float(budget),
            "within_budget": p95 <= budget,
        }

    return respond(payload, work)


def skip_ratio(payload: dict) -> dict:
    def work() -> dict:
        tests = payload["tests"]
        duplicate = _first_duplicate(test["name"] for test in tests)
        if duplicate is not None:
            return _invalid("tests", f"duplicate test {duplicate}")
        skipped = [test for test in tests if test["status"] == "skipped"]
        return {
            "state": "SUCCESS",
            "total": len(tests),
            "skipped": len(skipped),
            "skip_pct": _pct(len(skipped), len(tests)),
            "unexplained": [test["name"] for test in skipped if not test.get("reason", "").strip()],
            "within_limit": 100.0 * len(skipped) / len(tests) <= payload["max_skip_pct"],
        }

    return respond(payload, work)


def defect_escape_rate(payload: dict) -> dict:
    def work() -> dict:
        defects = payload["defects"]
        duplicate = _first_duplicate(defect["id"] for defect in defects)
        if duplicate is not None:
            return _invalid("defects", f"duplicate defect {duplicate}")
        escaped = [defect["id"] for defect in defects if defect["stage"] == "production"]
        total = len(defects)
        pre_prod = total - len(escaped)
        detection = 100.0 * pre_prod / total
        escape = 100.0 * len(escaped) / total
        return {
            "state": "SUCCESS",
            "total": total,
            "pre_prod": pre_prod,
            "escaped": len(escaped),
            "detection_pct": round(detection, 2),
            "escape_pct": round(escape, 2),
            "escaped_ids": escaped,
            "meets_detection": detection >= payload.get("detection_min_pct", DETECTION_MIN_PCT),
            "meets_escape": escape < payload.get("escape_max_pct", ESCAPE_MAX_PCT),
        }

    return respond(payload, work)


def retry_budget(payload: dict) -> dict:
    def work() -> dict:
        runs = payload["runs"]
        duplicate = _first_duplicate(run["test"] for run in runs)
        if duplicate is not None:
            return _invalid("runs", f"duplicate test {duplicate}")
        limit = payload["max_reruns"]
        return {
            "state": "SUCCESS",
            "evaluated": len(runs),
            "total_reruns": sum(len(run["attempts"]) - 1 for run in runs),
            "over_budget": [run["test"] for run in runs if len(run["attempts"]) - 1 > limit],
            "rescued": [run["test"] for run in runs if run["attempts"][0] == "fail" and "pass" in run["attempts"]],
            "unresolved": [run["test"] for run in runs if "pass" not in run["attempts"]],
        }

    return respond(payload, work)


def name_convention(payload: dict) -> dict:
    def work() -> dict:
        prefix = payload["prefix"]
        limit = payload.get("max_length", NAME_MAX_LENGTH)
        violations: list[str] = []
        seen: set[str] = set()
        for name in payload["names"]:
            if name in seen:
                violations.append(f"{name}: duplicate name")
            seen.add(name)
            if not name.startswith(prefix):
                violations.append(f"{name}: missing prefix {prefix}")
            if _SNAKE_CASE.fullmatch(name) is None:
                violations.append(f"{name}: not snake_case")
            if len(name) > limit:
                violations.append(f"{name}: longer than {limit}")
        return {
            "state": "SUCCESS",
            "checked": len(payload["names"]),
            "violations": violations,
            "conforming": not violations,
        }

    return respond(payload, work)
