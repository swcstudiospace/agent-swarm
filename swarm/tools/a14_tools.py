"""Lane tools for A14 (Maintenance Engineer): patch priority and deadlines, semver
bump classification, EOL and staleness ages, unpinned-spec counts, tech-debt
scoring, license-change review, and changelog validation.

Tools (all pure; dates, versions, and thresholds come from the payload):
  maint_patch_priority   CVSS/KEV/exploit/blast/usage priority score and tier
  maint_patch_deadline   24 h for KEV or critical findings, otherwise the supplied SLA
  maint_semver_bump      major/minor/patch/prerelease/downgrade kind, breaking flag, autonomy
  maint_eol_distance     days to platform EOL, status, upgrade-epic and compliance flags
  maint_unpinned_count   pinned / wildcard / unpinned counts over dependency specifiers
  maint_stale_age        days a dependency release trails the latest release
  maint_debt_priority    impact x recurrence x effort tech-debt score and iteration
  maint_license_change   SPDX identifier diff, allowlist check, copyleft introduction
  maint_changelog_check  Keep a Changelog release entry validation

Specifier rules for maint_unpinned_count: an empty spec or a bare version counts as
unpinned, a wildcard (`*`, `latest`, `1.x`) counts as wildcard, and only `==`, `===`,
or `=` followed by one exact version counts as pinned.
"""
from __future__ import annotations

import datetime as dt
import re

from swarm.tools.support import respond

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_UTC_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?")
_PIN_BODY = re.compile(r"[0-9A-Za-z.+-]+")
_WILDCARD_X = re.compile(r"(?:^|\.)[xX](?:\.|$)")
_LICENSE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
_COPYLEFT_PREFIXES = ("GPL", "AGPL", "LGPL", "MPL", "EPL", "CDDL", "EUPL")
_CHANGELOG_HEAD = re.compile(r"## \[([^\]]+)\](?: - (\S+))?")
_CHANGELOG_CATEGORIES = frozenset({"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"})
_EXPLOIT_POINTS = {"none": 0, "poc": 10, "functional": 15, "weaponized": 20}
_BLAST_POINTS = {"isolated": 0, "service": 5, "platform": 10, "internet-facing": 15}
_URGENT_WINDOW_H = 24
_EOL_EPIC_DAYS = 90


def _bad(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def _date(text: str) -> dt.date | None:
    if not _ISO_DATE.fullmatch(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _utc(text: str) -> dt.datetime | None:
    if not _UTC_STAMP.fullmatch(text):
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def patch_priority(payload: dict) -> dict:
    """Score = 4*CVSS + exploit points + blast points + 0.15*usage% + 10 if KEV, capped at 100."""

    def work() -> dict:
        kev = payload["kev"]
        cvss = payload["cvss"]
        raw = (
            cvss * 4
            + _EXPLOIT_POINTS[payload["exploit_maturity"]]
            + _BLAST_POINTS[payload["blast_radius"]]
            + payload["usage_pct"] * 0.15
            + (10 if kev else 0)
        )
        score = min(100, round(raw))
        immediate = kev or cvss >= 9.0
        if immediate:
            tier = "immediate"
        elif score >= 70:
            tier = "high"
        elif score >= 40:
            tier = "normal"
        else:
            tier = "low"
        return {"state": "SUCCESS", "score": score, "tier": tier, "immediate": immediate}

    return respond(payload, work)


def patch_deadline(payload: dict) -> dict:
    """KEV or critical severity gets a 24 h window from detection; anything else uses sla_hours."""

    def work() -> dict:
        detected = _utc(payload["detected_at"])
        if detected is None:
            return _bad("detected_at", "detected_at must be YYYY-MM-DDTHH:MM:SSZ")
        if payload["kev"] or payload["severity"] == "critical":
            window = _URGENT_WINDOW_H
            basis = "kev" if payload["kev"] else "critical"
        else:
            window = payload.get("sla_hours")
            if window is None:
                return _bad("sla_hours", "sla_hours is required unless the finding is KEV or critical")
            basis = "sla"
        deadline = detected + dt.timedelta(hours=window)
        return {
            "state": "SUCCESS",
            "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_hours": window,
            "basis": basis,
        }

    return respond(payload, work)


def _parse_semver(text: str) -> tuple[tuple[int, int, int], str | None] | None:
    match = _SEMVER.fullmatch(text)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3])), match[4]


def _pre_key(pre: str | None) -> tuple:
    """Semver precedence: a release outranks every prerelease of the same core version."""
    if pre is None:
        return (1,)
    idents = []
    for ident in pre.split("."):
        idents.append((0, int(ident), "") if ident.isdigit() else (1, 0, ident))
    return (0, tuple(idents))


def semver_bump(payload: dict) -> dict:
    """Classify from -> to. Breaking means a major change, or a minor change while major is 0."""

    def work() -> dict:
        old = _parse_semver(payload["from"])
        if old is None:
            return _bad("from", "from must be a semver version")
        new = _parse_semver(payload["to"])
        if new is None:
            return _bad("to", "to must be a semver version")
        (old_core, old_pre), (new_core, new_pre) = old, new
        old_key = (old_core, _pre_key(old_pre))
        new_key = (new_core, _pre_key(new_pre))
        if new_key < old_key:
            kind = "downgrade"
        elif new_key == old_key:
            kind = "none"
        elif new_core[0] != old_core[0]:
            kind = "major"
        elif new_core[1] != old_core[1]:
            kind = "minor"
        elif new_core[2] != old_core[2]:
            kind = "patch"
        else:
            kind = "prerelease"
        breaking = kind == "major" or (kind == "minor" and old_core[0] == 0)
        if kind in ("downgrade", "none"):
            autonomy = "none"
        else:
            autonomy = "L3" if breaking else "L2"
        return {"state": "SUCCESS", "kind": kind, "breaking": breaking, "autonomy": autonomy}

    return respond(payload, work)


def eol_distance(payload: dict) -> dict:
    """Days from today to EOL. Negative means passed; an epic is due at 90 days or less."""

    def work() -> dict:
        today = _date(payload["today"])
        if today is None:
            return _bad("today", "today must be YYYY-MM-DD")
        eol = _date(payload["eol_date"])
        if eol is None:
            return _bad("eol_date", "eol_date must be YYYY-MM-DD")
        days = (eol - today).days
        if days < 0:
            status = "passed"
        elif days <= _EOL_EPIC_DAYS:
            status = "within_90_days"
        else:
            status = "supported"
        return {
            "state": "SUCCESS",
            "days_to_eol": days,
            "status": status,
            "upgrade_epic": days <= _EOL_EPIC_DAYS,
            "compliance_finding": days < 0 and payload["in_production"],
        }

    return respond(payload, work)


def _spec_class(spec: str) -> str:
    text = spec.strip()
    if not text:
        return "unconstrained"
    if "*" in text or text.lower() == "latest" or _WILDCARD_X.search(text):
        return "wildcard"
    for op in ("===", "==", "="):
        if text.startswith(op):
            return "pinned" if _PIN_BODY.fullmatch(text[len(op):].strip()) else "range"
    return "range"


def unpinned_count(payload: dict) -> dict:
    """Count specifiers that are not a single exact pin. Empty and wildcard entries are unpinned too."""

    def work() -> dict:
        classes = [_spec_class(spec) for spec in payload["specs"]]
        total = len(classes)
        if total == 0:
            return _bad("specs", "specs must contain at least one entry")
        pinned = classes.count("pinned")
        unpinned = total - pinned
        return {
            "state": "SUCCESS",
            "total": total,
            "pinned": pinned,
            "unpinned": unpinned,
            "wildcard": classes.count("wildcard"),
            "unpinned_pct": round(100 * unpinned / total, 1),
        }

    return respond(payload, work)


def stale_age(payload: dict) -> dict:
    """Days between the installed release date and the latest release date, against a threshold."""

    def work() -> dict:
        installed = _date(payload["installed_released"])
        if installed is None:
            return _bad("installed_released", "installed_released must be YYYY-MM-DD")
        latest = _date(payload["latest_released"])
        if latest is None:
            return _bad("latest_released", "latest_released must be YYYY-MM-DD")
        age = (latest - installed).days
        if age < 0:
            return _bad("latest_released", "latest_released must not precede installed_released")
        threshold = payload["threshold_days"]
        return {
            "state": "SUCCESS",
            "age_days": age,
            "stale": age > threshold,
            "days_over_threshold": max(0, age - threshold),
        }

    return respond(payload, work)


def debt_priority(payload: dict) -> dict:
    """score = 100 * (impact*recurrence/25) * 8/(8+effort_h), capped at 100.

    Iteration: score >= 70 -> I+1, >= 40 -> I+2, otherwise I+3.
    """

    def work() -> dict:
        benefit = payload["impact"] * payload["recurrence"]
        score = min(100, round(100 * (benefit / 25) * (8 / (8 + payload["effort_h"]))))
        if score >= 70:
            iteration = "I+1"
        elif score >= 40:
            iteration = "I+2"
        else:
            iteration = "I+3"
        return {"state": "SUCCESS", "score": score, "proposed_iteration": iteration}

    return respond(payload, work)


def _license_ids(expression: str) -> set[str]:
    """SPDX identifiers in an expression. AND/OR drop out; the exception after WITH is skipped."""
    ids: set[str] = set()
    skip_next = False
    for token in _LICENSE_TOKEN.findall(expression):
        upper = token.upper()
        if skip_next:
            skip_next = False
        elif upper == "WITH":
            skip_next = True
        elif upper not in ("AND", "OR"):
            ids.add(token.lower())
    return ids


def _has_copyleft(ids: set[str]) -> bool:
    return any(ident.upper().startswith(_COPYLEFT_PREFIXES) for ident in ids)


def license_change(payload: dict) -> dict:
    """Compare SPDX identifier sets between two license records."""

    def work() -> dict:
        before = _license_ids(payload["previous_license"])
        if not before:
            return _bad("previous_license", "no SPDX identifier found")
        after = _license_ids(payload["current_license"])
        if not after:
            return _bad("current_license", "no SPDX identifier found")
        allowed_ids = {item.strip().lower() for item in payload["allowlist"]} - {""}
        changed = before != after
        allowed = after <= allowed_ids
        return {
            "state": "SUCCESS",
            "changed": changed,
            "allowed": allowed,
            "copyleft_introduced": _has_copyleft(after) and not _has_copyleft(before),
            "requires_review": changed or not allowed,
        }

    return respond(payload, work)


def _changelog_problems(entry: str, version: str) -> list[str]:
    lines = [line.rstrip() for line in entry.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        return ["entry is empty"]
    problems: list[str] = []
    head = _CHANGELOG_HEAD.fullmatch(lines[0])
    if head is None:
        problems.append("first line must be '## [version] - YYYY-MM-DD' or '## [Unreleased]'")
    else:
        heading_version, stamp = head.group(1), head.group(2)
        if heading_version != version:
            problems.append(f"heading version {heading_version!r} does not match {version!r}")
        if heading_version == "Unreleased":
            if stamp is not None:
                problems.append("Unreleased heading must not carry a date")
        elif stamp is None:
            problems.append("release heading is missing its date")
        elif _date(stamp) is None:
            problems.append(f"release date {stamp!r} is not a valid YYYY-MM-DD date")
    categories = 0
    entries = 0
    in_category = False
    for line in lines[1:]:
        if not line:
            continue
        if line.startswith("## "):
            problems.append("unexpected '## ' heading after the release heading")
        elif line.startswith("### "):
            name = line[4:].strip()
            if name not in _CHANGELOG_CATEGORIES:
                problems.append(f"unknown category {name!r}")
            categories += 1
            in_category = True
        elif line == "-" or line.startswith("- "):
            entries += 1
            if not in_category:
                problems.append("entry appears before any category heading")
            elif not line[1:].strip():
                problems.append("empty entry")
        elif line.startswith("  ") and entries:
            continue
        else:
            problems.append(f"unrecognized line {line[:40]!r}")
    if categories == 0:
        problems.append("no category heading")
    if entries == 0:
        problems.append("no entries")
    return problems


def changelog_check(payload: dict) -> dict:
    """Validate one Keep a Changelog release section against the expected version."""

    def work() -> dict:
        problems = _changelog_problems(payload["entry"], payload["version"])
        return {
            "state": "SUCCESS",
            "valid": not problems,
            "problem_count": len(problems),
            "first_problem": problems[0] if problems else "",
        }

    return respond(payload, work)
