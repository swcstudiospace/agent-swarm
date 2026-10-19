"""Behaviour tests for the v2.2 A02 tools: req_pii_scan, req_clarification_window, req_conflict_matrix.

Handlers are exercised directly; the registry-level checks use a merged deep copy of agents.json so they pass
both before and after registry/intake/a02-requirements.tools.json has been applied.
"""
import copy
import json
from pathlib import Path

import pytest

from swarm import tool_registry
from swarm.tools import a02_tools

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a02-requirements.tools.json"
NEW_TOOLS = ("req_pii_scan", "req_clarification_window", "req_conflict_matrix")


def _fragment_entries() -> list[dict]:
    """The fragment while it is unmerged; the live A02 entries once scripts/_registry_intake.py has applied (and deleted) it."""
    if FRAGMENT.exists():
        return json.loads(FRAGMENT.read_text(encoding="utf-8"))
    agent = next(a for a in tool_registry.load_document()["agents"] if a["id"] == "A02")
    return [tool for tool in agent["registered_tools"] if tool["name"] in NEW_TOOLS]


def _merged_registry() -> dict:
    document = copy.deepcopy(tool_registry.load_document())
    present = {tool["name"] for _container, tool in tool_registry.iter_entries(document)}
    agent = next(a for a in document["agents"] if a["id"] == "A02")
    for entry in _fragment_entries():
        if entry["name"] not in present:
            agent["registered_tools"].append(entry)
    return document


def _call(name: str, payload: dict, caller: str = "A02") -> dict:
    return tool_registry.call_tool(name, caller, payload, data=_merged_registry())


# --- registry ------------------------------------------------------------------------------------


def test_fragment_entries_validate_in_the_merged_registry(tmp_path):
    merged = tmp_path / "agents.json"
    merged.write_text(json.dumps(_merged_registry()), encoding="utf-8")
    assert tool_registry.validate_registry(merged, root=ROOT) == []
    entries = _fragment_entries()
    assert [entry["name"] for entry in entries] == list(NEW_TOOLS)
    for entry in entries:
        assert entry["owner"] == "A02" and entry["permitted_callers"] == ["A02"]
        assert entry["side_effect_tier"] == "READ" and entry["idempotency"] == "idempotent"


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_other_agents_are_unauthorized_and_probes_map_to_states(name):
    document = _merged_registry()
    tool = tool_registry.find_tool(document, name)
    examples = tool["examples"]
    assert tool_registry.call_tool(name, examples["unauthorized_caller"], examples["success"], data=document)["state"] == "UNAUTHORIZED"
    assert tool_registry.call_tool(name, "A02", examples["success"], data=document)["state"] == "SUCCESS"
    assert tool_registry.call_tool(name, "A02", examples["invalid_input"], data=document)["state"] == "INVALID_INPUT"
    assert tool_registry.call_tool(name, "A02", examples["dependency"], data=document)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert tool_registry.call_tool(name, "A02", examples["partial"], data=document)["state"] == "PARTIAL_SUCCESS"


# --- req_pii_scan --------------------------------------------------------------------------------


def _kinds(result: dict) -> dict:
    return {item["kind"]: item["count"] for item in result["found"]}


def test_pii_scan_detects_and_redacts_each_kind():
    text = "Mail jane.doe@example.com, call (415) 555-0100, SSN 123-45-6789, card 4111 1111 1111 1111, host 10.0.0.255."
    result = a02_tools.pii_scan({"text": text})
    assert result["state"] == "SUCCESS" and result["has_pii"] is True
    assert _kinds(result) == {"ssn": 1, "card": 1, "email": 1, "ipv4": 1, "phone": 1}
    assert result["redacted_text"] == (
        "Mail [REDACTED:EMAIL], call [REDACTED:PHONE], SSN [REDACTED:SSN], card [REDACTED:CARD], host [REDACTED:IPV4]."
    )


def test_pii_scan_luhn_invalid_digit_run_is_not_a_card():
    text = "Order ref 1234 5678 9012 3456 shipped."
    result = a02_tools.pii_scan({"text": text})
    assert result["found"] == [] and result["has_pii"] is False
    assert result["redacted_text"] == text


def test_pii_scan_redaction_is_idempotent():
    first = a02_tools.pii_scan({"text": "Reach bob@corp.io or +1 415-555-0199 for access."})
    second = a02_tools.pii_scan({"text": first["redacted_text"]})
    assert first["has_pii"] is True
    assert second["has_pii"] is False and second["redacted_text"] == first["redacted_text"]


def test_pii_scan_kinds_filter_limits_detection():
    text = "bob@corp.io and 123-45-6789"
    result = a02_tools.pii_scan({"text": text, "kinds": ["email"]})
    assert _kinds(result) == {"email": 1}
    assert result["redacted_text"] == "[REDACTED:EMAIL] and 123-45-6789"


def test_pii_scan_regime_hints_follow_the_spec_regimes():
    card = a02_tools.pii_scan({"text": "Pay with 4111111111111111"})
    assert card["regimes_hint"] == ["PCI-DSS"]
    health = a02_tools.pii_scan({"text": "The patient diagnosis is stored for jane@x.org"})
    assert health["regimes_hint"] == ["GDPR", "CCPA", "HIPAA"]
    clean = a02_tools.pii_scan({"text": "Users can export a report as CSV."})
    assert clean["regimes_hint"] == []


def test_pii_scan_rejects_blank_empty_and_oversized_text():
    assert a02_tools.pii_scan({"text": "   \n"}) == {
        "state": "INVALID_INPUT", "field": "text", "message": "text must contain non-whitespace characters",
    }
    assert _call("req_pii_scan", {"text": ""})["field"] == "text"
    assert _call("req_pii_scan", {"text": "a" * 100001})["state"] == "INVALID_INPUT"
    assert _call("req_pii_scan", {"text": "a" * 100000})["state"] == "SUCCESS"
    assert _call("req_pii_scan", {"text": "a." * 50000})["state"] == "SUCCESS"
    assert _call("req_pii_scan", {"text": "1 " * 50000})["state"] == "SUCCESS"


def test_pii_scan_ignores_lookalikes():
    text = "Released 2024-01-15 from 999.1.1.1 with ticket 12345."
    assert a02_tools.pii_scan({"text": text})["found"] == []


def test_pii_scan_finds_international_phone_and_repeated_values():
    result = a02_tools.pii_scan({"text": "UK +44 20 7946 0958, again +44 20 7946 0958, a@b.co and a@b.co"})
    assert _kinds(result) == {"email": 2, "phone": 2}


def test_pii_scan_probe_never_reaches_the_scanner():
    assert a02_tools.pii_scan({"text": "bob@corp.io", "probe": "partial"})["state"] == "PARTIAL_SUCCESS"
    with pytest.raises(OSError):
        a02_tools.pii_scan({"text": "bob@corp.io", "probe": "dependency"})


# --- req_clarification_window --------------------------------------------------------------------

NOW = "2026-01-10T12:00:00Z"


def _round(rid: str, sent: str, answered: str | None = None) -> dict:
    return {"id": rid, "sent_at": sent, **({"answered_at": answered} if answered else {})}


def test_window_without_rounds_allows_a_first_round():
    result = a02_tools.clarification_window({"rounds": [], "now": NOW})
    assert result["rounds_used"] == 0 and result["can_send_round"] is True
    assert result["exhausted"] is False and result["must_go_provisional"] is False
    assert "p50_latency_h" not in result


def test_window_boundary_at_exactly_48_hours_is_overdue():
    exactly = a02_tools.clarification_window({"rounds": [_round("CR-1", "2026-01-08T12:00:00Z")], "now": NOW})
    assert exactly["open_overdue"] == ["CR-1"] and exactly["open_within_window"] == 0
    just_inside = a02_tools.clarification_window({"rounds": [_round("CR-1", "2026-01-08T12:00:01Z")], "now": NOW})
    assert just_inside["open_overdue"] == [] and just_inside["open_within_window"] == 1


def test_window_two_unanswered_rounds_force_a_provisional_spec():
    rounds = [_round("CR-1", "2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z"), _round("CR-2", "2026-01-05T00:00:00Z")]
    result = a02_tools.clarification_window({"rounds": rounds, "now": NOW})
    assert result["rounds_used"] == 2 and result["exhausted"] is True
    assert result["open_overdue"] == ["CR-2"] and result["must_go_provisional"] is True
    assert result["can_send_round"] is False


def test_window_exhausted_but_second_round_still_open_or_answered_is_not_provisional_yet():
    waiting = [_round("CR-1", "2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z"), _round("CR-2", "2026-01-10T00:00:00Z")]
    still_open = a02_tools.clarification_window({"rounds": waiting, "now": NOW})
    assert still_open["exhausted"] is True and still_open["must_go_provisional"] is False
    answered = [_round("CR-1", "2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z"), _round("CR-2", "2026-01-05T00:00:00Z", "2026-01-09T00:00:00Z")]
    done = a02_tools.clarification_window({"rounds": answered, "now": NOW})
    assert done["must_go_provisional"] is False and done["open_overdue"] == []


def test_window_refuses_a_third_round():
    rounds = [_round("CR-1", "2026-01-01T00:00:00Z"), _round("CR-2", "2026-01-02T00:00:00Z"), _round("CR-3", "2026-01-03T00:00:00Z")]
    result = a02_tools.clarification_window({"rounds": rounds, "now": NOW})
    assert result["rounds_used"] == 3 and result["over_limit"] is True
    assert result["can_send_round"] is False and result["must_go_provisional"] is True


def test_window_p50_latency_uses_the_median_of_answered_rounds():
    one = a02_tools.clarification_window({"rounds": [_round("a", "2026-01-01T00:00:00Z", "2026-01-01T10:00:00Z")], "now": NOW})
    assert one["p50_latency_h"] == 10.0
    two = a02_tools.clarification_window(
        {"rounds": [_round("a", "2026-01-01T00:00:00Z", "2026-01-01T10:00:00Z"), _round("b", "2026-01-02T00:00:00Z", "2026-01-02T20:00:00Z")], "now": NOW}
    )
    assert two["p50_latency_h"] == 15.0 and two["answered"] == 2


def test_window_honours_custom_round_limit_and_window_hours():
    rounds = [_round("CR-1", "2026-01-10T06:00:00Z")]
    result = a02_tools.clarification_window({"rounds": rounds, "now": NOW, "max_rounds": 1, "window_h": 6})
    assert result["exhausted"] is True and result["open_overdue"] == ["CR-1"] and result["must_go_provisional"] is True


def test_window_orders_by_sent_time_and_handles_offsets():
    rounds = [_round("late", "2026-01-05T00:00:00+00:00"), _round("early", "2026-01-04T00:00:00+02:00")]
    result = a02_tools.clarification_window({"rounds": rounds, "now": NOW})
    assert result["open_overdue"] == ["early", "late"]
    naive = a02_tools.clarification_window({"rounds": [_round("n", "2026-01-08T12:00:00")], "now": NOW})
    assert naive["open_overdue"] == ["n"]


def test_window_rejects_malformed_and_inconsistent_input():
    bad_now = a02_tools.clarification_window({"rounds": [], "now": "yesterday"})
    assert bad_now["state"] == "INVALID_INPUT" and bad_now["field"] == "now"
    cases = [
        [_round("a", "not-a-date")],
        [_round("a", "2026-02-01T00:00:00Z")],
        [_round("a", "2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")],
        [_round("a", "2026-01-02T00:00:00Z", "2026-02-01T00:00:00Z")],
        [_round("a", "2026-01-02T00:00:00Z"), _round("a", "2026-01-03T00:00:00Z")],
    ]
    for rounds in cases:
        result = a02_tools.clarification_window({"rounds": rounds, "now": NOW})
        assert result["state"] == "INVALID_INPUT" and result["field"] == "rounds", rounds


def test_window_schema_bounds_are_enforced():
    assert _call("req_clarification_window", {"rounds": [], "now": NOW, "window_h": 0})["state"] == "INVALID_INPUT"
    assert _call("req_clarification_window", {"rounds": [], "now": NOW, "max_rounds": 0})["state"] == "INVALID_INPUT"
    too_many = [_round(f"r{i}", "2026-01-01T00:00:00Z") for i in range(51)]
    assert _call("req_clarification_window", {"rounds": too_many, "now": NOW})["state"] == "INVALID_INPUT"


# --- req_conflict_matrix -------------------------------------------------------------------------


def _req(rid: str, op: str, value: str, priority: int | None = None, subject: str = "search", attribute: str = "latency_ms") -> dict:
    return {"id": rid, "subject": subject, "attribute": attribute, "op": op, "value": value, **({"priority": priority} if priority else {})}


def _kinds_of(result: dict) -> list[tuple[str, str, str]]:
    return [(c["left"], c["right"], c["kind"]) for c in result["conflicts"]]


def test_conflict_incompatible_numeric_bounds():
    result = a02_tools.conflict_matrix({"requirements": [_req("NFR-1", "lt", "300"), _req("NFR-2", "gt", "500")]})
    assert result["state"] == "SUCCESS" and result["conflict_count"] == 1
    assert _kinds_of(result) == [("NFR-1", "NFR-2", "bounds")]
    assert result["blocked_ids"] == ["NFR-1", "NFR-2"] and result["equal_rank"] == []


def test_conflict_touching_bounds_depend_on_strictness():
    strict = a02_tools.conflict_matrix({"requirements": [_req("a", "lt", "300"), _req("b", "ge", "300")]})
    assert _kinds_of(strict) == [("a", "b", "bounds")]
    inclusive = a02_tools.conflict_matrix({"requirements": [_req("a", "le", "300"), _req("b", "ge", "300")]})
    assert inclusive["conflicts"] == []


def test_conflict_same_value_and_same_direction_bounds_do_not_conflict():
    same = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "5"), _req("b", "eq", "5.0"), _req("c", "lt", "300"), _req("d", "lt", "300"), _req("e", "lt", "100")]})
    assert same["conflicts"] == [] and same["blocked_ids"] == []


def test_conflict_equality_and_opposite_polarity():
    differing = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "Red", attribute="theme"), _req("b", "eq", "blue", attribute="theme")]})
    assert _kinds_of(differing) == [("a", "b", "value")]
    negated = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "csv"), _req("b", "ne", "CSV")]})
    assert _kinds_of(negated) == [("a", "b", "polarity")]
    harmless = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "csv"), _req("b", "ne", "pdf"), _req("c", "ne", "xml")]})
    assert harmless["conflicts"] == []


def test_conflict_equality_against_numeric_bounds():
    result = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "600"), _req("b", "lt", "300"), _req("c", "ge", "100")]})
    assert _kinds_of(result) == [("a", "b", "bounds")]
    textual = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "fast"), _req("b", "lt", "300")]})
    assert textual["conflicts"] == []


def test_conflict_only_within_the_same_subject_and_attribute():
    apart = a02_tools.conflict_matrix(
        {"requirements": [_req("a", "lt", "300"), _req("b", "gt", "500", subject="export"), _req("c", "gt", "500", attribute="size")]}
    )
    assert apart["conflicts"] == []
    folded = a02_tools.conflict_matrix({"requirements": [_req("a", "lt", "300"), _req("b", "gt", "500", subject=" SEARCH ", attribute="Latency_MS")]})
    assert folded["conflict_count"] == 1


def test_conflict_priority_decides_blocked_and_equal_rank():
    ranked = a02_tools.conflict_matrix({"requirements": [_req("a", "lt", "300", 1), _req("b", "gt", "500", 2)]})
    assert ranked["blocked_ids"] == ["b"] and ranked["equal_rank"] == [] and ranked["needs_escalation"] is False
    tie = a02_tools.conflict_matrix({"requirements": [_req("a", "lt", "300", 2), _req("b", "gt", "500", 2)]})
    assert tie["blocked_ids"] == ["a", "b"] and tie["equal_rank"] == [{"left": "a", "right": "b"}]
    assert tie["needs_escalation"] is True
    unranked = a02_tools.conflict_matrix({"requirements": [_req("a", "lt", "300", 1), _req("b", "gt", "500")]})
    assert unranked["blocked_ids"] == ["a", "b"] and unranked["equal_rank"] == []


def test_conflict_blocked_ids_follow_input_order_and_accumulate():
    result = a02_tools.conflict_matrix(
        {"requirements": [_req("z", "lt", "300"), _req("m", "ne", "x", attribute="k"), _req("a", "gt", "500"), _req("b", "eq", "x", attribute="k"), _req("c", "ne", "x", attribute="k")]}
    )
    assert result["conflict_count"] == 3
    assert result["blocked_ids"] == ["z", "m", "a", "b", "c"]


def test_conflict_empty_set_and_invalid_input():
    assert a02_tools.conflict_matrix({"requirements": []})["conflict_count"] == 0
    duplicate = a02_tools.conflict_matrix({"requirements": [_req("a", "eq", "1"), _req("a", "eq", "2")]})
    assert duplicate["state"] == "INVALID_INPUT" and duplicate["field"] == "requirements"
    nonnumeric = a02_tools.conflict_matrix({"requirements": [_req("a", "lt", "quick")]})
    assert nonnumeric["state"] == "INVALID_INPUT" and "requirements[0]" in nonnumeric["message"]
    infinite = a02_tools.conflict_matrix({"requirements": [_req("a", "gt", "inf")]})
    assert infinite["state"] == "INVALID_INPUT"


def test_conflict_schema_rejects_unknown_operator_and_oversized_sets():
    assert _call("req_conflict_matrix", {"requirements": [_req("a", "approx", "1")]})["state"] == "INVALID_INPUT"
    many = [_req(f"r{i}", "eq", "1") for i in range(501)]
    assert _call("req_conflict_matrix", {"requirements": many})["state"] == "INVALID_INPUT"
    zero_rank = {**_req("a", "eq", "1"), "priority": 0}
    assert _call("req_conflict_matrix", {"requirements": [zero_rank]})["state"] == "INVALID_INPUT"


# --- review fix-ups (RV-C) -----------------------------------------------------------------------------------

CARD = "4111 1111 1111 1111"


@pytest.mark.parametrize(
    "text",
    ["qty 2 4111 1111 1111 1111", "SSN 123-45-6789 4111 1111 1111 1111", "order 12 4111-1111-1111-1111"],
)
def test_pii_scan_redacts_a_card_that_follows_other_digits(text):
    """RV-C P1: pii_scan({'text': t})['redacted_text'] printed these three cards in clear text and `found` had no card."""
    result = a02_tools.pii_scan({"text": text})
    assert "4111" not in result["redacted_text"].replace("[REDACTED:CARD]", "")
    assert _kinds(result)["card"] == 1
    assert "PCI-DSS" in result["regimes_hint"]


def test_pii_scan_card_in_a_digit_run_keeps_the_other_digits_visible():
    result = a02_tools.pii_scan({"text": "qty 2 4111 1111 1111 1111 now"})
    assert result["redacted_text"] == "qty 2 [REDACTED:CARD] now"
    assert a02_tools.pii_scan({"text": "SSN 123-45-6789 " + CARD})["redacted_text"] == "SSN [REDACTED:SSN] [REDACTED:CARD]"


def test_pii_scan_prefers_the_longest_valid_window_and_never_overlaps():
    # 4111111111111111 (16) followed by 8 gives a 17-digit window that is not Luhn valid; the 16-digit card wins
    result = a02_tools.pii_scan({"text": "4111 1111 1111 1111 8"})
    assert result["redacted_text"] == "[REDACTED:CARD] 8"
    # a Luhn-invalid run stays visible
    assert a02_tools.pii_scan({"text": "4111 1111 1111 1112"})["has_pii"] is False


def test_pii_scan_luhn_matches_the_reference_for_every_window_in_a_random_run():
    """The O(1) prefix-sum Luhn must equal the plain definition for each 13-19 digit window."""
    import random

    def reference(digits: str) -> bool:
        total = 0
        for index, ch in enumerate(reversed(digits)):
            digit = int(ch)
            if index % 2:
                digit = digit * 2 - 9 if digit > 4 else digit * 2
            total += digit
        return total % 10 == 0

    rng = random.Random(7)
    for _ in range(200):
        digits = "".join(rng.choice("0123456789") for _ in range(rng.randint(13, 30)))
        text = " ".join(digits[i : i + 4] for i in range(0, len(digits), 4))
        spans = a02_tools._card_spans(text, 0, len(text))
        groups = text.split(" ")
        expected = set()
        for first in range(len(groups)):
            for last in range(first, len(groups)):
                window = "".join(groups[first : last + 1])
                if 13 <= len(window) <= 19 and reference(window):
                    start = len(" ".join(groups[:first])) + (1 if first else 0)
                    expected.add((start, start + len(" ".join(groups[first : last + 1]))))
        assert set(spans) == expected


def test_pii_scan_many_matches_finish_well_inside_the_tool_timeout():
    """RV-C P2: 100 KB of 'a@b.cc ' took 5.27 s (quadratic overlap scan) and call_tool returned TIMEOUT."""
    import time

    for chunk in ("a@b.cc ", "10.0.0.1 ", "1 ", "4111 1111 1111 1111 "):
        text = chunk * (100_000 // len(chunk))
        started = time.monotonic()
        result = a02_tools.pii_scan({"text": text})
        assert time.monotonic() - started < 2.0, chunk
        assert result["state"] == "SUCCESS"


def test_pii_scan_many_cards_through_call_tool_does_not_time_out():
    text = (CARD + " ") * 4000
    out = _call("req_pii_scan", {"text": text})
    assert out["state"] == "SUCCESS" and _kinds(out)["card"] == 4000


@pytest.mark.parametrize("window_h", [float("nan"), float("inf"), -1])
def test_window_non_finite_window_h_is_invalid_input(window_h):
    """RV-C P2: window_h NaN surfaced as DEPENDENCY_UNAVAILABLE 'cannot convert float NaN to integer'."""
    out = _call("req_clarification_window", {"rounds": [], "now": "2026-01-10T12:00:00Z", "window_h": window_h})
    assert out["state"] == "INVALID_INPUT" and out["field"] == "window_h"
