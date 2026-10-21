"""Behaviour of the v2.1 A15 lane tools: output values for specific inputs, boundaries, malformed input and error branches.

Tools: docs_heading_order, docs_link_targets, docs_anchor_check, docs_changelog_section_order, docs_conventional_groups,
docs_api_endpoint_coverage, docs_flesch_grade, docs_stale_doc_age, docs_runbook_verified_ratio, docs_adr_render_coverage.
The three v2.2 A15 tools are covered by tests/test_tools_a15.py.
"""
from __future__ import annotations

import importlib

import pytest

from swarm.tools import a15_tools, batch_11_15


def call(name: str, payload, caller: str = "A15") -> dict:
    """Go through the registry gate. tests/conftest.py evicts swarm* modules, so resolve call_tool at call time."""
    return importlib.import_module("swarm.tool_registry").call_tool(name, caller, payload)


def invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT", result
    assert result["field"] == field, result


# ------------------------------------------------------------------------------ gate (all ten tools)

LEGACY_PAYLOADS = {
    "docs_heading_order": {"markdown": "# T"},
    "docs_link_targets": {"files": ["a.md"], "links": []},
    "docs_anchor_check": {"headings": [], "references": []},
    "docs_changelog_section_order": {"sections": ["Added"]},
    "docs_conventional_groups": {"subjects": ["feat: x"]},
    "docs_api_endpoint_coverage": {"total_endpoints": ["GET /a"]},
    "docs_flesch_grade": {"text": "Hello there."},
    "docs_stale_doc_age": {"as_of": "2026-10-15", "max_age_days": 7, "docs": [{"path": "a", "last_verified": "2026-10-14"}]},
    "docs_runbook_verified_ratio": {
        "as_of": "2026-10-15", "max_age_days": 7, "runbooks": [{"runbook_id": "R1", "tier": 1, "last_verified": "2026-10-14"}],
    },
    "docs_adr_render_coverage": {"adrs": [{"id": "ADR-1", "status": "accepted"}], "rendered": ["ADR-1"]},
}


@pytest.mark.parametrize("name", sorted(LEGACY_PAYLOADS))
def test_only_a15_may_call_and_an_unknown_field_is_named(name):
    payload = LEGACY_PAYLOADS[name]
    assert call(name, payload)["state"] == "SUCCESS"
    refused = call(name, payload, caller="A05")
    assert refused["state"] == "UNAUTHORIZED" and refused["caller"] == "A05" and refused["target"] == name
    invalid(call(name, {**payload, "surprise": 1}), "surprise")


# ------------------------------------------------------------------------------ docs_heading_order


def heading_order(markdown: str) -> dict:
    return call("docs_heading_order", {"markdown": markdown})


def test_heading_order_counts_h1_and_a_clean_ladder_has_no_skip():
    assert heading_order("# Title\n\n## A\n\n### B\n\n## C\n") == {"state": "SUCCESS", "h1": 1, "skips": 0}


def test_heading_order_counts_each_skipped_level_once():
    # H1 -> H3 skips; H3 -> H4 is fine; H4 -> H6 skips again
    assert heading_order("# T\n### A\n#### B\n###### C\n") == {"state": "SUCCESS", "h1": 1, "skips": 2}


def test_heading_order_counts_several_h1_and_a_drop_back_is_not_a_skip():
    assert heading_order("# One\n## Two\n### Three\n# Four\n## Five\n") == {"state": "SUCCESS", "h1": 2, "skips": 0}


def test_heading_order_first_heading_cannot_skip_even_when_it_is_deep():
    assert heading_order("### Deep start\n#### next\n") == {"state": "SUCCESS", "h1": 0, "skips": 0}


def test_heading_order_ignores_headings_inside_backtick_fences():
    text = "# Title\n\n```bash\n# a shell comment\n### not a heading\n```\n\n## Section\n"
    assert heading_order(text) == {"state": "SUCCESS", "h1": 1, "skips": 0}


def test_heading_order_needs_a_space_after_the_hashes_and_at_most_six_hashes():
    text = "#hashtag\n####### seven\n# real\n"
    assert heading_order(text) == {"state": "SUCCESS", "h1": 1, "skips": 0}


def test_heading_order_text_without_headings_is_all_zero():
    assert heading_order("just prose\nand more prose") == {"state": "SUCCESS", "h1": 0, "skips": 0}


def test_heading_order_closing_hashes_do_not_change_the_level():
    assert heading_order("# Title #\n## Section ##\n") == {"state": "SUCCESS", "h1": 1, "skips": 0}


def test_heading_order_empty_markdown_is_invalid_and_names_the_field():
    invalid(heading_order(""), "markdown")
    invalid(call("docs_heading_order", {}), "markdown")
    invalid(call("docs_heading_order", {"markdown": 5}), "markdown")


def test_heading_order_handler_matches_the_registry_result():
    payload = {"markdown": "# T\n### A\n"}
    assert batch_11_15.heading_order(payload) == {"state": "SUCCESS", "h1": 1, "skips": 1}


@pytest.mark.parametrize(
    "text, h1, skips",
    [
        ("# Title\n\n~~~\n# not a heading\n~~~\n", 1, 0),
        ("# Title\n\n~~~python\n### skipped level\n~~~\n## Next\n", 1, 0),
        ("# T\n~~~~\n~~~\n# still code\n~~~~\n## ok\n", 1, 0),  # a shorter fence does not close a longer one
        ("# T\n~~~\n```\n# still code\n```\n~~~\n## ok\n", 1, 0),  # a backtick fence inside a tilde fence is content
        ("# T\n```\n~~~\n# still code\n~~~\n```\n## ok\n", 1, 0),
        ("# T\n~~~\n# never closed\n## also code\n", 1, 0),  # an unclosed fence runs to the end of the document
        ("# T\n```\n# never closed\n", 1, 0),
        ("# T\n~~~\n# code\n~~~ trailing\n# still code\n~~~\n## ok\n", 1, 0),  # a closing fence carries no info string
        ("# T\n``` `x`\n# a heading: backtick info strings cannot contain backticks\n", 2, 0),
        ("# T\n~~~ a `b`\n# code\n~~~\n", 1, 0),
        ("# T\n```\ncode\n```\n### real skip\n", 1, 1),
        ("# T\n> ```md\n> # not a heading\n> ```\n## ok\n", 1, 0),  # a fence inside a blockquote is a fence (RV-H P3-3)
        ("# T\n>```\n># code\n>```\n## ok\n", 1, 0),
        ("# T\n> > ```\n> > # code\n> > ```\n## ok\n", 1, 0),  # two quote levels
        ("# T\n> ```\n> # code\n\n# real heading: the blank line ended the quote and the fence\n", 2, 0),
        ("# T\n> ```\n> # code\n> ````\n> ```\n# real\n", 2, 0),  # a longer closing fence closes; the later one reopens a fence
        ("# T\n```\n> ```\n# still code: a quoted fence line does not close a top-level fence\n```\n## ok\n", 1, 0),
        ("# T\n> ````\n> ```\n> # code\n> ```\n> ````\n## ok\n", 1, 0),
    ],
)
def test_heading_order_ignores_headings_inside_fences(text, h1, skips):
    result = heading_order(text)
    assert (result["h1"], result["skips"]) == (h1, skips)


# ------------------------------------------------------------------------------ docs_link_targets

FILES = ["README.md", "docs/index.md", "docs/guide/setup.md", "docs/api.md", "docs/img/logo.png"]


def link_targets(links: list, files: list | None = None) -> dict:
    return call("docs_link_targets", {"files": FILES if files is None else files, "links": links})


def test_link_targets_spec_example_counts_checked_external_and_broken():
    links = [
        {"source": "docs/index.md", "target": "guide/setup.md#install"},
        {"source": "docs/index.md", "target": "missing.md"},
        {"source": "README.md", "target": "https://example.com"},
        {"source": "docs/guide/setup.md", "target": "../api.md"},
    ]
    assert link_targets(links) == {
        "state": "SUCCESS", "checked": 3, "external": 1, "broken_count": 1, "broken": ["docs/index.md -> missing.md"],
    }


def test_link_targets_scheme_and_protocol_relative_links_are_external_and_never_broken():
    links = [{"source": "README.md", "target": t} for t in ("http://a.b", "mailto:x@y.z", "//cdn.example/x.js", "tel:+1")]
    assert link_targets(links) == {"state": "SUCCESS", "checked": 0, "external": 4, "broken_count": 0, "broken": []}


def test_link_targets_fragment_only_link_resolves_to_its_own_source():
    result = link_targets([{"source": "docs/api.md", "target": "#endpoints"}])
    assert (result["checked"], result["broken_count"]) == (1, 0)


def test_link_targets_query_string_and_fragment_are_ignored_for_the_path():
    links = [
        {"source": "docs/index.md", "target": "api.md?plain=1"},
        {"source": "docs/index.md", "target": "api.md?plain=1#top"},
    ]
    assert link_targets(links)["broken_count"] == 0


def test_link_targets_directory_links_resolve_through_parent_directories_of_listed_files():
    links = [
        {"source": "README.md", "target": "docs/guide/"},
        {"source": "README.md", "target": "docs"},
        {"source": "docs/guide/setup.md", "target": "./"},
        {"source": "docs/guide/setup.md", "target": "."},
    ]
    assert link_targets(links)["broken_count"] == 0


def test_link_targets_root_relative_target_resolves_from_the_repo_root_not_the_source_dir():
    links = [
        {"source": "docs/guide/setup.md", "target": "/docs/api.md"},
        {"source": "docs/guide/setup.md", "target": "/api.md"},
    ]
    result = link_targets(links)
    assert result["broken"] == ["docs/guide/setup.md -> /api.md"]


def test_link_targets_dotdot_above_the_repo_root_is_broken():
    result = link_targets([{"source": "README.md", "target": "../outside.md"}])
    assert result["broken"] == ["README.md -> ../outside.md"] and result["checked"] == 1


def test_link_targets_paths_are_case_sensitive_and_normalized_before_matching():
    links = [
        {"source": "docs/index.md", "target": "API.md"},
        {"source": "./docs//index.md", "target": "guide/../api.md"},
    ]
    assert link_targets(links)["broken"] == ["docs/index.md -> API.md"]


def test_link_targets_file_list_entries_are_normalized():
    result = link_targets([{"source": "docs/index.md", "target": "api.md"}], files=["./docs//api.md", "docs/index.md"])
    assert result["broken_count"] == 0


def test_link_targets_no_links_is_a_clean_zero_result():
    assert link_targets([]) == {"state": "SUCCESS", "checked": 0, "external": 0, "broken_count": 0, "broken": []}


def test_link_targets_broken_list_keeps_input_order_and_repeats_duplicates():
    links = [{"source": "README.md", "target": "b.md"}, {"source": "README.md", "target": "a.md"}, {"source": "README.md", "target": "b.md"}]
    assert link_targets(links)["broken"] == ["README.md -> b.md", "README.md -> a.md", "README.md -> b.md"]


def test_link_targets_malformed_input_names_the_field():
    invalid(call("docs_link_targets", {"files": [], "links": []}), "files")
    invalid(call("docs_link_targets", {"files": ["a.md"]}), "links")
    invalid(call("docs_link_targets", {"files": ["a.md"], "links": [{"source": "a.md"}]}), "links")
    invalid(call("docs_link_targets", {"files": ["a.md"], "links": [{"source": "", "target": "x"}]}), "links")
    invalid(call("docs_link_targets", {"files": [""], "links": []}), "files")


def test_link_targets_oversized_lists_are_refused_at_the_gate():
    invalid(call("docs_link_targets", {"files": ["a.md"] * 5001, "links": []}), "files")
    invalid(call("docs_link_targets", {"files": ["a.md"], "links": [{"source": "a.md", "target": "a.md"}] * 5001}), "links")


def test_link_targets_handler_directly_agrees_with_the_gate():
    payload = {"files": ["a.md"], "links": [{"source": "a.md", "target": "b.md"}]}
    assert a15_tools.docs_link_targets(payload)["broken"] == ["a.md -> b.md"]


def test_link_targets_percent_encoded_space_resolves_to_the_listed_file():
    result = link_targets([{"source": "README.md", "target": "my%20doc.md"}], files=["README.md", "my doc.md"])
    assert result["broken_count"] == 0


def test_link_targets_percent_decoding_applies_to_the_path_only_and_after_query_and_fragment_are_dropped():
    files = ["README.md", "my doc.md", "a#b.md"]
    links = [
        {"source": "README.md", "target": "my%20doc.md?plain=1#top"},
        {"source": "README.md", "target": "my%20doc.md#a%20b"},
        {"source": "README.md", "target": "a%23b.md"},  # %23 is a literal '#' in the file name, not a fragment
        {"source": "README.md", "target": "%6dy%20doc.md"},
    ]
    assert link_targets(links, files=files)["broken_count"] == 0
    assert link_targets([{"source": "README.md", "target": "my%20missing.md"}], files=files)["broken"] == ["README.md -> my%20missing.md"]


@pytest.mark.parametrize(
    "target",
    ["..%2F..%2Fetc", "..%2f..%2fetc/passwd", "docs%2Fapi.md", "docs%5Capi.md", "api.md%00", "%2e%2e/%2e%2e/etc", "%2E%2E%2F"],
)
def test_link_targets_encoded_separators_and_traversal_never_resolve(target):
    """An encoded slash must not become a separator that escapes the tree (or reaches a listed file by a detour)."""
    result = link_targets([{"source": "README.md", "target": target}], files=FILES + ["etc", "etc/passwd", "../etc"])
    assert result["checked"] == 1 and result["broken_count"] == 1


# ------------------------------------------------------------------------------ docs_anchor_check


def anchors(headings: list, references: list) -> dict:
    return call("docs_anchor_check", {"headings": headings, "references": references})


def test_anchor_check_spec_example_handles_duplicate_headings_and_case():
    result = anchors(["Install Steps", "API v2", "Install Steps"], ["#install-steps", "install-steps-1", "api-v2", "Install-Steps"])
    assert result == {
        "state": "SUCCESS", "headings": 3, "anchors": 3, "checked": 4, "broken_count": 1, "broken": ["Install-Steps"],
    }


def test_anchor_check_slug_drops_punctuation_and_keeps_hyphen_underscore_and_digits():
    result = anchors(["What's New? (v2.0)", "snake_case & kebab-case"], ["#whats-new-v20", "#snake_case--kebab-case"])
    assert result["broken"] == [] and result["anchors"] == 2


def test_anchor_check_third_repeat_gets_suffix_two():
    result = anchors(["Setup", "Setup", "Setup"], ["#setup", "#setup-1", "#setup-2", "#setup-3"])
    assert result["anchors"] == 3 and result["broken"] == ["#setup-3"]


def test_anchor_check_a_bare_hash_or_empty_reference_is_not_checked_as_broken():
    # the schema rejects empty strings, so '#' is the smallest reference that reaches the handler
    result = anchors(["Intro"], ["#"])
    assert result["checked"] == 1 and result["broken_count"] == 0


def test_anchor_check_references_report_with_their_original_spelling():
    result = anchors(["Intro"], ["#missing-one", "other"])
    assert result["broken"] == ["#missing-one", "other"] and result["broken_count"] == 2


def test_anchor_check_no_headings_makes_every_real_reference_broken():
    result = anchors([], ["#a", "b"])
    assert result == {"state": "SUCCESS", "headings": 0, "anchors": 0, "checked": 2, "broken_count": 2, "broken": ["#a", "b"]}


def test_anchor_check_heading_text_is_trimmed_and_lowercased():
    assert anchors(["  Mixed CASE Title  "], ["#mixed-case-title"])["broken_count"] == 0


def test_anchor_check_unicode_letters_are_kept_in_the_slug():
    assert anchors(["Übersicht Größe"], ["#übersicht-größe"])["broken_count"] == 0


def test_anchor_check_malformed_input_names_the_field():
    invalid(call("docs_anchor_check", {"references": ["#a"]}), "headings")
    invalid(call("docs_anchor_check", {"headings": []}), "references")
    invalid(call("docs_anchor_check", {"headings": [""], "references": []}), "headings")
    invalid(call("docs_anchor_check", {"headings": ["A"], "references": [3]}), "references")
    invalid(call("docs_anchor_check", {"headings": ["A"] * 2001, "references": []}), "headings")
    invalid(call("docs_anchor_check", {"headings": [], "references": ["#a"] * 5001}), "references")


# ------------------------------------------------------------------------------ docs_changelog_section_order


def changelog(sections: list) -> dict:
    return call("docs_changelog_section_order", {"sections": sections})


def test_changelog_full_keep_a_changelog_order_is_valid():
    result = changelog(["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"])
    assert result == {"state": "SUCCESS", "valid": True, "known": 6, "unknown": [], "duplicates": [], "out_of_order": []}


def test_changelog_a_subset_in_order_is_valid():
    assert changelog(["Added", "Fixed"])["valid"] is True


def test_changelog_out_of_order_section_is_named():
    result = changelog(["Added", "Fixed", "Changed"])
    assert result["valid"] is False and result["out_of_order"] == ["Changed"] and result["known"] == 3


def test_changelog_every_later_section_that_goes_back_is_reported_once():
    result = changelog(["Security", "Added", "Changed", "Added"])
    assert result["out_of_order"] == ["Added", "Changed"] and result["duplicates"] == ["Added"]


def test_changelog_duplicate_is_flagged_and_adjacent_duplicate_is_not_out_of_order():
    result = changelog(["Added", "Added", "Fixed"])
    assert result == {
        "state": "SUCCESS", "valid": False, "known": 3, "unknown": [], "duplicates": ["Added"], "out_of_order": [],
    }


def test_changelog_unknown_headings_are_listed_once_and_excluded_from_known():
    result = changelog(["Unreleased", "Added", "Misc", "Misc"])
    assert result["unknown"] == ["Unreleased", "Misc"] and result["known"] == 1
    assert result["duplicates"] == ["Misc"] and result["valid"] is False


def test_changelog_names_are_case_and_whitespace_sensitive():
    result = changelog(["added", "Added "])
    assert result["unknown"] == ["added", "Added "] and result["known"] == 0 and result["valid"] is False


def test_changelog_unknown_heading_does_not_disturb_the_order_of_known_ones():
    assert changelog(["Added", "Notes", "Fixed"])["out_of_order"] == []


def test_changelog_single_section_and_fifty_sections_are_accepted():
    assert changelog(["Security"])["valid"] is True
    assert changelog(["Added"] * 50)["duplicates"] == ["Added"]


def test_changelog_malformed_and_oversized_input_names_the_field():
    invalid(call("docs_changelog_section_order", {}), "sections")
    invalid(call("docs_changelog_section_order", {"sections": []}), "sections")
    invalid(call("docs_changelog_section_order", {"sections": ["Added"] * 51}), "sections")
    invalid(call("docs_changelog_section_order", {"sections": [""]}), "sections")
    invalid(call("docs_changelog_section_order", {"sections": "Added"}), "sections")


# ------------------------------------------------------------------------------ docs_conventional_groups


def groups(subjects: list) -> dict:
    return call("docs_conventional_groups", {"subjects": subjects})


def test_conventional_groups_spec_example():
    result = groups(["feat(api): add export", "fix!: drop legacy cache", "perf: speed up query", "docs: update guide", "wip stuff"])
    assert result == {
        "state": "SUCCESS", "total": 5, "added": 1, "changed": 1, "fixed": 1, "other": 1, "breaking": 1, "unparsed": ["wip stuff"],
    }


def test_conventional_groups_maps_each_type_to_its_section():
    result = groups(["feat: a", "fix: b", "perf: c", "refactor: d", "chore: e", "test: f", "ci: g"])
    assert (result["added"], result["fixed"], result["changed"], result["other"]) == (1, 1, 2, 3)


def test_conventional_groups_type_is_case_insensitive():
    result = groups(["FEAT: a", "Fix: b"])
    assert (result["added"], result["fixed"], result["unparsed"]) == (1, 1, [])


def test_conventional_groups_scope_and_breaking_marker_combine():
    result = groups(["feat(parser)!: new grammar", "fix(db)!: drop column", "chore!: bump node"])
    assert result["breaking"] == 3 and (result["added"], result["fixed"], result["other"]) == (1, 1, 1)


def test_conventional_groups_requires_colon_space_and_a_nonblank_description():
    result = groups(["feat:nospace", "feat: ", "feat:", "fix : spaced", "feat(): empty scope", "feat(a b): scope with space"])
    assert result["unparsed"] == ["feat:nospace", "feat: ", "feat:", "fix : spaced", "feat(): empty scope", "feat(a b): scope with space"]
    assert result["added"] == result["fixed"] == result["other"] == 0 and result["total"] == 6


def test_conventional_groups_merge_commits_and_prose_are_unparsed_in_input_order():
    result = groups(["Merge branch 'main'", "feat: x", "Update readme"])
    assert result["unparsed"] == ["Merge branch 'main'", "Update readme"] and result["added"] == 1


def test_conventional_groups_total_counts_every_subject_including_unparsed():
    result = groups(["feat: a", "nope", "fix: b"])
    assert result["total"] == 3 and result["added"] + result["fixed"] + result["changed"] + result["other"] + len(result["unparsed"]) == 3


def test_conventional_groups_description_may_contain_more_colons():
    assert groups(["feat(ui): add dark mode: toggle"])["added"] == 1


def test_conventional_groups_malformed_and_oversized_input_names_the_field():
    invalid(call("docs_conventional_groups", {}), "subjects")
    invalid(call("docs_conventional_groups", {"subjects": []}), "subjects")
    invalid(call("docs_conventional_groups", {"subjects": [""]}), "subjects")
    invalid(call("docs_conventional_groups", {"subjects": ["x" * 501]}), "subjects")
    invalid(call("docs_conventional_groups", {"subjects": ["feat: a"] * 5001}), "subjects")


def test_conventional_groups_boundary_subject_of_500_characters_is_accepted():
    subject = "feat: " + "a" * 494
    assert len(subject) == 500 and groups([subject])["added"] == 1


# ------------------------------------------------------------------------------ docs_api_endpoint_coverage


def coverage(total: list, documented: list | None = None, **extra) -> dict:
    payload = {"total_endpoints": total, **extra}
    if documented is not None:
        payload["documented_endpoints"] = documented
    return call("docs_api_endpoint_coverage", payload)


def test_api_coverage_spec_example_treats_path_parameter_names_as_equal():
    result = coverage(
        ["GET /users", "GET /users/:id", "POST /users", "DELETE /users/{id}"],
        ["GET /users", "get /users/{userId}", "POST /users", "GET /orders"],
        target_pct=100,
    )
    assert result == {
        "state": "SUCCESS", "total": 4, "covered": 3, "covered_pct": 75.0, "meets_target": False,
        "undocumented": ["DELETE /users/{id}"], "unmatched_documented": ["GET /orders"],
    }


def test_api_coverage_full_coverage_meets_the_default_target_of_100():
    result = coverage(["GET /a", "POST /a"], ["POST /a", "GET /a"])
    assert result["covered_pct"] == 100.0 and result["meets_target"] is True
    assert result["undocumented"] == [] and result["unmatched_documented"] == []


def test_api_coverage_documented_list_is_optional_and_defaults_to_nothing_documented():
    result = coverage(["GET /a"])
    assert (result["covered"], result["covered_pct"], result["meets_target"]) == (0, 0.0, False)
    assert result["undocumented"] == ["GET /a"]


def test_api_coverage_target_boundary_is_inclusive_and_zero_target_always_met():
    assert coverage(["GET /a", "GET /b", "GET /c", "GET /d"], ["GET /a", "GET /b", "GET /c"], target_pct=75)["meets_target"] is True
    assert coverage(["GET /a", "GET /b", "GET /c", "GET /d"], ["GET /a", "GET /b", "GET /c"], target_pct=75.01)["meets_target"] is False
    assert coverage(["GET /a"], [], target_pct=0)["meets_target"] is True


def test_api_coverage_percentage_is_rounded_to_two_decimals():
    result = coverage(["GET /a", "GET /b", "GET /c"], ["GET /a"])
    assert result["covered_pct"] == 33.33


def test_api_coverage_trailing_slash_and_method_case_are_normalized_root_path_kept():
    result = coverage(["get /users/", "GET /"], ["GET /users", "Get /"])
    assert result["covered"] == 2 and result["unmatched_documented"] == []


def test_api_coverage_duplicate_entries_collapse_and_keep_the_first_spelling():
    result = coverage(["GET /u/:id", "GET /u/{id}"], [])
    assert result["total"] == 1 and result["undocumented"] == ["GET /u/:id"]


def test_api_coverage_different_methods_or_extra_segments_do_not_match():
    result = coverage(["GET /users", "GET /users/:id/posts"], ["POST /users", "GET /users/{id}"])
    assert result["covered"] == 0
    assert result["undocumented"] == ["GET /users", "GET /users/:id/posts"]
    assert result["unmatched_documented"] == ["POST /users", "GET /users/{id}"]


def test_api_coverage_malformed_total_entry_names_total_endpoints():
    for bad in ("GET users", "FETCH /users", "/users", "GET", "GET  "):
        invalid(coverage([bad]), "total_endpoints")


def test_api_coverage_malformed_documented_entry_names_documented_endpoints():
    invalid(coverage(["GET /a"], ["nonsense"]), "documented_endpoints")
    invalid(coverage(["GET /a"], ["GET relative"]), "documented_endpoints")


def test_api_coverage_gate_rejects_bad_shapes_and_bounds():
    invalid(call("docs_api_endpoint_coverage", {}), "total_endpoints")
    invalid(call("docs_api_endpoint_coverage", {"total_endpoints": []}), "total_endpoints")
    invalid(call("docs_api_endpoint_coverage", {"total_endpoints": ["GET /a"], "target_pct": 101}), "target_pct")
    invalid(call("docs_api_endpoint_coverage", {"total_endpoints": ["GET /a"], "target_pct": -1}), "target_pct")
    invalid(call("docs_api_endpoint_coverage", {"total_endpoints": ["GET /" + "a" * 300]}), "total_endpoints")
    invalid(call("docs_api_endpoint_coverage", {"total_endpoints": ["GET /a"] * 5001}), "total_endpoints")


def test_api_coverage_handler_directly_reports_the_first_original_text_stripped():
    result = a15_tools.docs_api_endpoint_coverage({"total_endpoints": ["  PUT   /a/:x  "], "documented_endpoints": []})
    assert result["undocumented"] == ["PUT   /a/:x"]


# ------------------------------------------------------------------------------ docs_flesch_grade


def flesch(text: str, **extra) -> dict:
    return call("docs_flesch_grade", {"text": text, **extra})


TWELVE = "The cat sat on the mat. It was happy. Dogs run fast."


def test_flesch_spec_example_counts_and_scores():
    result = flesch(TWELVE, max_grade=9)
    assert (result["words"], result["sentences"], result["syllables"]) == (12, 3, 13)
    # grade = 0.39*4 + 11.8*(13/12) - 15.59 ; ease = 206.835 - 1.015*4 - 84.6*(13/12)
    assert result["grade"] == pytest.approx(-1.2, abs=0.06)
    assert result["reading_ease"] == pytest.approx(111.1, abs=0.06)
    assert result["within_target"] is True and result["state"] == "SUCCESS"


def test_flesch_hard_prose_exceeds_the_default_grade_limit_of_nine():
    text = "Notwithstanding established precedent, administrative organizations frequently implement comprehensive regulatory methodologies."
    result = flesch(text)
    assert result["words"] == 10 and result["sentences"] == 1
    assert result["syllables"] == 41
    # grade = 0.39*10 + 11.8*4.1 - 15.59 ; ease = 206.835 - 1.015*10 - 84.6*4.1
    assert result["grade"] == pytest.approx(36.7, abs=0.06)
    assert result["reading_ease"] == pytest.approx(-150.2, abs=0.06)
    assert result["within_target"] is False
    assert flesch(text, max_grade=30)["within_target"] is False  # the schema caps the limit at 30, this text is above it


def test_flesch_max_grade_boundary_is_inclusive():
    text = "Dogs run fast and the old man sat on a mat near the bay."
    grade = flesch(text)["grade"]
    assert grade == 1.7
    assert flesch(text, max_grade=grade)["within_target"] is True
    assert flesch(text, max_grade=grade - 0.1)["within_target"] is False


def test_flesch_syllable_heuristic_silent_e_and_le_endings():
    # table -> 2 (le kept), make -> 1 (silent e), the -> 1, queue -> 1, rhythm -> 1
    assert flesch("table")["syllables"] == 2
    assert flesch("make")["syllables"] == 1
    assert flesch("the")["syllables"] == 1
    assert flesch("queue rhythm")["syllables"] == 2
    assert flesch("e")["syllables"] == 1


def test_flesch_contractions_are_single_words_and_digits_are_not_words():
    result = flesch("Don't stop 42 times.")
    assert result["words"] == 3 and result["sentences"] == 1


def test_flesch_sentence_terminators_group_and_empty_segments_do_not_count():
    result = flesch("Wait... what?! Yes. ")
    assert result["words"] == 3 and result["sentences"] == 3
    assert flesch("no terminator at all")["sentences"] == 1


def test_flesch_text_without_any_word_is_invalid_for_the_text_field():
    invalid(flesch("123 456 ... !!!"), "text")
    invalid(flesch("   "), "text")


def test_flesch_gate_rejects_empty_oversized_and_out_of_range_input():
    invalid(call("docs_flesch_grade", {"text": ""}), "text")
    invalid(call("docs_flesch_grade", {"text": "x" * 200001}), "text")
    invalid(call("docs_flesch_grade", {"text": "Hi", "max_grade": -0.1}), "max_grade")
    invalid(call("docs_flesch_grade", {"text": "Hi", "max_grade": 30.5}), "max_grade")
    invalid(call("docs_flesch_grade", {}), "text")


def test_flesch_maximum_length_text_is_scored_quickly():
    result = flesch("cat " * 50000)  # 200000 characters
    assert result["words"] == 50000 and result["sentences"] == 1 and result["syllables"] == 50000


def test_flesch_longer_sentences_raise_the_grade():
    short = flesch("Cat sat. Dog ran. Bird flew.")["grade"]
    long = flesch("Cat sat and dog ran and bird flew.")["grade"]
    assert long > short


# ------------------------------------------------------------------------------ docs_stale_doc_age


def stale(docs: list, as_of: str = "2026-10-15", limit: int = 7) -> dict:
    return call("docs_stale_doc_age", {"as_of": as_of, "max_age_days": limit, "docs": docs})


def test_stale_age_spec_example():
    result = stale([
        {"path": "docs/a.md", "last_verified": "2026-10-10"},
        {"path": "docs/b.md", "last_verified": "2026-08-15"},
        {"path": "docs/c.md", "last_verified": "2026-13-40"},
    ])
    assert result == {
        "state": "SUCCESS", "total": 3, "checked": 2, "stale": ["docs/b.md"], "stale_count": 1,
        "stale_pct": 33.33, "invalid": ["docs/c.md"], "oldest_days": 61,
    }


def test_stale_age_limit_is_exclusive_exactly_max_age_days_is_still_fresh():
    result = stale([{"path": "edge", "last_verified": "2026-10-08"}, {"path": "over", "last_verified": "2026-10-07"}])
    assert result["stale"] == ["over"] and result["oldest_days"] == 8


def test_stale_age_zero_limit_makes_anything_not_verified_today_stale():
    result = stale([{"path": "today", "last_verified": "2026-10-15"}, {"path": "yday", "last_verified": "2026-10-14"}], limit=0)
    assert result["stale"] == ["yday"]


def test_stale_age_future_and_empty_and_malformed_dates_are_invalid_not_stale():
    docs = [
        {"path": "future", "last_verified": "2026-10-16"},
        {"path": "empty", "last_verified": ""},
        {"path": "slashes", "last_verified": "2026/10/01"},
        {"path": "short", "last_verified": "2026-1-1"},
        {"path": "ok", "last_verified": "2026-10-15"},
    ]
    result = stale(docs)
    assert result["invalid"] == ["future", "empty", "slashes", "short"]
    assert result["checked"] == 1 and result["stale"] == [] and result["oldest_days"] == 0


def test_stale_age_all_invalid_has_zero_oldest_and_pct_uses_total():
    result = stale([{"path": "a", "last_verified": ""}])
    assert result["oldest_days"] == 0 and result["checked"] == 0 and result["stale_pct"] == 0.0 and result["total"] == 1


def test_stale_age_pct_divides_by_all_docs_including_invalid_ones():
    result = stale([
        {"path": "old", "last_verified": "2020-01-01"},
        {"path": "bad", "last_verified": ""},
    ])
    assert result["stale_pct"] == 50.0 and result["stale_count"] == 1


def test_stale_age_leap_day_is_counted():
    result = stale([{"path": "a", "last_verified": "2028-02-28"}], as_of="2028-03-01", limit=0)
    assert result["oldest_days"] == 2


def test_stale_age_preserves_input_order_of_stale_paths():
    docs = [{"path": p, "last_verified": "2020-01-01"} for p in ("z.md", "a.md", "m.md")]
    assert stale(docs)["stale"] == ["z.md", "a.md", "m.md"]


def test_stale_age_bad_as_of_names_as_of():
    docs = [{"path": "a", "last_verified": "2026-10-01"}]
    invalid(stale(docs, as_of="2026-13-01"), "as_of")
    invalid(stale(docs, as_of="not-a-date"), "as_of")
    invalid(stale(docs, as_of="2026-1-1"), "as_of")  # shorter than 10 is stopped by the schema
    invalid(stale(docs, as_of="2026/10/01"), "as_of")


def test_stale_age_gate_rejects_bounds_and_shape_errors():
    docs = [{"path": "a", "last_verified": "2026-10-01"}]
    invalid(stale(docs, limit=-1), "max_age_days")
    invalid(stale(docs, limit=3651), "max_age_days")
    invalid(call("docs_stale_doc_age", {"as_of": "2026-10-15", "max_age_days": 1.5, "docs": docs}), "max_age_days")
    invalid(call("docs_stale_doc_age", {"as_of": "2026-10-15", "max_age_days": 1, "docs": []}), "docs")
    invalid(call("docs_stale_doc_age", {"as_of": "2026-10-15", "max_age_days": 1, "docs": [{"path": "a"}]}), "docs")
    invalid(call("docs_stale_doc_age", {"as_of": "2026-10-15", "max_age_days": 1, "docs": [{"path": "a", "last_verified": "2026-10-010"}]}), "docs")
    invalid(call("docs_stale_doc_age", {"as_of": "2026-10-15", "max_age_days": 1}), "docs")


# ------------------------------------------------------------------------------ docs_runbook_verified_ratio


def rb_ratio(runbooks: list, as_of: str = "2026-10-15", limit: int = 30, **extra) -> dict:
    return call("docs_runbook_verified_ratio", {"as_of": as_of, "max_age_days": limit, "runbooks": runbooks, **extra})


def rbk(rid: str, tier: int, verified: str) -> dict:
    return {"runbook_id": rid, "tier": tier, "last_verified": verified}


def test_runbook_ratio_counts_only_tier_one_and_lists_unverified_ids_in_order():
    result = rb_ratio([
        rbk("RB-1", 1, "2026-10-10"), rbk("RB-2", 1, "2026-01-01"), rbk("RB-3", 2, "2020-01-01"),
        rbk("RB-4", 3, ""), rbk("RB-5", 1, ""), rbk("RB-6", 1, "2026-10-14"),
    ])
    assert result == {
        "state": "SUCCESS", "tier1_total": 4, "tier1_verified": 2, "tier1_verified_pct": 50.0,
        "meets_target": False, "unverified": ["RB-2", "RB-5"],
    }


def test_runbook_ratio_window_boundary_is_inclusive():
    result = rb_ratio([rbk("on", 1, "2026-09-15"), rbk("past", 1, "2026-09-14")])
    assert result["unverified"] == ["past"] and result["tier1_verified"] == 1


def test_runbook_ratio_future_and_malformed_dates_are_unverified():
    result = rb_ratio([rbk("f", 1, "2026-10-16"), rbk("m", 1, "2026-02-30"), rbk("s", 1, "2026/10/01"), rbk("g", 1, "2026-10-15")])
    assert result["unverified"] == ["f", "m", "s"] and result["tier1_verified"] == 1


def test_runbook_ratio_default_target_is_95_and_boundary_is_inclusive():
    nineteen_ok = [rbk(f"ok{i}", 1, "2026-10-14") for i in range(19)]
    result = rb_ratio(nineteen_ok + [rbk("bad", 1, "")])
    assert result["tier1_verified_pct"] == 95.0 and result["meets_target"] is True
    result = rb_ratio(nineteen_ok[:18] + [rbk("bad", 1, ""), rbk("bad2", 1, "")])
    assert result["tier1_verified_pct"] == 90.0 and result["meets_target"] is False


def test_runbook_ratio_explicit_target_overrides_the_default():
    runbooks = [rbk("a", 1, "2026-10-14"), rbk("b", 1, "")]
    assert rb_ratio(runbooks, target_pct=50)["meets_target"] is True
    assert rb_ratio(runbooks, target_pct=50.5)["meets_target"] is False
    assert rb_ratio(runbooks, target_pct=0)["meets_target"] is True


def test_runbook_ratio_percentage_rounds_to_two_decimals():
    result = rb_ratio([rbk("a", 1, "2026-10-14"), rbk("b", 1, ""), rbk("c", 1, "")])
    assert result["tier1_verified_pct"] == 33.33


def test_runbook_ratio_no_tier_one_runbook_is_invalid_for_runbooks():
    invalid(rb_ratio([rbk("a", 2, "2026-10-14"), rbk("b", 3, "2026-10-14")]), "runbooks")


def test_runbook_ratio_duplicate_id_is_invalid_even_across_tiers():
    result = rb_ratio([rbk("RB-1", 1, "2026-10-14"), rbk("RB-1", 2, "2026-10-14")])
    invalid(result, "runbooks")
    assert "RB-1" in result["message"]


def test_runbook_ratio_bad_as_of_names_as_of_before_runbooks_are_looked_at():
    invalid(rb_ratio([rbk("a", 2, "")], as_of="2026-99-99"), "as_of")


def test_runbook_ratio_gate_rejects_bad_tier_bounds_and_shapes():
    invalid(rb_ratio([rbk("a", 0, "2026-10-14")]), "runbooks")
    invalid(rb_ratio([rbk("a", 4, "2026-10-14")]), "runbooks")
    invalid(rb_ratio([rbk("", 1, "2026-10-14")]), "runbooks")
    invalid(rb_ratio([]), "runbooks")
    invalid(rb_ratio([rbk("a", 1, "2026-10-14")], limit=-1), "max_age_days")
    invalid(rb_ratio([rbk("a", 1, "2026-10-14")], limit=3651), "max_age_days")
    invalid(rb_ratio([rbk("a", 1, "2026-10-14")], target_pct=100.5), "target_pct")
    invalid(call("docs_runbook_verified_ratio", {"as_of": "2026-10-15", "max_age_days": 3, "runbooks": [{"runbook_id": "a", "tier": 1}]}), "runbooks")
    invalid(call("docs_runbook_verified_ratio", {"as_of": "2026-10-15", "max_age_days": 3, "runbooks": [rbk("a", True, "")]}), "runbooks")


# ------------------------------------------------------------------------------ docs_adr_render_coverage


def adr_cov(adrs: list, rendered: list, **extra) -> dict:
    return call("docs_adr_render_coverage", {"adrs": adrs, "rendered": rendered, **extra})


def adr(adr_id: str, status: str) -> dict:
    return {"id": adr_id, "status": status}


def test_adr_coverage_only_accepted_superseded_deprecated_are_in_scope():
    adrs = [adr("ADR-1", "accepted"), adr("ADR-2", "superseded"), adr("ADR-3", "deprecated"), adr("ADR-4", "proposed"), adr("ADR-5", "rejected")]
    result = adr_cov(adrs, ["ADR-1", "ADR-2"])
    assert result == {
        "state": "SUCCESS", "in_scope": 3, "rendered_in_scope": 2, "adr_rendered_pct": 66.67, "meets_target": False,
        "missing": ["ADR-3"], "orphan_rendered": [], "excluded": 2,
    }


def test_adr_coverage_full_render_meets_the_default_target_of_100():
    result = adr_cov([adr("A", "accepted"), adr("B", "accepted")], ["B", "A"])
    assert result["adr_rendered_pct"] == 100.0 and result["meets_target"] is True and result["missing"] == []


def test_adr_coverage_rendering_an_out_of_scope_adr_is_neither_counted_nor_orphan():
    result = adr_cov([adr("A", "accepted"), adr("P", "proposed")], ["A", "P"])
    assert result["rendered_in_scope"] == 1 and result["orphan_rendered"] == [] and result["excluded"] == 1


def test_adr_coverage_unknown_rendered_ids_are_orphans_in_first_seen_order_without_repeats():
    result = adr_cov([adr("A", "accepted")], ["Z", "A", "Y", "Z"])
    assert result["orphan_rendered"] == ["Z", "Y"] and result["rendered_in_scope"] == 1


def test_adr_coverage_repeated_rendered_ids_do_not_inflate_the_ratio():
    result = adr_cov([adr("A", "accepted"), adr("B", "accepted")], ["A", "A", "A"])
    assert result["rendered_in_scope"] == 1 and result["adr_rendered_pct"] == 50.0 and result["missing"] == ["B"]


def test_adr_coverage_target_boundary_and_zero_rendered():
    adrs = [adr("A", "accepted"), adr("B", "accepted")]
    assert adr_cov(adrs, ["A"], target_pct=50)["meets_target"] is True
    assert adr_cov(adrs, ["A"], target_pct=50.01)["meets_target"] is False
    none = adr_cov(adrs, [])
    assert none["rendered_in_scope"] == 0 and none["adr_rendered_pct"] == 0.0 and none["missing"] == ["A", "B"]


def test_adr_coverage_ids_are_case_sensitive():
    result = adr_cov([adr("ADR-1", "accepted")], ["adr-1"])
    assert result["missing"] == ["ADR-1"] and result["orphan_rendered"] == ["adr-1"]


def test_adr_coverage_duplicate_adr_id_is_invalid_for_adrs():
    result = adr_cov([adr("A", "accepted"), adr("A", "proposed")], ["A"])
    invalid(result, "adrs")
    assert "A" in result["message"]


def test_adr_coverage_with_nothing_in_scope_is_invalid_for_adrs():
    invalid(adr_cov([adr("A", "proposed"), adr("B", "rejected")], []), "adrs")


def test_adr_coverage_gate_rejects_unknown_status_and_bad_shapes():
    invalid(adr_cov([adr("A", "approved")], []), "adrs")
    invalid(adr_cov([], []), "adrs")
    invalid(adr_cov([adr("", "accepted")], []), "adrs")
    invalid(adr_cov([{"id": "A"}], []), "adrs")
    invalid(adr_cov([adr("A", "accepted")], [""]), "rendered")
    invalid(adr_cov([adr("A", "accepted")], ["A"] * 2001), "rendered")
    invalid(adr_cov([adr("A", "accepted")], [], target_pct=101), "target_pct")
    invalid(call("docs_adr_render_coverage", {"adrs": [adr("A", "accepted")]}), "rendered")


# ------------------------------------------------------------------------------ probes (contract-test only)


@pytest.mark.parametrize("name", sorted(LEGACY_PAYLOADS))
def test_probe_values_never_reach_the_lane_logic(name):
    partial = call(name, {**LEGACY_PAYLOADS[name], "probe": "partial"})
    assert partial == {"state": "PARTIAL_SUCCESS", "completed_fraction": 0.5}
    assert call(name, {**LEGACY_PAYLOADS[name], "probe": "dependency"})["state"] == "DEPENDENCY_UNAVAILABLE"
    invalid(call(name, {**LEGACY_PAYLOADS[name], "probe": "bogus"}), "probe")


# ------------------------------------------------------------------------------ scripts/docs_bundle.py fences in quotes


def test_docs_bundle_does_not_check_links_inside_a_fence_in_a_blockquote(tmp_path):
    """RV-H P3-3: `> ```md` / `> See [the guide](missing-guide.md)` / `> ```` raised a major broken-link finding."""
    docs = importlib.import_module("swarm.tools.support").script("docs_bundle")
    quoted = tmp_path / "quoted.md"
    quoted.write_text("# T\n\n> ```md\n> See [the guide](missing-guide.md)\n> ```\n", encoding="utf-8")
    info, findings = docs.analyse_md(quoted, tmp_path)
    assert findings == [] and info["links"] == 0
    control = tmp_path / "control.md"
    control.write_text("# T\n\n> See [the guide](missing-guide.md)\n", encoding="utf-8")
    _, findings = docs.analyse_md(control, tmp_path)
    assert [f["kind"] for f in findings] == ["broken-link"]
    after = tmp_path / "after.md"
    after.write_text("# T\n\n> ```md\n> code\n\nSee [the guide](missing-guide.md)\n", encoding="utf-8")
    _, findings = docs.analyse_md(after, tmp_path)
    assert [f["kind"] for f in findings] == ["broken-link"]  # the blank line ended the quote and its fence


def test_docs_bundle_strip_code_keeps_line_numbers_for_quoted_fences():
    docs = importlib.import_module("swarm.tools.support").script("docs_bundle")
    text = "a\n> ```py\n> x\n> ```\nb\n"
    assert docs.strip_code(text) == "a\n\n\n\nb\n"
