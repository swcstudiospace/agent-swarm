"""Behaviour tests for the v2.1 A02 lane tools that had only generated contract probes.

Tools: req_gwt_check, req_invest_score, req_ambiguity_scan, req_story_duplicates, req_wsjf_rank,
req_criteria_testability, req_traceability_coverage, req_change_impact, req_nfr_coverage.
Every test asserts output values for specific inputs, through the registry (`call_tool`) so that schema and
authorisation are part of the path. tests/conftest.py evicts swarm* modules between tests, so the registry is
resolved through the current module graph on every call.
"""
from __future__ import annotations

import importlib

import pytest

ALL_TOOLS = (
    "req_gwt_check", "req_invest_score", "req_ambiguity_scan", "req_story_duplicates", "req_wsjf_rank",
    "req_criteria_testability", "req_traceability_coverage", "req_change_impact", "req_nfr_coverage",
)


def call(name: str, payload: dict, caller: str = "A02") -> dict:
    return importlib.import_module("swarm.tool_registry").call_tool(name, caller, payload)


def invalid(name: str, payload: dict, field: str) -> dict:
    result = call(name, payload)
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result
    return result


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_only_a02_may_call_each_tool(name):
    for caller in ("A01", "A08"):
        result = call(name, {"text": "x"}, caller)
        assert result["state"] == "UNAUTHORIZED" and result["caller"] == caller and result["target"] == name


# --- req_gwt_check -------------------------------------------------------------------------------


def test_gwt_complete_criterion_and_multi_block_counts():
    text = (
        "AC-1 Given a user When they ping Then they see pong\n"
        "AC-2 Given a cart When it is empty Then checkout is disabled\n"
        "AC-3 Given a cart When it has items\n"
    )
    assert call("req_gwt_check", {"text": text}) == {"state": "SUCCESS", "criteria": 3, "missing_gwt": 1}


@pytest.mark.parametrize(
    ("body", "missing"),
    [
        ("AC-1 Given a user Then they see pong", 1),  # no When
        ("AC-1 When they ping Then they see pong", 1),  # no Given
        ("AC-1 Given a user When they ping", 1),  # no Then
        ("AC-1 the system is fast", 1),  # none of the three
        ("AC-1 GIVEN a user WHEN they ping THEN pong", 0),  # case-insensitive
    ],
)
def test_gwt_each_missing_keyword_is_detected(body, missing):
    assert call("req_gwt_check", {"text": body}) == {"state": "SUCCESS", "criteria": 1, "missing_gwt": missing}


def test_gwt_text_without_ac_ids_reports_zero_criteria():
    result = call("req_gwt_check", {"text": "Given a user When they ping Then pong, but no criterion id"})
    assert result == {"state": "SUCCESS", "criteria": 0, "missing_gwt": 0}


def test_gwt_text_before_the_first_ac_id_is_not_a_block_and_ids_with_suffixes_split_blocks():
    text = "Given When Then preamble.\nAC-1.2 Given a When b Then c\nAC-2b_x only prose here\n"
    assert call("req_gwt_check", {"text": text}) == {"state": "SUCCESS", "criteria": 2, "missing_gwt": 1}


def test_gwt_empty_text_is_invalid_and_names_text():
    invalid("req_gwt_check", {"text": ""}, "text")
    invalid("req_gwt_check", {}, "text")
    invalid("req_gwt_check", {"text": 5}, "text")


def test_gwt_unknown_field_is_rejected():
    invalid("req_gwt_check", {"text": "AC-1", "mode": "strict"}, "mode")


def test_gwt_keywords_must_be_whole_words():
    # Spec (prompts/A02): count blocks that "lack given, when or then". 'authentication' contains 'then' only as a
    # substring; 'whenever' contains 'when'. Repro: criteria 1, expected missing_gwt 1, actual 0.
    result = call("req_gwt_check", {"text": "AC-1 Given a user whenever authentication runs"})
    assert result["missing_gwt"] == 1


# --- req_invest_score ----------------------------------------------------------------------------

STORY = "As a shopper I want to save items to a wishlist so that I can buy them later"
CRITERION = "Given a signed-in shopper When they save an item Then it appears in the wishlist"


def test_invest_full_score_matches_the_spec_example():
    result = call("req_invest_score", {"story": STORY, "estimate_points": 3, "criteria": [CRITERION]})
    assert result == {"state": "SUCCESS", "score": 6, "failed": []}


def test_invest_everything_fails_and_failed_letters_are_in_invest_order():
    result = call("req_invest_score", {"story": "Use the redis queue", "dependencies": ["US-9"]})
    assert result == {"state": "SUCCESS", "score": 0, "failed": ["I", "N", "V", "E", "S", "T"]}


def test_invest_dependencies_fail_only_independence():
    result = call("req_invest_score", {"story": STORY, "dependencies": ["US-1", "US-2"], "estimate_points": 2, "criteria": ["c"]})
    assert result["failed"] == ["I"] and result["score"] == 5
    empty = call("req_invest_score", {"story": STORY, "dependencies": [], "estimate_points": 2, "criteria": ["c"]})
    assert empty["failed"] == []


@pytest.mark.parametrize(("points", "failed", "score"), [(1, [], 6), (8, [], 6), (9, ["S"], 5), (100, ["S"], 5)])
def test_invest_small_boundary_is_eight_points_inclusive(points, failed, score):
    result = call("req_invest_score", {"story": STORY, "estimate_points": points, "criteria": ["c"]})
    assert result["failed"] == failed and result["score"] == score


def test_invest_missing_estimate_fails_estimable_and_small():
    result = call("req_invest_score", {"story": STORY, "criteria": ["c"]})
    assert result["failed"] == ["E", "S"] and result["score"] == 4


@pytest.mark.parametrize(
    "story",
    [
        "As an admin I want an API to export logs so that I can audit",
        "As a user I want results via websocket so that I see updates",
        "As a dev I want to store sessions in Redis so that logins persist",
        "As a user I want to save using a form so that I keep my work",
    ],
)
def test_invest_solution_prescribing_words_fail_negotiable_only(story):
    result = call("req_invest_score", {"story": story, "estimate_points": 2, "criteria": ["c"]})
    assert result["failed"] == ["N"]


def test_invest_negotiable_check_uses_whole_words_not_substrings():
    # 'rapid' contains 'api', 'tablet' contains 'table', 'discussion' contains 'cuss' etc.: none is a how-word.
    story = "As a user I want a rapid tablet sync so that I can work offline"
    result = call("req_invest_score", {"story": story, "estimate_points": 2, "criteria": ["c"]})
    assert result["failed"] == []


def test_invest_value_clause_accepts_both_phrasings_case_insensitively():
    base = {"estimate_points": 2, "criteria": ["c"]}
    assert call("req_invest_score", {"story": "I want X in order to ship", **base})["failed"] == []
    assert call("req_invest_score", {"story": "I want X SO THAT I ship", **base})["failed"] == []
    assert call("req_invest_score", {"story": "I want X so I ship", **base})["failed"] == ["V"]
    # 'so that' must be a whole phrase, not the prefix of another word
    assert call("req_invest_score", {"story": "I want X so thatcher ships", **base})["failed"] == ["V"]


def test_invest_testable_needs_at_least_one_criterion():
    assert call("req_invest_score", {"story": STORY, "estimate_points": 2, "criteria": []})["failed"] == ["T"]
    assert call("req_invest_score", {"story": STORY, "estimate_points": 2, "criteria": ["c"]})["failed"] == []


def test_invest_invalid_inputs_name_their_fields():
    invalid("req_invest_score", {"story": ""}, "story")
    invalid("req_invest_score", {"story": "   "}, "story")
    invalid("req_invest_score", {"story": "!!! ???"}, "story")
    invalid("req_invest_score", {"story": "x" * 2001}, "story")
    invalid("req_invest_score", {"story": STORY, "estimate_points": 0}, "estimate_points")
    invalid("req_invest_score", {"story": STORY, "estimate_points": 101}, "estimate_points")
    invalid("req_invest_score", {"story": STORY, "estimate_points": 2.5}, "estimate_points")
    invalid("req_invest_score", {"story": STORY, "estimate_points": True}, "estimate_points")
    invalid("req_invest_score", {"story": STORY, "dependencies": [""]}, "dependencies")
    invalid("req_invest_score", {"story": STORY, "dependencies": ["d"] * 51}, "dependencies")
    invalid("req_invest_score", {"story": STORY, "criteria": [""]}, "criteria")
    invalid("req_invest_score", {}, "story")


# --- req_ambiguity_scan --------------------------------------------------------------------------


def test_ambiguity_spec_example_values():
    text = "The export should be fast. It must produce a CSV file within 30 seconds. Some rows may be skipped."
    assert call("req_ambiguity_scan", {"text": text}) == {
        "state": "SUCCESS", "score": 0.6667, "sentence_count": 3, "flagged_sentences": 2,
        "terms": ["fast", "may", "should", "some"], "needs_clarification": True,
    }


def test_ambiguity_clean_text_scores_zero():
    result = call("req_ambiguity_scan", {"text": "The export produces a CSV file within 30 seconds. It must log every row."})
    assert result == {
        "state": "SUCCESS", "score": 0.0, "sentence_count": 2, "flagged_sentences": 0, "terms": [],
        "needs_clarification": False,
    }


def test_ambiguity_threshold_is_strictly_above_0_4():
    at = "It should work. Some rows skip. Import runs. Export runs. Sync runs."
    over = "It should work. Some rows skip. Sync may fail. Export runs. Import runs."
    low = call("req_ambiguity_scan", {"text": at})
    high = call("req_ambiguity_scan", {"text": over})
    assert (low["score"], low["sentence_count"], low["flagged_sentences"], low["needs_clarification"]) == (0.4, 5, 2, False)
    assert (high["score"], high["flagged_sentences"], high["needs_clarification"]) == (0.6, 3, True)


def test_ambiguity_counts_sentences_not_terms_and_dedups_sorted_terms():
    result = call("req_ambiguity_scan", {"text": "Some many several rows. Some more. Clean."})
    assert result["flagged_sentences"] == 2 and result["sentence_count"] == 3
    assert result["terms"] == ["many", "several", "some"]


def test_ambiguity_matching_is_case_insensitive_and_whole_word():
    flagged = call("req_ambiguity_scan", {"text": "The page SHOULD load Quickly."})
    assert flagged["terms"] == ["quickly", "should"] and flagged["score"] == 1.0
    lookalikes = call("req_ambiguity_scan", {"text": "The mayor visited somewhere faster than most-likely."})
    assert lookalikes["terms"] == ["likely", "most"]  # hyphen splits the words; mayor/somewhere/faster are not flagged


def test_ambiguity_splits_on_bang_question_and_ignores_empty_fragments():
    result = call("req_ambiguity_scan", {"text": "Is it fast? Yes!!! Really... Done."})
    assert result["sentence_count"] == 4 and result["flagged_sentences"] == 1 and result["terms"] == ["fast"]


def test_ambiguity_single_sentence_without_terminator():
    result = call("req_ambiguity_scan", {"text": "login should be easy"})
    assert result["sentence_count"] == 1 and result["score"] == 1.0 and result["terms"] == ["easy", "should"]


def test_ambiguity_invalid_inputs_name_text():
    invalid("req_ambiguity_scan", {"text": ""}, "text")
    invalid("req_ambiguity_scan", {"text": "... ???"}, "text")
    invalid("req_ambiguity_scan", {"text": "a" * 20001}, "text")
    invalid("req_ambiguity_scan", {}, "text")


def test_ambiguity_accepts_text_at_the_size_limit():
    result = call("req_ambiguity_scan", {"text": "a" * 20000})
    assert result["state"] == "SUCCESS" and result["sentence_count"] == 1 and result["score"] == 0.0


# --- req_story_duplicates ------------------------------------------------------------------------


def _stories(*pairs):
    return [{"id": story_id, "text": text} for story_id, text in pairs]


def test_duplicates_spec_example():
    stories = _stories(
        ("US-1", "As a shopper I want to save items to a wishlist"),
        ("US-2", "As a shopper I want to save items to my wishlist"),
        ("US-3", "As an admin I want to export audit logs"),
    )
    assert call("req_story_duplicates", {"stories": stories}) == {
        "state": "SUCCESS", "pair_count": 1, "pairs": [{"left": "US-1", "right": "US-2", "similarity": 1.0}],
    }


def test_duplicates_threshold_boundary_is_inclusive():
    # tokens {export,audit,log,csv} vs {export,audit,log,pdf}: 3 shared of 5 = 0.6 exactly
    stories = _stories(("A", "export audit logs csv"), ("B", "export audit logs pdf"))
    at = call("req_story_duplicates", {"stories": stories})  # default threshold 0.6
    assert at["pairs"] == [{"left": "A", "right": "B", "similarity": 0.6}]
    assert call("req_story_duplicates", {"stories": stories, "threshold": 0.61})["pair_count"] == 0
    assert call("req_story_duplicates", {"stories": stories, "threshold": 0.6})["pair_count"] == 1


def test_duplicates_pairs_sorted_by_similarity_then_ids():
    stories = _stories(
        ("Z", "alpha beta gamma delta"),
        ("B", "alpha beta gamma delta"),
        ("C", "alpha beta gamma epsilon"),
        ("A", "unrelated words only"),
    )
    pairs = call("req_story_duplicates", {"stories": stories, "threshold": 0.5})["pairs"]
    assert pairs == [
        {"left": "Z", "right": "B", "similarity": 1.0},
        {"left": "B", "right": "C", "similarity": 0.6},
        {"left": "Z", "right": "C", "similarity": 0.6},
    ]


def test_duplicates_normalisation_strips_stop_words_case_and_trailing_s():
    stories = _stories(("A", "The Items, LOGS and Reports"), ("B", "item log report"))
    assert call("req_story_duplicates", {"stories": stories})["pairs"] == [{"left": "A", "right": "B", "similarity": 1.0}]


def test_duplicates_trailing_s_rule_keeps_ss_endings_and_short_words():
    # 'class' keeps its s (ss ending); 'bus' (3 letters) is not stemmed; 'classes' -> 'classe' != 'class'
    same = call("req_story_duplicates", {"stories": _stories(("A", "class bus"), ("B", "class bus"))})
    assert same["pairs"][0]["similarity"] == 1.0
    differ = call("req_story_duplicates", {"stories": _stories(("A", "class"), ("B", "classes")), "threshold": 0.0})
    assert differ["pairs"] == [{"left": "A", "right": "B", "similarity": 0.0}]


def test_duplicates_threshold_one_requires_identical_token_sets_and_zero_reports_every_pair():
    stories = _stories(("A", "red blue"), ("B", "red blue"), ("C", "red green"))
    assert [(p["left"], p["right"]) for p in call("req_story_duplicates", {"stories": stories, "threshold": 1})["pairs"]] == [("A", "B")]
    everything = call("req_story_duplicates", {"stories": stories, "threshold": 0})
    assert everything["pair_count"] == 3


def test_duplicates_similarity_is_rounded_to_four_places():
    stories = _stories(("A", "one two three"), ("B", "one four five six"))  # 1 shared / 6 union
    pair = call("req_story_duplicates", {"stories": stories, "threshold": 0.1})["pairs"][0]
    assert pair["similarity"] == 0.1667


def test_duplicates_duplicate_ids_are_invalid():
    result = invalid("req_story_duplicates", {"stories": _stories(("A", "x y"), ("A", "x y"))}, "stories")
    assert "unique" in result["message"]


def test_duplicates_schema_bounds_name_their_fields():
    invalid("req_story_duplicates", {"stories": _stories(("A", "x y"))}, "stories")  # fewer than 2
    invalid("req_story_duplicates", {"stories": _stories(("A", "x"), ("B", "y")), "threshold": 1.5}, "threshold")
    invalid("req_story_duplicates", {"stories": _stories(("A", "x"), ("B", ""))}, "stories")
    invalid("req_story_duplicates", {"stories": [{"id": f"S{i}", "text": "t"} for i in range(201)]}, "stories")
    invalid("req_story_duplicates", {}, "stories")


def test_duplicates_accepts_the_maximum_200_stories():
    stories = [{"id": f"S{i}", "text": f"word{i}"} for i in range(200)]
    result = call("req_story_duplicates", {"stories": stories})
    assert result == {"state": "SUCCESS", "pair_count": 0, "pairs": []}


def test_duplicates_identical_stories_are_reported_even_when_all_stop_words():
    # Repro: two stories "As a user I want it" / "As a user I want it" (every word is a stop word).
    # Expected one pair with similarity 1.0; actual pair_count 0 (swarm/tools/a02_tools.py story_duplicates `if not union: continue`).
    stories = _stories(("A", "as a i want it"), ("B", "as a i want it"))
    assert call("req_story_duplicates", {"stories": stories})["pair_count"] == 1


# --- req_wsjf_rank -------------------------------------------------------------------------------


def _item(item_id, value, crit, risk, size):
    return {"id": item_id, "business_value": value, "time_criticality": crit, "risk_reduction": risk, "job_size": size}


def test_wsjf_spec_example_and_method_for_small_backlog():
    result = call("req_wsjf_rank", {"items": [_item("US-142", 8, 5, 3, 4), _item("US-143", 3, 2, 1, 2)]})
    assert result == {
        "state": "SUCCESS", "method": "MOSCOW", "top_id": "US-142",
        "ranked": [{"id": "US-142", "wsjf": 4.0, "rank": 1}, {"id": "US-143", "wsjf": 3.0, "rank": 2}],
    }


def test_wsjf_orders_descending_regardless_of_input_order():
    items = [_item("low", 1, 1, 1, 3), _item("high", 9, 9, 9, 3), _item("mid", 3, 3, 3, 3)]
    ranked = call("req_wsjf_rank", {"items": items})["ranked"]
    assert [(r["id"], r["wsjf"], r["rank"]) for r in ranked] == [("high", 9.0, 1), ("mid", 3.0, 2), ("low", 1.0, 3)]


def test_wsjf_ties_sort_by_id_ascending_with_distinct_ranks():
    items = [_item("b", 2, 2, 2, 2), _item("c", 6, 0, 0, 2), _item("a", 1, 1, 1, 1)]
    result = call("req_wsjf_rank", {"items": items})
    assert [r["id"] for r in result["ranked"]] == ["a", "b", "c"] and [r["rank"] for r in result["ranked"]] == [1, 2, 3]
    assert result["top_id"] == "a"


def test_wsjf_method_switches_above_twenty_items():
    twenty = [_item(f"S{i:02d}", 1, 1, 1, 1) for i in range(20)]
    assert call("req_wsjf_rank", {"items": twenty})["method"] == "MOSCOW"
    assert call("req_wsjf_rank", {"items": [*twenty, _item("S20", 1, 1, 1, 1)]})["method"] == "WSJF"


def test_wsjf_rounds_to_four_places_and_accepts_fractional_job_size():
    result = call("req_wsjf_rank", {"items": [_item("a", 1, 1, 1, 7), _item("b", 1, 0, 0, 0.5)]})
    assert result["ranked"] == [{"id": "b", "wsjf": 2.0, "rank": 1}, {"id": "a", "wsjf": 0.4286, "rank": 2}]


def test_wsjf_zero_scores_are_allowed_and_single_item_ranks_first():
    result = call("req_wsjf_rank", {"items": [_item("only", 0, 0, 0, 5)]})
    assert result["top_id"] == "only" and result["ranked"] == [{"id": "only", "wsjf": 0.0, "rank": 1}]


def test_wsjf_duplicate_ids_are_invalid():
    result = invalid("req_wsjf_rank", {"items": [_item("a", 1, 1, 1, 1), _item("a", 2, 2, 2, 2)]}, "items")
    assert "unique" in result["message"]


def test_wsjf_schema_rejects_zero_job_size_negative_scores_and_missing_fields():
    assert "job_size" in invalid("req_wsjf_rank", {"items": [_item("a", 1, 1, 1, 0)]}, "items")["message"]
    assert "business_value" in invalid("req_wsjf_rank", {"items": [_item("a", -1, 1, 1, 1)]}, "items")["message"]
    missing = {"id": "a", "business_value": 1, "time_criticality": 1, "job_size": 1}
    assert "risk_reduction" in invalid("req_wsjf_rank", {"items": [missing]}, "items")["message"]
    invalid("req_wsjf_rank", {"items": []}, "items")
    invalid("req_wsjf_rank", {"items": [_item(f"S{i}", 1, 1, 1, 1) for i in range(201)]}, "items")
    invalid("req_wsjf_rank", {}, "items")


# --- req_criteria_testability --------------------------------------------------------------------


def _crit(crit_id, text, automated):
    return {"id": crit_id, "text": text, "automated": automated}


def test_testability_spec_example():
    criteria = [
        _crit("AC-1", "Given a user with valid session When POST /orders exceeds budget Then 422 with error.code ORDER_LIMIT", True),
        _crit("AC-2", "The checkout page feels quick to a reviewer", False),
    ]
    assert call("req_criteria_testability", {"criteria": criteria}) == {
        "state": "SUCCESS", "total": 2, "automated": 1, "manual_only": 1, "manual_ratio": 0.5,
        "unmeasurable_ids": ["AC-2"], "needs_clarification": True,
    }


def test_testability_manual_threshold_is_strictly_above_30_percent():
    def build(manual):
        return [_crit(f"AC-{i}", "p95 under 200 ms", i >= manual) for i in range(10)]

    at = call("req_criteria_testability", {"criteria": build(3)})
    over = call("req_criteria_testability", {"criteria": build(4)})
    assert (at["manual_ratio"], at["needs_clarification"]) == (0.3, False)
    assert (over["manual_ratio"], over["needs_clarification"]) == (0.4, True)


def test_testability_all_automated_and_all_manual_extremes():
    auto = call("req_criteria_testability", {"criteria": [_crit("a", "no digits here", True)]})
    assert auto["manual_only"] == 0 and auto["manual_ratio"] == 0.0 and auto["unmeasurable_ids"] == []
    assert auto["needs_clarification"] is False
    manual = call("req_criteria_testability", {"criteria": [_crit("a", "looks nice", False), _crit("b", "feels ok", False)]})
    assert manual["manual_ratio"] == 1.0 and manual["unmeasurable_ids"] == ["a", "b"] and manual["needs_clarification"] is True


def test_testability_measurable_means_a_digit_or_the_word_then_for_manual_criteria_only():
    criteria = [
        _crit("digit", "page loads within 2 seconds", False),
        _crit("then", "Given a reviewer When they open it THEN they approve", False),
        _crit("embedded-digit", "supports http2", False),
        _crit("substring", "Thenceforth the page is pretty", False),
        _crit("vague", "the page is pretty", False),
        _crit("auto-vague", "the page is pretty", True),
    ]
    result = call("req_criteria_testability", {"criteria": criteria})
    assert result["unmeasurable_ids"] == ["substring", "vague"]
    assert (result["total"], result["automated"], result["manual_only"]) == (6, 1, 5)
    assert result["manual_ratio"] == 0.8333


def test_testability_preserves_input_order_of_unmeasurable_ids():
    criteria = [_crit("z", "meh", False), _crit("a", "meh", False), _crit("m", "meh", False)]
    assert call("req_criteria_testability", {"criteria": criteria})["unmeasurable_ids"] == ["z", "a", "m"]


def test_testability_duplicate_ids_are_invalid():
    result = invalid("req_criteria_testability", {"criteria": [_crit("a", "t", True), _crit("a", "u", False)]}, "criteria")
    assert "unique" in result["message"]


def test_testability_schema_rejections_name_the_field():
    invalid("req_criteria_testability", {"criteria": []}, "criteria")
    invalid("req_criteria_testability", {"criteria": [{"id": "a", "text": "t"}]}, "criteria")
    invalid("req_criteria_testability", {"criteria": [_crit("a", "t", "yes")]}, "criteria")
    invalid("req_criteria_testability", {"criteria": [_crit("a", "", True)]}, "criteria")
    invalid("req_criteria_testability", {"criteria": [_crit(f"c{i}", "t", True) for i in range(201)]}, "criteria")
    invalid("req_criteria_testability", {}, "criteria")


# --- req_traceability_coverage -------------------------------------------------------------------


def _link(req, artifact):
    return {"requirement_id": req, "artifact_id": artifact}


def test_traceability_spec_example():
    payload = {
        "requirement_ids": ["US-142", "US-143", "NFR-P1"],
        "links": [_link("US-142", "AC-1"), _link("US-143", "T-4")],
    }
    assert call("req_traceability_coverage", payload) == {
        "state": "SUCCESS", "total": 3, "covered": 2, "coverage": 0.6667, "uncovered": ["NFR-P1"],
        "unknown_requirement_ids": [], "linked_artifacts": 2, "meets_target": False,
    }


def test_traceability_full_coverage_meets_default_target():
    payload = {"requirement_ids": ["A", "B"], "links": [_link("A", "t1"), _link("B", "t2")]}
    result = call("req_traceability_coverage", payload)
    assert result["coverage"] == 1.0 and result["uncovered"] == [] and result["meets_target"] is True


def test_traceability_empty_links_cover_nothing():
    result = call("req_traceability_coverage", {"requirement_ids": ["B", "A", "C"], "links": []})
    assert result["covered"] == 0 and result["coverage"] == 0.0 and result["linked_artifacts"] == 0
    assert result["uncovered"] == ["B", "A", "C"]  # input order, not sorted
    assert result["meets_target"] is False


def test_traceability_target_boundary_is_inclusive_and_zero_target_always_met():
    payload = {"requirement_ids": ["A", "B"], "links": [_link("A", "t")]}
    assert call("req_traceability_coverage", {**payload, "target": 0.5})["meets_target"] is True
    assert call("req_traceability_coverage", {**payload, "target": 0.51})["meets_target"] is False
    assert call("req_traceability_coverage", {"requirement_ids": ["A"], "links": [], "target": 0})["meets_target"] is True


def test_traceability_unknown_ids_are_sorted_deduplicated_and_do_not_count_as_coverage():
    payload = {
        "requirement_ids": ["A", "B"],
        "links": [_link("Z", "t1"), _link("M", "t2"), _link("Z", "t3"), _link("A", "t4")],
    }
    result = call("req_traceability_coverage", payload)
    assert result["unknown_requirement_ids"] == ["M", "Z"]
    assert result["covered"] == 1 and result["uncovered"] == ["B"]
    assert result["linked_artifacts"] == 1  # artifacts of unknown requirements are not counted


def test_traceability_linked_artifacts_are_distinct_ids():
    payload = {
        "requirement_ids": ["A", "B", "C"],
        "links": [_link("A", "t1"), _link("A", "t1"), _link("A", "t2"), _link("B", "t1")],
    }
    result = call("req_traceability_coverage", payload)
    assert result["covered"] == 2 and result["linked_artifacts"] == 2 and result["coverage"] == 0.6667


def test_traceability_rounds_coverage_to_four_places():
    payload = {"requirement_ids": ["A", "B", "C"], "links": [_link("A", "t")]}
    assert call("req_traceability_coverage", payload)["coverage"] == 0.3333


def test_traceability_duplicate_requirement_ids_are_invalid():
    result = invalid("req_traceability_coverage", {"requirement_ids": ["A", "A"], "links": []}, "requirement_ids")
    assert "unique" in result["message"]


def test_traceability_schema_rejections_name_the_field():
    invalid("req_traceability_coverage", {"requirement_ids": [], "links": []}, "requirement_ids")
    invalid("req_traceability_coverage", {"requirement_ids": ["A"]}, "links")
    invalid("req_traceability_coverage", {"requirement_ids": ["A"], "links": [{"requirement_id": "A"}]}, "links")
    invalid("req_traceability_coverage", {"requirement_ids": ["A"], "links": [], "target": 1.1}, "target")
    invalid("req_traceability_coverage", {"requirement_ids": ["A"], "links": [], "target": -0.1}, "target")
    invalid("req_traceability_coverage", {"requirement_ids": [f"R{i}" for i in range(501)], "links": []}, "requirement_ids")


# --- req_change_impact ---------------------------------------------------------------------------

COMMITTED = ["US-142", "US-143", "US-144", "US-145", "US-146"]


def _task(req, task):
    return {"requirement_id": req, "task_id": task}


def test_change_impact_spec_example_sits_exactly_on_the_threshold():
    payload = {
        "affected_ids": ["US-142", "NFR-P1"],
        "committed_ids": COMMITTED,
        "task_links": [_task("US-142", "T-1"), _task("US-142", "T-2"), _task("NFR-P1", "T-3"), _task("US-143", "T-4")],
    }
    assert call("req_change_impact", payload) == {
        "state": "SUCCESS", "tasks_at_risk": 3, "affected_tasks": ["T-1", "T-2", "T-3"], "affected_committed": 1,
        "scope_change_ratio": 0.2, "needs_human_approval": False,
    }


def test_change_impact_above_twenty_percent_needs_human_approval():
    payload = {"affected_ids": ["US-142", "US-143"], "committed_ids": COMMITTED, "task_links": []}
    result = call("req_change_impact", payload)
    assert result["affected_committed"] == 2 and result["scope_change_ratio"] == 0.4
    assert result["needs_human_approval"] is True


def test_change_impact_uncommitted_requirements_risk_tasks_but_not_scope():
    payload = {"affected_ids": ["NEW-1"], "committed_ids": COMMITTED, "task_links": [_task("NEW-1", "T-9")]}
    result = call("req_change_impact", payload)
    assert result["tasks_at_risk"] == 1 and result["affected_tasks"] == ["T-9"]
    assert result["affected_committed"] == 0 and result["scope_change_ratio"] == 0.0
    assert result["needs_human_approval"] is False


def test_change_impact_tasks_are_distinct_and_sorted_lexicographically():
    links = [_task("A", "T-2"), _task("A", "T-10"), _task("B", "T-2"), _task("C", "T-1")]
    result = call("req_change_impact", {"affected_ids": ["A", "B"], "committed_ids": ["A", "B", "C"], "task_links": links})
    assert result["affected_tasks"] == ["T-10", "T-2"] and result["tasks_at_risk"] == 2  # C is not affected


def test_change_impact_duplicate_ids_collapse_before_the_ratio():
    payload = {"affected_ids": ["a", "a"], "committed_ids": ["a", "a", "b"], "task_links": []}
    result = call("req_change_impact", payload)
    assert result["affected_committed"] == 1 and result["scope_change_ratio"] == 0.5


def test_change_impact_everything_affected_is_ratio_one_and_rounds_to_four_places():
    all_hit = call("req_change_impact", {"affected_ids": ["a", "b"], "committed_ids": ["a", "b"], "task_links": []})
    assert all_hit["scope_change_ratio"] == 1.0 and all_hit["tasks_at_risk"] == 0
    third = call("req_change_impact", {"affected_ids": ["a"], "committed_ids": ["a", "b", "c"], "task_links": []})
    assert third["scope_change_ratio"] == 0.3333 and third["needs_human_approval"] is True


def test_change_impact_schema_rejections_name_the_field():
    invalid("req_change_impact", {"affected_ids": [], "committed_ids": ["a"], "task_links": []}, "affected_ids")
    invalid("req_change_impact", {"affected_ids": ["a"], "committed_ids": [], "task_links": []}, "committed_ids")
    invalid("req_change_impact", {"affected_ids": ["a"], "committed_ids": ["a"]}, "task_links")
    invalid("req_change_impact", {"affected_ids": ["a"], "committed_ids": ["a"], "task_links": [{"requirement_id": "a"}]}, "task_links")
    invalid("req_change_impact", {"affected_ids": ["a" * 65], "committed_ids": ["a"], "task_links": []}, "affected_ids")


# --- req_nfr_coverage ----------------------------------------------------------------------------


def _nfr(nfr_id, kind):
    return {"id": nfr_id, "kind": kind}


def test_nfr_spec_example_with_default_required_kinds():
    nfrs = [_nfr("NFR-P1", "perf"), _nfr("NFR-S1", "security"), _nfr("NFR-A1", "a11y")]
    assert call("req_nfr_coverage", {"nfrs": nfrs}) == {
        "state": "SUCCESS", "nfr_count": 3, "present_kinds": ["a11y", "perf", "security"],
        "missing_kinds": ["i18n", "compliance"], "coverage": 0.6, "complete": False,
    }


def test_nfr_complete_when_every_default_kind_is_present():
    nfrs = [_nfr(f"N{i}", kind) for i, kind in enumerate(("compliance", "i18n", "a11y", "security", "perf"))]
    result = call("req_nfr_coverage", {"nfrs": nfrs})
    assert result["missing_kinds"] == [] and result["coverage"] == 1.0 and result["complete"] is True
    assert result["present_kinds"] == ["a11y", "compliance", "i18n", "perf", "security"]


def test_nfr_empty_list_misses_every_required_kind():
    result = call("req_nfr_coverage", {"nfrs": []})
    assert result == {
        "state": "SUCCESS", "nfr_count": 0, "present_kinds": [],
        "missing_kinds": ["perf", "security", "a11y", "i18n", "compliance"], "coverage": 0.0, "complete": False,
    }


def test_nfr_required_kinds_subset_and_missing_follow_required_order():
    nfrs = [_nfr("N1", "perf")]
    result = call("req_nfr_coverage", {"nfrs": nfrs, "required_kinds": ["compliance", "perf", "a11y"]})
    assert result["missing_kinds"] == ["compliance", "a11y"] and result["coverage"] == 0.3333
    assert result["complete"] is False


def test_nfr_kinds_outside_the_required_set_do_not_count():
    result = call("req_nfr_coverage", {"nfrs": [_nfr("N1", "perf"), _nfr("N2", "perf")], "required_kinds": ["security"]})
    assert result["nfr_count"] == 2 and result["present_kinds"] == ["perf"]
    assert result["missing_kinds"] == ["security"] and result["coverage"] == 0.0


def test_nfr_duplicate_required_kinds_collapse():
    result = call("req_nfr_coverage", {"nfrs": [_nfr("N1", "perf")], "required_kinds": ["perf", "perf", "security"]})
    assert result["missing_kinds"] == ["security"] and result["coverage"] == 0.5


def test_nfr_repeated_kind_counts_every_nfr_but_one_present_kind():
    result = call("req_nfr_coverage", {"nfrs": [_nfr("N1", "i18n"), _nfr("N2", "i18n")], "required_kinds": ["i18n"]})
    assert result["nfr_count"] == 2 and result["present_kinds"] == ["i18n"] and result["complete"] is True


def test_nfr_schema_rejections_name_the_field():
    invalid("req_nfr_coverage", {"nfrs": [_nfr("N1", "speed")]}, "nfrs")
    invalid("req_nfr_coverage", {"nfrs": [{"id": "N1"}]}, "nfrs")
    invalid("req_nfr_coverage", {"nfrs": [_nfr("", "perf")]}, "nfrs")
    invalid("req_nfr_coverage", {"nfrs": [], "required_kinds": []}, "required_kinds")
    invalid("req_nfr_coverage", {"nfrs": [], "required_kinds": ["perf"] * 6}, "required_kinds")
    invalid("req_nfr_coverage", {"nfrs": [], "required_kinds": ["speed"]}, "required_kinds")
    invalid("req_nfr_coverage", {"nfrs": [_nfr(f"N{i}", "perf") for i in range(501)]}, "nfrs")
    invalid("req_nfr_coverage", {}, "nfrs")
