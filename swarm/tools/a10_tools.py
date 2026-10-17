"""A10 security lane: deterministic checks over payload data for the security gate.

Mission: give sec_gate triage and the verdict cross-check reproducible evidence.
Tools: sec_credential_scan, sec_cvss_band, sec_dependency_age, sec_license_risk,
sec_tls_min_version, sec_header_presence, sec_kev_match_count, sec_sbom_summary.
Pure logic: no I/O, clock, environment, or randomness. Credential values are never returned.
"""
from __future__ import annotations

import datetime as dt
import re

from swarm.tools.support import respond

# Heuristic detectors. The sec_gate scan remains the authority; these only report rule hits.
_CREDENTIAL_RULES = (
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,255}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
    ("stripe_live_key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    (
        "generic_secret_assignment",
        re.compile(
            r"[a-z0-9_-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)[a-z0-9_-]*"
            r"\s*[:=]\s*[\"']?[^\s\"'*<>{}$=]{8,}",
            re.IGNORECASE,
        ),
    ),
)
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_TLS_RANK = {"SSL2": 0, "SSL3": 1, "TLS1.0": 2, "TLS1.1": 3, "TLS1.2": 4, "TLS1.3": 5}
_CVE_ID = re.compile(r"CVE-\d{4}-\d{4,}")
# Exact dotted-numeric version with optional pre-release and build metadata.
# Ranges, wildcards, tags such as "latest", and missing versions are not pinned.
_PINNED_VERSION = re.compile(r"v?\d+(?:\.\d+)*(?:-[0-9A-Za-z.]+)?(?:\+[0-9A-Za-z.]+)?")


def credential_scan(payload: dict) -> dict:
    def work() -> dict:
        # Match objects are discarded; only the rule name and 1-based line number leave this function.
        findings = [
            {"rule": name, "line": number}
            for number, line in enumerate(payload["lines"], start=1)
            for name, pattern in _CREDENTIAL_RULES
            if pattern.search(line)
        ]
        return {"state": "SUCCESS", "finding_count": len(findings), "findings": findings}

    return respond(payload, work)


def cvss_band(payload: dict) -> dict:
    def work() -> dict:
        score = payload["score"]
        if score == 0:
            band = "none"
        elif score < 4.0:
            band = "low"
        elif score < 7.0:
            band = "medium"
        elif score < 9.0:
            band = "high"
        else:
            band = "critical"
        return {"state": "SUCCESS", "severity": band, "gate_blocking": score >= 7.0}

    return respond(payload, work)


def _day(text: str) -> dt.date | None:
    if not _ISO_DAY.fullmatch(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def dependency_age(payload: dict) -> dict:
    def work() -> dict:
        released = _day(payload["released"])
        if released is None:
            return {"state": "INVALID_INPUT", "field": "released", "message": "released must be a valid YYYY-MM-DD date"}
        as_of = _day(payload["as_of"])
        if as_of is None:
            return {"state": "INVALID_INPUT", "field": "as_of", "message": "as_of must be a valid YYYY-MM-DD date"}
        age = (as_of - released).days
        if age < 0:
            return {"state": "INVALID_INPUT", "field": "released", "message": "released is after as_of"}
        return {"state": "SUCCESS", "age_days": age, "stale": age > payload["max_age_days"]}

    return respond(payload, work)


def license_risk(payload: dict) -> dict:
    def work() -> dict:
        licenses = {value.strip() for value in payload["licenses"]}
        allowed = {value.strip().lower() for value in payload["allowlist"]}
        if "" in licenses:
            return {"state": "INVALID_INPUT", "field": "licenses", "message": "license identifiers must not be blank"}
        if "" in allowed:
            return {"state": "INVALID_INPUT", "field": "allowlist", "message": "allowlist entries must not be blank"}
        unapproved = sorted(item for item in licenses if item.lower() not in allowed)
        return {
            "state": "SUCCESS",
            "license_ok": not unapproved,
            "unapproved": unapproved,
            "unapproved_count": len(unapproved),
        }

    return respond(payload, work)


def _tls_key(text: str) -> str:
    """Canonical key: 'TLSv1.2', 'TLS 1.2' and 'tls1.2' all map to 'TLS1.2'."""
    key = re.sub(r"[^0-9A-Z.]", "", text.upper())
    return key.replace("TLSV", "TLS").replace("SSLV", "SSL")


def tls_min_version(payload: dict) -> dict:
    def work() -> dict:
        floor = _TLS_RANK[_tls_key(payload["minimum_version"])]
        below: set[str] = set()
        unrecognized: set[str] = set()
        for raw in payload["protocols"]:
            if not raw.strip():
                return {"state": "INVALID_INPUT", "field": "protocols", "message": "protocol names must not be blank"}
            key = _tls_key(raw)
            if key not in _TLS_RANK:
                unrecognized.add(raw.strip())
            elif _TLS_RANK[key] < floor:
                below.add(key)
        return {
            "state": "SUCCESS",
            "compliant": not below and not unrecognized,
            "below_minimum": sorted(below),
            "unrecognized": sorted(unrecognized),
        }

    return respond(payload, work)


def header_presence(payload: dict) -> dict:
    def work() -> dict:
        observed = {value.strip().lower() for value in payload["headers"]}
        required = {value.strip().lower() for value in payload["required_headers"]}
        if "" in observed:
            return {"state": "INVALID_INPUT", "field": "headers", "message": "header names must not be blank"}
        if "" in required:
            return {"state": "INVALID_INPUT", "field": "required_headers", "message": "header names must not be blank"}
        missing = sorted(required - observed)
        return {"state": "SUCCESS", "complete": not missing, "missing": missing, "missing_count": len(missing)}

    return respond(payload, work)


def _cve_set(values: list[str]) -> set[str] | None:
    cves = {value.strip().upper() for value in values}
    if not all(_CVE_ID.fullmatch(cve) for cve in cves):
        return None
    return cves


def kev_match_count(payload: dict) -> dict:
    def work() -> dict:
        checked = _cve_set(payload["cve_ids"])
        if checked is None:
            return {"state": "INVALID_INPUT", "field": "cve_ids", "message": "entries must be CVE-YYYY-NNNN identifiers"}
        kev = _cve_set(payload["kev_ids"])
        if kev is None:
            return {"state": "INVALID_INPUT", "field": "kev_ids", "message": "entries must be CVE-YYYY-NNNN identifiers"}
        matched = sorted(checked & kev)
        return {
            "state": "SUCCESS",
            "checked_count": len(checked),
            "matched": matched,
            "matched_count": len(matched),
            "blocking": bool(matched),
        }

    return respond(payload, work)


def sbom_summary(payload: dict) -> dict:
    def work() -> dict:
        unpinned_names: set[str] = set()
        unpinned_count = 0
        for component in payload["components"]:
            name = component["name"].strip()
            if not name:
                return {"state": "INVALID_INPUT", "field": "components", "message": "component names must not be blank"}
            version = component.get("version")
            if not (isinstance(version, str) and _PINNED_VERSION.fullmatch(version.strip())):
                unpinned_count += 1
                unpinned_names.add(name)
        return {
            "state": "SUCCESS",
            "component_count": len(payload["components"]),
            "unpinned_count": unpinned_count,
            "unpinned": sorted(unpinned_names),
            "all_pinned": unpinned_count == 0,
        }

    return respond(payload, work)
