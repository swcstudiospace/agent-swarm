"""A05 backend tools: HTTP contract, dependency, and test-coverage checks over supplied data.

Tools:
- be_openapi_path_params: extract {name} path parameters from an OpenAPI route template.
- be_route_collision: find same-method routes whose templated shapes collide.
- be_method_status_check: check an HTTP method and status code against allowlists.
- be_semver_range_class: classify a semver range and whether it has an upper bound.
- be_license_allowlist_check: find packages whose license expression is not allowed.
- be_coverage_delta: compute the percentage-point coverage change between two runs.
- be_acceptance_coverage: compute acceptance-criteria coverage for a test plan.

All tools are pure: every result is computed from the payload.
"""

from __future__ import annotations

import re

from swarm.tools.support import respond

_PARAM_FORM = re.compile(r"\{[^{}/]*\}|:[A-Za-z_][A-Za-z0-9_]*")
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_METHOD = re.compile(r"[A-Za-z]{1,10}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){0,2}\Z")
_EXACT = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_COMPARATORS = (">=", "<=", ">", "<", "=")
_OR = re.compile(r"\s+OR\s+", re.IGNORECASE)
_AND = re.compile(r"\s+AND\s+", re.IGNORECASE)


def _invalid(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def openapi_path_params(payload: dict) -> dict:
    def work() -> dict:
        template = payload["template"]
        if not template.startswith("/"):
            return _invalid("template", "template must start with '/'")
        names: list[str] = []
        current: list[str] | None = None
        for char in template:
            if char == "{":
                if current is not None:
                    return _invalid("template", "nested '{' in path template")
                current = []
            elif char == "}":
                if current is None:
                    return _invalid("template", "unmatched '}' in path template")
                name = "".join(current)
                if not _PARAM_NAME.match(name):
                    return _invalid("template", f"invalid parameter name {name!r}")
                names.append(name)
                current = None
            elif current is not None:
                current.append(char)
        if current is not None:
            return _invalid("template", "unclosed '{' in path template")
        duplicates = sorted({name for name in names if names.count(name) > 1})
        return {"state": "SUCCESS", "params": names, "param_count": len(names), "duplicates": duplicates}

    return respond(payload, work)


def _route_shape(path: str) -> str:
    shaped = _PARAM_FORM.sub("{}", path)
    return shaped.rstrip("/") or "/"


def route_collision(payload: dict) -> dict:
    def work() -> dict:
        groups: dict[tuple[str, str], list[str]] = {}
        for index, route in enumerate(payload["routes"]):
            method = route["method"].strip().upper()
            path = route["path"].strip()
            if not method or not path.startswith("/"):
                return _invalid("routes", f"route {index} needs a method and a path starting with '/'")
            groups.setdefault((method, _route_shape(path)), []).append(path)
        collisions = [
            f"{method} {shape} <- {', '.join(paths)}"
            for (method, shape), paths in groups.items()
            if len(paths) > 1
        ]
        return {
            "state": "SUCCESS",
            "routes_checked": len(payload["routes"]),
            "collision_count": len(collisions),
            "collisions": collisions,
        }

    return respond(payload, work)


def method_status_check(payload: dict) -> dict:
    def work() -> dict:
        method = payload["method"].strip()
        if not _METHOD.match(method):
            return _invalid("method", "method must be 1-10 ASCII letters")
        status = payload["status"]
        if not 100 <= status <= 599:
            return _invalid("status", "status must be between 100 and 599")
        method_upper = method.upper()
        allowed_methods = {item.strip().upper() for item in payload["allowed_methods"]}
        method_ok = method_upper in allowed_methods
        status_ok = status in set(payload["allowed_statuses"])
        return {
            "state": "SUCCESS",
            "method": method_upper,
            "status_class": status // 100,
            "method_allowed": method_ok,
            "status_allowed": status_ok,
            "verdict": "PASS" if method_ok and status_ok else "FAIL",
        }

    return respond(payload, work)


def semver_range_class(payload: dict) -> dict:
    def work() -> dict:
        text = payload["range"].strip()
        if text in ("", "*", "x", "X"):
            kind, bounded = "wildcard", False
        elif "||" in text:
            return _invalid("range", "union ranges ('||') are not supported")
        elif " - " in text:
            left, _, right = text.partition(" - ")
            if not (_VERSION.match(left.strip()) and _VERSION.match(right.strip())):
                return _invalid("range", "hyphen range needs two versions")
            kind, bounded = "hyphen", True
        elif text[0] in "^~":
            if not _VERSION.match(text[1:]):
                return _invalid("range", f"{text[0]} range needs a version")
            kind, bounded = ("caret" if text[0] == "^" else "tilde"), True
        elif text[0] in "<>=":
            operators: list[str] = []
            for token in text.split():
                operator = next((op for op in _COMPARATORS if token.startswith(op)), None)
                if operator is None or not _VERSION.match(token[len(operator):]):
                    return _invalid("range", f"invalid comparator token {token!r}")
                operators.append(operator)
            kind = "comparator"
            bounded = any(op in ("<", "<=", "=") for op in operators)
        elif _EXACT.match(text):
            kind, bounded = "exact", True
        else:
            return _invalid("range", f"unrecognized semver range {text!r}")
        return {"state": "SUCCESS", "kind": kind, "upper_bounded": bounded}

    return respond(payload, work)


def _license_allowed(expression: str, allowed: set[str]) -> bool:
    # Parentheses are not evaluated; such expressions fail closed.
    if "(" in expression or ")" in expression:
        return False
    cleaned = expression.strip()
    if not cleaned:
        return False
    return any(
        all(term.strip().lower() in allowed for term in _AND.split(clause.strip()))
        for clause in _OR.split(cleaned)
    )


def license_allowlist_check(payload: dict) -> dict:
    def work() -> dict:
        allowed = {item.strip().lower() for item in payload["allowlist"]}
        packages = payload["packages"]
        violations = [
            f"{package['name']} ({package['license'] or 'none'})"
            for package in packages
            if not _license_allowed(package["license"], allowed)
        ]
        return {
            "state": "SUCCESS",
            "checked_count": len(packages),
            "violation_count": len(violations),
            "violations": violations,
        }

    return respond(payload, work)


def coverage_delta(payload: dict) -> dict:
    def work() -> dict:
        before = payload["before"]
        after = payload["after"]
        for field, value in (("before", before), ("after", after)):
            if not 0 <= value <= 100:
                return _invalid(field, "coverage must be between 0 and 100")
        delta = round(after - before, 2) + 0.0
        direction = "up" if delta > 0 else "down" if delta < 0 else "flat"
        return {"state": "SUCCESS", "delta": delta, "direction": direction, "regressed": after < before}

    return respond(payload, work)


def acceptance_coverage(payload: dict) -> dict:
    def work() -> dict:
        criteria = list(dict.fromkeys(payload["criteria"]))
        if not criteria:
            return _invalid("criteria", "at least one acceptance criterion is required")
        criteria_set = set(criteria)
        covered = set(payload["covered"])
        uncovered = [item for item in criteria if item not in covered]
        return {
            "state": "SUCCESS",
            "total_count": len(criteria),
            "covered_count": len(criteria) - len(uncovered),
            "uncovered": uncovered,
            "unmapped": sorted(covered - criteria_set),
            "ratio": round((len(criteria) - len(uncovered)) / len(criteria), 4),
        }

    return respond(payload, work)
