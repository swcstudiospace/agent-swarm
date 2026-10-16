"""A15 documentation tools: deterministic checks over markdown text and supplied registries.

Mission: verify internal links and heading anchors, Keep-a-Changelog section order,
conventional-commit grouping, API and ADR render coverage, prose readability,
document staleness and runbook verification dates. Pure functions over the payload.

Tools: docs_link_targets, docs_anchor_check, docs_changelog_section_order,
docs_conventional_groups, docs_api_endpoint_coverage, docs_flesch_grade,
docs_stale_doc_age, docs_runbook_verified_ratio, docs_adr_render_coverage.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from datetime import date

from swarm.tools.support import respond

CHANGELOG_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
CONVENTIONAL_SECTION = {"feat": "added", "fix": "fixed", "perf": "changed", "refactor": "changed"}
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
ADR_RENDER_SCOPE = ("accepted", "superseded", "deprecated")

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_PATH_PARAM = re.compile(r"\{[^{}/]+\}|:[A-Za-z_]\w*")
_CONVENTIONAL = re.compile(r"^([A-Za-z]+)(?:\([^()\s]+\))?(!)?: \S")
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
_SENTENCE_END = re.compile(r"[.!?]+")
_VOWEL_RUN = re.compile(r"[aeiouy]+")


def _unique(items) -> list:
    return list(dict.fromkeys(items))


def _duplicates(items) -> list:
    return [item for item, count in Counter(items).items() if count > 1]


def _invalid(field: str, message: str) -> dict:
    return {"state": "INVALID_INPUT", "field": field, "message": message}


def _parse_date(value) -> date | None:
    if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _link_path(source: str, target: str) -> str | None:
    """Repo-relative path a link targets, or None when the link leaves the repo."""
    target = target.strip()
    if target.startswith("//") or _SCHEME.match(target):
        return None
    path = target.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return source
    if path.startswith("/"):
        return posixpath.normpath(path.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), path))


def _with_parents(files: set) -> set:
    dirs: set = set()
    for path in files:
        parent = posixpath.dirname(path)
        while parent and parent not in dirs:
            dirs.add(parent)
            parent = posixpath.dirname(parent)
    return dirs


def docs_link_targets(payload: dict) -> dict:
    def work() -> dict:
        files = {posixpath.normpath(path) for path in payload["files"]}
        known = files | _with_parents(files) | {"."}
        checked = external = 0
        broken: list[str] = []
        for link in payload["links"]:
            path = _link_path(posixpath.normpath(link["source"]), link["target"])
            if path is None:
                external += 1
                continue
            checked += 1
            if path not in known:
                broken.append(f"{link['source']} -> {link['target']}")
        return {
            "state": "SUCCESS",
            "checked": checked,
            "external": external,
            "broken_count": len(broken),
            "broken": broken,
        }

    return respond(payload, work)


def _slug(text: str) -> str:
    """GitHub-style heading slug: lowercase, spaces to hyphens, keep letters, digits, '-' and '_'."""
    return "".join(
        ("-" if ch == " " else ch) for ch in text.strip().lower() if ch == " " or ch.isalnum() or ch in "-_"
    )


def _heading_anchors(headings: list) -> set:
    """Anchors for ordered headings; repeated slugs get -1, -2 suffixes as GitHub does."""
    occurrences: dict[str, int] = {}
    anchors: set = set()
    for text in headings:
        base = _slug(text)
        seen = occurrences.get(base, 0)
        anchors.add(base if seen == 0 else f"{base}-{seen}")
        occurrences[base] = seen + 1
    return anchors


def docs_anchor_check(payload: dict) -> dict:
    def work() -> dict:
        anchors = _heading_anchors(payload["headings"])
        broken = [ref for ref in payload["references"] if ref.lstrip("#") and ref.lstrip("#") not in anchors]
        return {
            "state": "SUCCESS",
            "headings": len(payload["headings"]),
            "anchors": len(anchors),
            "checked": len(payload["references"]),
            "broken_count": len(broken),
            "broken": broken,
        }

    return respond(payload, work)


def docs_changelog_section_order(payload: dict) -> dict:
    def work() -> dict:
        sections = payload["sections"]
        rank = {name: index for index, name in enumerate(CHANGELOG_ORDER)}
        known = [name for name in sections if name in rank]
        unknown = _unique(name for name in sections if name not in rank)
        duplicates = _unique(_duplicates(sections))
        out_of_order: list[str] = []
        highest = -1
        for name in known:
            if rank[name] < highest:
                out_of_order.append(name)
            highest = max(highest, rank[name])
        out_of_order = _unique(out_of_order)
        return {
            "state": "SUCCESS",
            "valid": not (unknown or duplicates or out_of_order),
            "known": len(known),
            "unknown": unknown,
            "duplicates": duplicates,
            "out_of_order": out_of_order,
        }

    return respond(payload, work)


def docs_conventional_groups(payload: dict) -> dict:
    def work() -> dict:
        counts = {"added": 0, "changed": 0, "fixed": 0, "other": 0}
        breaking = 0
        unparsed: list[str] = []
        for subject in payload["subjects"]:
            match = _CONVENTIONAL.match(subject)
            if match is None:
                unparsed.append(subject)
                continue
            counts[CONVENTIONAL_SECTION.get(match.group(1).lower(), "other")] += 1
            if match.group(2):
                breaking += 1
        return {
            "state": "SUCCESS",
            "total": len(payload["subjects"]),
            "added": counts["added"],
            "changed": counts["changed"],
            "fixed": counts["fixed"],
            "other": counts["other"],
            "breaking": breaking,
            "unparsed": unparsed,
        }

    return respond(payload, work)


def _endpoint_key(text: str) -> tuple[str, str] | None:
    """(normalized key, original text) for 'METHOD /path'; path parameters collapse to {}."""
    parts = text.split(None, 1)
    if len(parts) != 2 or parts[0].upper() not in HTTP_METHODS or not parts[1].startswith("/"):
        return None
    path = _PATH_PARAM.sub("{}", parts[1].strip()).rstrip("/") or "/"
    return f"{parts[0].upper()} {path}", text.strip()


def _endpoint_map(entries: list) -> dict[str, str] | None:
    """Normalized key mapped to first original text; None when an entry is malformed."""
    mapping: dict[str, str] = {}
    for text in entries:
        parsed = _endpoint_key(text)
        if parsed is None:
            return None
        mapping.setdefault(parsed[0], parsed[1])
    return mapping


def docs_api_endpoint_coverage(payload: dict) -> dict:
    def work() -> dict:
        total = _endpoint_map(payload["total_endpoints"])
        if total is None:
            return _invalid("total_endpoints", "each entry must be 'METHOD /path' with a known HTTP method")
        documented = _endpoint_map(payload.get("documented_endpoints", []))
        if documented is None:
            return _invalid("documented_endpoints", "each entry must be 'METHOD /path' with a known HTTP method")
        covered = sum(1 for key in total if key in documented)
        covered_pct = round(covered * 100 / len(total), 2)
        return {
            "state": "SUCCESS",
            "total": len(total),
            "covered": covered,
            "covered_pct": covered_pct,
            "meets_target": covered_pct >= payload.get("target_pct", 100),
            "undocumented": [text for key, text in total.items() if key not in documented],
            "unmatched_documented": [text for key, text in documented.items() if key not in total],
        }

    return respond(payload, work)


def _syllables(word: str) -> int:
    lowered = word.lower()
    count = len(_VOWEL_RUN.findall(lowered))
    if lowered.endswith("e") and not lowered.endswith("le") and count > 1:
        count -= 1
    return max(1, count)


def docs_flesch_grade(payload: dict) -> dict:
    def work() -> dict:
        text = payload["text"]
        words = _WORD.findall(text)
        if not words:
            return _invalid("text", "no words to score")
        sentences = max(1, sum(1 for segment in _SENTENCE_END.split(text) if _WORD.search(segment)))
        syllables = sum(_syllables(word) for word in words)
        words_per_sentence = len(words) / sentences
        syllables_per_word = syllables / len(words)
        grade = round(0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59, 1)
        reading_ease = round(206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word, 1)
        return {
            "state": "SUCCESS",
            "words": len(words),
            "sentences": sentences,
            "syllables": syllables,
            "grade": grade,
            "reading_ease": reading_ease,
            "within_target": grade <= payload.get("max_grade", 9),
        }

    return respond(payload, work)


def docs_stale_doc_age(payload: dict) -> dict:
    def work() -> dict:
        as_of = _parse_date(payload["as_of"])
        if as_of is None:
            return _invalid("as_of", "as_of must be a YYYY-MM-DD date")
        limit = payload["max_age_days"]
        stale: list[str] = []
        invalid: list[str] = []
        ages: list[int] = []
        for doc in payload["docs"]:
            verified = _parse_date(doc["last_verified"])
            if verified is None or verified > as_of:
                invalid.append(doc["path"])
                continue
            age = (as_of - verified).days
            ages.append(age)
            if age > limit:
                stale.append(doc["path"])
        total = len(payload["docs"])
        return {
            "state": "SUCCESS",
            "total": total,
            "checked": len(ages),
            "stale": stale,
            "stale_count": len(stale),
            "stale_pct": round(len(stale) * 100 / total, 2),
            "invalid": invalid,
            "oldest_days": max(ages, default=0),
        }

    return respond(payload, work)


def docs_runbook_verified_ratio(payload: dict) -> dict:
    def work() -> dict:
        as_of = _parse_date(payload["as_of"])
        if as_of is None:
            return _invalid("as_of", "as_of must be a YYYY-MM-DD date")
        duplicates = _duplicates(rb["runbook_id"] for rb in payload["runbooks"])
        if duplicates:
            return _invalid("runbooks", f"duplicate runbook_id {duplicates[0]}")
        tier1 = [rb for rb in payload["runbooks"] if rb["tier"] == 1]
        if not tier1:
            return _invalid("runbooks", "no tier-1 runbook supplied")
        limit = payload["max_age_days"]
        unverified: list[str] = []
        verified_count = 0
        for rb in tier1:
            verified = _parse_date(rb["last_verified"])
            if verified is None or verified > as_of or (as_of - verified).days > limit:
                unverified.append(rb["runbook_id"])
            else:
                verified_count += 1
        verified_pct = round(verified_count * 100 / len(tier1), 2)
        return {
            "state": "SUCCESS",
            "tier1_total": len(tier1),
            "tier1_verified": verified_count,
            "tier1_verified_pct": verified_pct,
            "meets_target": verified_pct >= payload.get("target_pct", 95),
            "unverified": unverified,
        }

    return respond(payload, work)


def docs_adr_render_coverage(payload: dict) -> dict:
    def work() -> dict:
        ids = [adr["id"] for adr in payload["adrs"]]
        duplicates = _duplicates(ids)
        if duplicates:
            return _invalid("adrs", f"duplicate ADR id {duplicates[0]}")
        in_scope = [adr["id"] for adr in payload["adrs"] if adr["status"] in ADR_RENDER_SCOPE]
        if not in_scope:
            return _invalid("adrs", "no accepted, superseded or deprecated ADR to render")
        rendered = _unique(payload["rendered"])
        rendered_set = set(rendered)
        known = set(ids)
        rendered_in_scope = sum(1 for adr_id in in_scope if adr_id in rendered_set)
        adr_rendered_pct = round(rendered_in_scope * 100 / len(in_scope), 2)
        return {
            "state": "SUCCESS",
            "in_scope": len(in_scope),
            "rendered_in_scope": rendered_in_scope,
            "adr_rendered_pct": adr_rendered_pct,
            "meets_target": adr_rendered_pct >= payload.get("target_pct", 100),
            "missing": [adr_id for adr_id in in_scope if adr_id not in rendered_set],
            "orphan_rendered": [adr_id for adr_id in rendered if adr_id not in known],
            "excluded": len(ids) - len(in_scope),
        }

    return respond(payload, work)
