"""A02 (REQ) requirements tools: story INVEST and ambiguity checks, duplicate-story detection,
WSJF ranking, acceptance-criteria testability, traceability and NFR coverage, change impact.

Tools: req_invest_score, req_ambiguity_scan, req_story_duplicates, req_wsjf_rank,
req_criteria_testability, req_traceability_coverage, req_change_impact, req_nfr_coverage.
Pure functions over the payload; standard library only.
"""
from __future__ import annotations

import re

from swarm.tools.support import respond

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
_SENTENCE_END = re.compile(r"[.!?]+")
_MEASURABLE = re.compile(r"\d|\bthen\b")
_MODALS = frozenset({"could", "likely", "may", "might", "perhaps", "possibly", "should"})
_QUANTIFIERS = frozenset({"few", "many", "most", "numerous", "several", "some", "various"})
_VAGUE = frozenset({
    "adequate", "appropriate", "approximately", "easily", "easy", "efficient", "etc", "fast",
    "flexible", "good", "intuitive", "minimal", "quick", "quickly", "reasonable", "robust",
    "seamless", "simple", "soon", "sufficient", "timely", "typically", "usually",
})
_AMBIGUOUS = _MODALS | _QUANTIFIERS | _VAGUE
# Solution-prescribing markers: a story that names them is not negotiable (INVEST N).
_HOW_WORDS = frozenset({
    "api", "cache", "column", "endpoint", "graphql", "json", "kafka", "microservice", "mysql",
    "postgres", "queue", "redis", "sql", "table", "using", "via", "websocket",
})
_STOP = frozenset({
    "a", "an", "and", "as", "be", "can", "for", "from", "i", "in", "is", "it", "its", "must",
    "my", "of", "on", "or", "our", "should", "so", "that", "the", "to", "want", "will", "with",
})
_NFR_KINDS = ("perf", "security", "a11y", "i18n", "compliance")
_CLARIFY_RATIO = 0.4  # ambiguity score above this requires a clarification.request
_MANUAL_LIMIT = 0.3  # manual-only criteria share above this requires clarification
_SCOPE_LIMIT = 0.2  # committed scope change above this requires human approval
_SMALL_POINTS = 8  # INVEST Small: estimate at or below this
_DUP_THRESHOLD = 0.6  # default Jaccard similarity for duplicate stories
_WSJF_MIN_STORIES = 20  # WSJF policy applies above this many stories; MoSCoW otherwise


def _tokens(text: str) -> frozenset[str]:
    kept = set()
    for word in _WORD.findall(text.lower()):
        if word in _STOP:
            continue
        if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        kept.add(word)
    return frozenset(kept)


def invest_score(payload: dict) -> dict:
    def work() -> dict:
        words = _WORD.findall(payload["story"].lower())
        if not words:
            return {"state": "INVALID_INPUT", "field": "story", "message": "story must contain words"}
        flat = f" {' '.join(words)} "
        estimate = payload.get("estimate_points")
        checks = (
            ("I", not payload.get("dependencies")),
            ("N", _HOW_WORDS.isdisjoint(words)),
            ("V", " so that " in flat or " in order to " in flat),
            ("E", estimate is not None),
            ("S", estimate is not None and estimate <= _SMALL_POINTS),
            ("T", bool(payload.get("criteria"))),
        )
        failed = [letter for letter, ok in checks if not ok]
        return {"state": "SUCCESS", "score": len(checks) - len(failed), "failed": failed}

    return respond(payload, work)


def ambiguity_scan(payload: dict) -> dict:
    def work() -> dict:
        lowered = payload["text"].lower()
        if not _WORD.search(lowered):
            return {"state": "INVALID_INPUT", "field": "text", "message": "text must contain words"}
        sentences = [part for part in _SENTENCE_END.split(lowered) if _WORD.search(part)]
        terms: set[str] = set()
        flagged = 0
        for sentence in sentences:
            hits = [word for word in _WORD.findall(sentence) if word in _AMBIGUOUS]
            if hits:
                flagged += 1
                terms.update(hits)
        ratio = flagged / len(sentences)
        return {
            "state": "SUCCESS",
            "score": round(ratio, 4),
            "sentence_count": len(sentences),
            "flagged_sentences": flagged,
            "terms": sorted(terms),
            "needs_clarification": ratio > _CLARIFY_RATIO,
        }

    return respond(payload, work)


def story_duplicates(payload: dict) -> dict:
    def work() -> dict:
        stories = payload["stories"]
        ids = [story["id"] for story in stories]
        if len(set(ids)) != len(ids):
            return {"state": "INVALID_INPUT", "field": "stories", "message": "story ids must be unique"}
        threshold = payload.get("threshold", _DUP_THRESHOLD)
        sets = [(story["id"], _tokens(story["text"])) for story in stories]
        pairs = []
        for index, (left_id, left) in enumerate(sets):
            for right_id, right in sets[index + 1 :]:
                union = left | right
                if not union:
                    continue
                similarity = len(left & right) / len(union)
                if similarity >= threshold:
                    pairs.append({"left": left_id, "right": right_id, "similarity": round(similarity, 4)})
        pairs.sort(key=lambda pair: (-pair["similarity"], pair["left"], pair["right"]))
        return {"state": "SUCCESS", "pair_count": len(pairs), "pairs": pairs}

    return respond(payload, work)


def wsjf_rank(payload: dict) -> dict:
    def work() -> dict:
        items = payload["items"]
        ids = [item["id"] for item in items]
        if len(set(ids)) != len(ids):
            return {"state": "INVALID_INPUT", "field": "items", "message": "item ids must be unique"}
        scored = [
            (
                item["id"],
                (item["business_value"] + item["time_criticality"] + item["risk_reduction"]) / item["job_size"],
            )
            for item in items
        ]
        scored.sort(key=lambda row: (-row[1], row[0]))
        ranked = [
            {"id": item_id, "wsjf": round(value, 4), "rank": rank}
            for rank, (item_id, value) in enumerate(scored, start=1)
        ]
        return {
            "state": "SUCCESS",
            "method": "WSJF" if len(items) > _WSJF_MIN_STORIES else "MOSCOW",
            "top_id": ranked[0]["id"],
            "ranked": ranked,
        }

    return respond(payload, work)


def criteria_testability(payload: dict) -> dict:
    def work() -> dict:
        criteria = payload["criteria"]
        ids = [criterion["id"] for criterion in criteria]
        if len(set(ids)) != len(ids):
            return {"state": "INVALID_INPUT", "field": "criteria", "message": "criterion ids must be unique"}
        total = len(criteria)
        automated = sum(1 for criterion in criteria if criterion["automated"])
        manual = total - automated
        unmeasurable = [
            criterion["id"]
            for criterion in criteria
            if not criterion["automated"] and not _MEASURABLE.search(criterion["text"].lower())
        ]
        ratio = manual / total
        return {
            "state": "SUCCESS",
            "total": total,
            "automated": automated,
            "manual_only": manual,
            "manual_ratio": round(ratio, 4),
            "unmeasurable_ids": unmeasurable,
            "needs_clarification": ratio > _MANUAL_LIMIT,
        }

    return respond(payload, work)


def traceability_coverage(payload: dict) -> dict:
    def work() -> dict:
        requirements = payload["requirement_ids"]
        if len(set(requirements)) != len(requirements):
            return {"state": "INVALID_INPUT", "field": "requirement_ids", "message": "requirement ids must be unique"}
        known = set(requirements)
        links = payload["links"]
        covered = {link["requirement_id"] for link in links if link["requirement_id"] in known}
        artifacts = {link["artifact_id"] for link in links if link["requirement_id"] in known}
        unknown = sorted({link["requirement_id"] for link in links} - known)
        ratio = len(covered) / len(requirements)
        return {
            "state": "SUCCESS",
            "total": len(requirements),
            "covered": len(covered),
            "coverage": round(ratio, 4),
            "uncovered": [req for req in requirements if req not in covered],
            "unknown_requirement_ids": unknown,
            "linked_artifacts": len(artifacts),
            "meets_target": ratio >= payload.get("target", 1.0),
        }

    return respond(payload, work)


def change_impact(payload: dict) -> dict:
    def work() -> dict:
        affected = set(payload["affected_ids"])
        committed = set(payload["committed_ids"])
        tasks = sorted({link["task_id"] for link in payload["task_links"] if link["requirement_id"] in affected})
        affected_committed = len(affected & committed)
        ratio = affected_committed / len(committed)
        return {
            "state": "SUCCESS",
            "tasks_at_risk": len(tasks),
            "affected_tasks": tasks,
            "affected_committed": affected_committed,
            "scope_change_ratio": round(ratio, 4),
            "needs_human_approval": ratio > _SCOPE_LIMIT,
        }

    return respond(payload, work)


def nfr_coverage(payload: dict) -> dict:
    def work() -> dict:
        nfrs = payload["nfrs"]
        present = {nfr["kind"] for nfr in nfrs}
        required = list(dict.fromkeys(payload.get("required_kinds", _NFR_KINDS)))
        missing = [kind for kind in required if kind not in present]
        return {
            "state": "SUCCESS",
            "nfr_count": len(nfrs),
            "present_kinds": sorted(present),
            "missing_kinds": missing,
            "coverage": round((len(required) - len(missing)) / len(required), 4),
            "complete": not missing,
        }

    return respond(payload, work)
