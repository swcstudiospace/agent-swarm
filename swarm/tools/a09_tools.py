"""A09 review-gate tools: pure checks over data the reviewer already holds.

Mission: produce measurable review evidence (restricted paths, auto-approve
thresholds, latency, severities, escape rate, comment density, test pairing,
open threads) for the signed review verdict. Tools:
  rev_sensitive_paths      restricted-path categories and co-sign need for changed paths
  rev_auto_approve_check   L2 auto-approve thresholds from decision_logic
  rev_review_latency_p95   nearest-rank P95 of review durations against target
  rev_severity_histogram   severity counts, blocking count and highest severity
  rev_false_approve_rate   defects later found in approved code, against target
  rev_comment_density      comment-line ratio of added source lines
  rev_test_pairing         test-to-source ratio and source files without a test
  rev_unresolved_threads   open review threads, including open blocking threads
"""
from __future__ import annotations

import os
import re

from swarm.tools.support import respond

_SEVERITIES = ("info", "minor", "major", "critical", "blocker")
_BLOCKING_FROM = _SEVERITIES.index("major")  # same threshold as swarm.gates.BLOCKING_SEVERITY

# Restricted-path categories, matched on whole word tokens of the path.
_CATEGORY_WORDS = {
    "contracts": {"openapi", "asyncapi", "swagger", "proto", "graphql", "contract", "contracts"},
    "auth": {"auth", "authn", "authz", "oauth", "oidc", "login", "logout", "session", "sessions", "jwt",
             "rbac", "acl", "permission", "permissions", "password", "passwords", "credential",
             "credentials", "sso", "saml", "mfa"},
    "payments": {"payment", "payments", "billing", "invoice", "invoices", "checkout", "stripe", "ledger",
                 "refund", "refunds"},
    "migrations": {"migration", "migrations", "migrate", "alembic", "flyway", "liquibase"},
    "pii": {"pii", "gdpr", "privacy", "ssn"},
    "iac": {"terraform", "tf", "tfvars", "helm", "k8s", "kubernetes", "ansible", "pulumi", "cloudformation",
            "dockerfile"},
}
_CO_SIGN_CATEGORIES = frozenset({"auth", "payments", "pii", "iac"})  # high-risk: A10 co-sign required
_TOKEN = re.compile(r"[a-z0-9]+")

_CODE_EXTENSIONS = frozenset({".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".java", ".kt",
                              ".rb", ".rs", ".cs", ".php", ".swift", ".scala", ".c", ".h", ".cc", ".cpp",
                              ".hpp", ".ex", ".exs"})
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs"})
_TEST_SUFFIXES = ("_test", ".test", "_spec", ".spec")

_COMMENT_PREFIXES = ("#", "//", "/*", "*/", "* ", "--", "<!--")
_DIRECTIVES = ("#include", "#define", "#if", "#elif", "#else", "#endif", "#pragma", "#undef", "#error",
               "#import", "#!")


def _bad(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def _categories(path: str) -> set[str]:
    tokens = set(_TOKEN.findall(path.replace("\\", "/").lower()))
    return {name for name, words in _CATEGORY_WORDS.items() if tokens & words}


def sensitive_paths(payload: dict) -> dict:
    def work() -> dict:
        flagged: list[str] = []
        categories: set[str] = set()
        for path in dict.fromkeys(payload["paths"]):
            hits = _categories(path)
            if hits:
                flagged.append(path)
                categories |= hits
        return {
            "state": "SUCCESS",
            "sensitive_count": len(flagged),
            "categories": sorted(categories),
            "flagged_paths": flagged,
            "co_sign_required": bool(categories & _CO_SIGN_CATEGORIES),
        }

    return respond(payload, work)


def auto_approve_check(payload: dict) -> dict:
    def work() -> dict:
        diff_lines = payload["added"] + payload["deleted"]
        failed: list[str] = []
        if diff_lines >= 100:
            failed.append("diff_size")
        # Any restricted category (contracts, auth, payments, migrations, PII, IaC) blocks auto-approve.
        if any(_categories(path) for path in payload["changed_paths"]):
            failed.append("restricted_paths")
        if payload["sast_findings"] != 0:
            failed.append("sast")
        if not payload["tests_present"]:
            failed.append("tests")
        if payload["first_pass_pct"] <= 90:
            failed.append("first_pass_rate")
        auto = not failed
        return {
            "state": "SUCCESS",
            "auto_approve": auto,
            "route": "auto_approve" if auto else "full_semantic_review",
            "failed_criteria": failed,
            "diff_lines": diff_lines,
        }

    return respond(payload, work)


def review_latency_p95(payload: dict) -> dict:
    def work() -> dict:
        ordered = sorted(payload["durations_min"])
        rank = (95 * len(ordered) + 99) // 100  # nearest-rank: ceil(0.95 * n), 1-based
        p95 = float(ordered[rank - 1])
        return {
            "state": "SUCCESS",
            "sample_count": len(ordered),
            "p95_min": round(p95, 2),
            "meets_target": p95 < payload["target_min"],
        }

    return respond(payload, work)


def severity_histogram(payload: dict) -> dict:
    def work() -> dict:
        counts = {name: 0 for name in _SEVERITIES}
        for severity in payload["severities"]:
            counts[severity] += 1
        present = [name for name in _SEVERITIES if counts[name]]
        return {
            "state": "SUCCESS",
            "total": len(payload["severities"]),
            **counts,
            "blocking_count": sum(counts[name] for name in _SEVERITIES[_BLOCKING_FROM:]),
            "max_severity": present[-1] if present else "none",
        }

    return respond(payload, work)


def false_approve_rate(payload: dict) -> dict:
    def work() -> dict:
        approved = payload["approved_count"]
        defects = payload["defects_found_later"]
        if defects > approved:
            return _bad("defects_found_later", "cannot exceed approved_count")
        raw_rate = defects * 100 / approved
        return {
            "state": "SUCCESS",
            "rate_pct": round(raw_rate, 2),
            "within_target": raw_rate < payload["target_pct"],
        }

    return respond(payload, work)


def _is_comment(line: str) -> bool:
    if line.startswith(_DIRECTIVES):
        return False
    return line.startswith(_COMMENT_PREFIXES) or line == "*"


def comment_density(payload: dict) -> dict:
    def work() -> dict:
        nonblank = 0
        comments = 0
        for raw in payload["added_lines"]:
            line = raw.strip()
            if not line:
                continue
            nonblank += 1
            if _is_comment(line):
                comments += 1
        density = comments / nonblank if nonblank else 0.0
        return {
            "state": "SUCCESS",
            "nonblank_lines": nonblank,
            "comment_lines": comments,
            "density": round(density, 4),
        }

    return respond(payload, work)


def _split(path: str) -> tuple[list[str], str, str]:
    *dirs, base = path.replace("\\", "/").lower().split("/")
    stem, ext = os.path.splitext(base)
    return dirs, stem, ext


def _test_base(dirs: list[str], stem: str) -> str | None:
    """Source stem a test file covers, or None when the file is not a test."""
    for suffix in _TEST_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    if stem.startswith("test_"):
        return stem[len("test_"):]
    if any(part in _TEST_DIRS for part in dirs):
        return stem
    return None


def test_pairing(payload: dict) -> dict:
    def work() -> dict:
        sources: list[tuple[str, str]] = []
        covered: set[str] = set()
        test_count = 0
        for path in dict.fromkeys(payload["changed_paths"]):
            dirs, stem, ext = _split(path)
            if ext not in _CODE_EXTENSIONS:
                continue
            base = _test_base(dirs, stem)
            if base is None:
                sources.append((path, stem))
            else:
                test_count += 1
                covered.add(base)
        untested = [path for path, stem in sources if stem not in covered]
        ratio = test_count / len(sources) if sources else 0.0
        return {
            "state": "SUCCESS",
            "source_files": len(sources),
            "test_files": test_count,
            "test_to_source_ratio": round(ratio, 4),
            "untested_sources": untested,
            "all_sources_paired": not untested,
        }

    return respond(payload, work)


def unresolved_threads(payload: dict) -> dict:
    def work() -> dict:
        threads = payload["threads"]
        ids = [thread["id"] for thread in threads]
        if len(set(ids)) != len(ids):
            return _bad("threads", "thread ids must be unique")
        open_threads = [thread for thread in threads if not thread["resolved"]]
        open_blocking = [thread for thread in open_threads if thread["blocking"]]
        return {
            "state": "SUCCESS",
            "total_threads": len(threads),
            "unresolved_count": len(open_threads),
            "unresolved_blocking_count": len(open_blocking),
            "unresolved_ids": [thread["id"] for thread in open_threads],
            "blocking_clear": not open_blocking,
        }

    return respond(payload, work)
