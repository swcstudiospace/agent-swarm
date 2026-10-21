"""A15 v2.2 tools: runbook schema, digest staleness, terminology drift. Handler behaviour plus registry shape."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm.tool_registry import call_tool, load_document, validate_registry
from swarm.tools.a15_tools import docs_digest_staleness, docs_runbook_schema_check, docs_terminology_drift

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a15-docs.tools.json"
NEW_TOOLS = ("docs_runbook_schema_check", "docs_digest_staleness", "docs_terminology_drift")
D1 = "sha256:" + "a" * 64
D2 = "sha256:" + "b" * 64


# ---------------------------------------------------------------- docs_runbook_schema_check

RB31 = {
    "runbook_id": "RB-31",
    "title": "checkout error-rate spike",
    "trigger": "slo:checkout.availability burn>6x",
    "preconditions": ["sev<=2"],
    "steps": [
        {"action": "flag-off", "target": "checkout-v2", "reversible": True},
        {"action": "rollback", "release_delta": -1},
    ],
    "whitelisted_for_automation": ["flag-off"],
    "last_verified": "2026-08-15",
}


def rb(**changes) -> dict:
    doc = copy.deepcopy(RB31)
    doc.update(changes)
    return doc


def check(*runbooks, **extra) -> dict:
    return docs_runbook_schema_check({"runbooks": list(runbooks), **extra})


def test_spec_example_runbook_is_valid():
    result = check(RB31, known_actions=["flag-off", "rollback", "scale-out"])
    assert result == {
        "state": "SUCCESS", "checked": 1, "valid_count": 1, "valid": True,
        "invalid": [], "non_reversible_whitelisted": [],
    }


def test_missing_trigger_and_title_are_named_by_field():
    result = check(rb(trigger="  ", title=""))
    assert result["valid"] is False
    assert [(i["field"], i["runbook_id"]) for i in result["invalid"]] == [("title", "RB-31"), ("trigger", "RB-31")]
    assert result["valid_count"] == 0


def test_runbook_without_steps_is_invalid():
    doc = rb()
    del doc["steps"]
    doc["whitelisted_for_automation"] = []
    assert [i["field"] for i in check(doc)["invalid"]] == ["steps"]


def test_whitelisted_action_must_be_a_step():
    result = check(rb(whitelisted_for_automation=["flag-off", "scale-out"]))
    assert result["invalid"] == [
        {"runbook_id": "RB-31", "field": "whitelisted_for_automation", "reason": "action scale-out is not a step of this runbook"}
    ]


def test_whitelisted_step_without_reversible_true_is_reported_separately():
    result = check(rb(whitelisted_for_automation=["flag-off", "rollback"]))
    assert result["non_reversible_whitelisted"] == [{"runbook_id": "RB-31", "action": "rollback"}]
    assert result["invalid"] == []
    assert result["valid"] is False and result["valid_count"] == 0


def test_action_repeated_with_one_irreversible_step_counts_as_irreversible():
    steps = [{"action": "flag-off", "reversible": True}, {"action": "flag-off"}]
    result = check(rb(steps=steps))
    assert result["non_reversible_whitelisted"] == [{"runbook_id": "RB-31", "action": "flag-off"}]


@pytest.mark.parametrize("value, reason", [
    ("", "last_verified is missing"),
    ("2026-13-01", "last_verified must be a YYYY-MM-DD date"),
    ("15/08/2026", "last_verified must be a YYYY-MM-DD date"),
    ("2026-8-5", "last_verified must be a YYYY-MM-DD date"),
])
def test_last_verified_must_be_a_real_calendar_date(value, reason):
    assert check(rb(last_verified=value))["invalid"] == [{"runbook_id": "RB-31", "field": "last_verified", "reason": reason}]


def test_duplicate_runbook_id_is_reported_once_and_counts_one_runbook():
    result = check(RB31, rb(title="copy"))
    assert result["checked"] == 1
    assert result["invalid"] == [{"runbook_id": "RB-31", "field": "runbook_id", "reason": "duplicate runbook_id RB-31"}]


def test_known_actions_flag_unknown_step_actions_only_when_supplied():
    assert check(RB31)["valid"] is True
    result = check(RB31, known_actions=["flag-off"])
    assert result["invalid"] == [{"runbook_id": "RB-31", "field": "steps[1].action", "reason": "unknown action rollback"}]


def test_blank_step_action_and_blank_runbook_id():
    assert check(rb(steps=[{"target": "x"}], whitelisted_for_automation=[]))["invalid"] == [
        {"runbook_id": "RB-31", "field": "steps[0].action", "reason": "step action is missing"}
    ]
    bad = docs_runbook_schema_check({"runbooks": [rb(runbook_id="  ")]})
    assert bad["state"] == "INVALID_INPUT" and bad["field"] == "runbooks[0].runbook_id"


def test_second_runbook_valid_count_is_independent():
    result = check(RB31, rb(runbook_id="RB-32", trigger=""))
    assert (result["checked"], result["valid_count"]) == (2, 1)


# ---------------------------------------------------------------- docs_digest_staleness

def stale(docs, current) -> dict:
    return docs_digest_staleness({"docs": docs, "current": current})


def test_matching_digest_is_fresh():
    result = stale([{"path": "a.md", "bound_artifact": "api.contract", "bound_digest": D1}], [{"artifact": "api.contract", "digest": D1}])
    assert result == {"state": "SUCCESS", "total": 1, "fresh_count": 1, "stale": [], "unbound": [], "stale_scopes": []}


def test_changed_digest_marks_doc_stale_and_names_the_scope():
    docs = [
        {"path": "a.md", "bound_artifact": "api.contract", "bound_digest": D1},
        {"path": "b.md", "bound_artifact": "adr.set", "bound_digest": D1},
    ]
    result = stale(docs, [{"artifact": "api.contract", "digest": D2}, {"artifact": "adr.set", "digest": D1}])
    assert result["stale"] == [{"path": "a.md", "reason": "digest of api.contract changed"}]
    assert result["stale_scopes"] == ["api.contract"]
    assert result["fresh_count"] == 1


def test_unknown_bound_artifact_is_stale_not_fresh():
    result = stale([{"path": "a.md", "bound_artifact": "gone", "bound_digest": D1}], [{"artifact": "other", "digest": D1}])
    assert result["stale"][0]["reason"] == "bound artifact gone is not in the current set"


def test_doc_without_a_binding_is_unbound_and_not_counted_fresh():
    result = stale([{"path": "a.md"}, {"path": "b.md", "bound_digest": D1}], [{"artifact": "x", "digest": D1}])
    assert result["unbound"] == ["a.md", "b.md"]
    assert result["fresh_count"] == 0 and result["stale"] == []


def test_bound_artifact_without_digest_or_with_malformed_digest_fails_closed():
    docs = [{"path": "a.md", "bound_artifact": "x"}, {"path": "b.md", "bound_artifact": "x", "bound_digest": "sha256:abc"}]
    result = stale(docs, [{"artifact": "x", "digest": D1}])
    assert [item["reason"] for item in result["stale"]] == ["no bound_digest recorded", "bound_digest is malformed"]
    assert result["stale_scopes"] == ["x"]


def test_malformed_current_digest_is_invalid_input():
    result = stale([{"path": "a.md"}], [{"artifact": "x", "digest": "md5:1234"}])
    assert result["state"] == "INVALID_INPUT" and result["field"] == "current[0].digest"


def test_current_artifact_with_two_digests_and_duplicate_doc_paths_are_invalid():
    conflict = stale([{"path": "a.md"}], [{"artifact": "x", "digest": D1}, {"artifact": "x", "digest": D2}])
    assert (conflict["state"], conflict["field"]) == ("INVALID_INPUT", "current")
    twice = stale([{"path": "a.md"}, {"path": "a.md"}], [])
    assert (twice["state"], twice["field"]) == ("INVALID_INPUT", "docs")


def test_same_digest_listed_twice_is_accepted_and_empty_current_stales_every_bound_doc():
    assert stale([{"path": "a.md"}], [{"artifact": "x", "digest": D1}, {"artifact": "x", "digest": D1}])["fresh_count"] == 0
    result = stale([{"path": "a.md", "bound_artifact": "x", "bound_digest": D1}], [])
    assert result["stale_scopes"] == ["x"]


# ---------------------------------------------------------------- docs_terminology_drift

GLOSSARY = [{"preferred": "runbook", "deprecated": ["playbook", "run book"]}]


def drift(text: str, glossary=None, path: str = "a.md") -> dict:
    return docs_terminology_drift({"glossary": glossary or GLOSSARY, "files": [{"path": path, "text": text}]})


def test_violation_reports_file_line_found_text_and_preferred_term():
    result = drift("# Ops\n\nOpen the Playbook first.\n")
    assert result["violations"] == [{"path": "a.md", "line": 3, "found": "Playbook", "preferred": "runbook"}]
    assert result["by_term"] == {"runbook": 1} and result["pass_pct"] == 0.0


def test_word_boundaries_prevent_substring_hits():
    assert drift("playbooks and playbook_x and xplaybook")["violation_count"] == 0
    assert drift("the playbook, the playbook.")["violation_count"] == 2


def test_fenced_blocks_and_inline_code_are_skipped_and_lines_stay_aligned():
    text = "```\nplaybook\n```\nuse `playbook` here\n~~~\nplaybook\n~~~\nreal playbook\n"
    result = drift(text)
    assert [v["line"] for v in result["violations"]] == [8]


def test_multi_word_terms_tolerate_any_whitespace_and_overlaps_prefer_the_longer_term():
    glossary = [{"preferred": "sign in", "deprecated": ["log in", "log in to"]}, {"preferred": "runbook", "deprecated": ["run book"]}]
    result = drift("Please log   in to the run book.\n", glossary)
    assert [(v["found"], v["preferred"]) for v in result["violations"]] == [("log   in to", "sign in"), ("run book", "runbook")]


def test_pass_pct_counts_files_without_violations():
    result = docs_terminology_drift({
        "glossary": GLOSSARY,
        "files": [{"path": "a.md", "text": "playbook"}, {"path": "b.md", "text": "runbook"}, {"path": "c.md", "text": ""}],
    })
    assert (result["files_checked"], result["pass_pct"]) == (3, 66.67)


def test_longer_backtick_fence_is_not_closed_by_a_shorter_inner_fence():
    """Reviewer repro (RV-E 4a): a four-backtick block holding a ```bash example was closed by the inner
    fence, so `echo playbook` (code) was reported as a violation."""
    text = "````markdown\n```bash\necho playbook\n```\nstill code playbook\n````\nreal playbook\n"
    assert [v["line"] for v in drift(text)["violations"]] == [7]


def test_a_tilde_fence_is_not_closed_by_a_backtick_fence_line():
    """Reviewer repro (RV-E 4b): "~~~\\n```\\n~~~\\nProse whitelist here.\\n" reported 0 violations because the
    backtick line toggled the fence state back to prose and the closing ~~~ reopened it."""
    glossary = [{"preferred": "allowlist", "deprecated": ["whitelist"]}]
    result = drift("~~~\n```\n~~~\nProse whitelist here.\n", glossary)
    assert [(v["line"], v["found"]) for v in result["violations"]] == [(4, "whitelist")]


def test_a_closing_fence_needs_no_info_string_and_the_same_character():
    text = "```python\n```js\nplaybook\n```\nafter playbook\n"
    assert [v["line"] for v in drift(text)["violations"]] == [5]
    assert [v["line"] for v in drift("~~~\nplaybook\n~~~ tail\nplaybook\n~~~\nplaybook\n")["violations"]] == [6]


def test_a_large_glossary_over_many_files_finishes_inside_the_registry_timeout(merged):
    """Reviewer repro (RV-E 5): 100 entries x 2 deprecated terms over 200 files x 150 lines took 10.2 s direct
    (one regex pass per term) and returned TIMEOUT (5 s) through the registry."""
    glossary = [{"preferred": f"pref{i}", "deprecated": [f"old{i}a", f"old{i}b term"]} for i in range(100)]
    line = "Some ordinary documentation prose that mentions old7a and nothing else of interest here."
    files = [{"path": f"f{n}.md", "text": "\n".join([line] * 150)} for n in range(200)]
    result = call_tool("docs_terminology_drift", "A15", {"glossary": glossary, "files": files}, data=merged)
    assert result["state"] == "SUCCESS"
    assert result["violation_count"] == 200 * 150 and result["by_term"] == {"pref7": 200 * 150}
    assert len(result["violations"]) == 500 and result["violations_truncated"] is True  # a sample; the registry bounds output bytes


@pytest.mark.parametrize("glossary, field", [
    ([{"preferred": "a", "deprecated": ["x"]}, {"preferred": "b", "deprecated": ["X"]}], "glossary[1].deprecated"),
    ([{"preferred": "a", "deprecated": ["A"]}], "glossary[0].deprecated"),
    ([{"preferred": "a", "deprecated": ["x"]}, {"preferred": "A", "deprecated": ["y"]}], "glossary[1].preferred"),
    ([{"preferred": "  ", "deprecated": ["x"]}], "glossary[0].preferred"),
    ([{"preferred": "a", "deprecated": ["  "]}], "glossary[0].deprecated"),
])
def test_ambiguous_glossaries_are_invalid_input(glossary, field):
    result = drift("text", glossary)
    assert result["state"] == "INVALID_INPUT" and result["field"] == field


def test_duplicate_file_paths_are_invalid_input():
    result = docs_terminology_drift({"glossary": GLOSSARY, "files": [{"path": "a.md", "text": ""}, {"path": "a.md", "text": ""}]})
    assert (result["state"], result["field"]) == ("INVALID_INPUT", "files")


MASTER = [{"preferred": "main", "deprecated": ["master", "white list"]}]


@pytest.mark.parametrize("text, found", [("push to maſter now", "maſter"), ("a whıtelist here", None), ("WHİTELIST", None),
                                         ("white\u00a0list", "white\u00a0list"), ("MASTER", "MASTER")])
def test_unicode_case_folding_matches_never_crash_the_lint(text, found):
    """RV-H P2-4: `ſ` matches `s` and `İ` matches `I` under re.IGNORECASE; resolving the key by `.lower()` raised KeyError."""
    glossary = MASTER + [{"preferred": "allowlist", "deprecated": ["whitelist"]}]
    result = drift(text, glossary)
    assert result["state"] == "SUCCESS"
    assert all(v["preferred"] in {"main", "allowlist"} for v in result["violations"])
    if found is not None:
        assert [v["found"] for v in result["violations"]] == [found]


def test_unicode_case_folding_variants_report_the_right_preferred_term():
    glossary = [{"preferred": "main", "deprecated": ["master"]}, {"preferred": "allowlist", "deprecated": ["whitelist"]}]
    assert [(v["found"], v["preferred"]) for v in drift("maſter and WHITELIST", glossary)["violations"]] == [
        ("maſter", "main"), ("WHITELIST", "allowlist")]


def test_non_ascii_glossary_terms_still_match_case_insensitively():
    glossary = [{"preferred": "kaffee", "deprecated": ["café"]}, {"preferred": "wiki", "deprecated": ["Übersicht seite"]}]
    result = drift("Ein CAFÉ und die übersicht   SEITE.", glossary)
    assert [(v["found"], v["preferred"]) for v in result["violations"]] == [("CAFÉ", "kaffee"), ("übersicht   SEITE", "wiki")]


def test_no_code_point_inserted_into_a_matching_term_can_fail_the_lint():
    """Fuzz over code points 0..0x2FFF at every position of the terms: the lint must always answer SUCCESS."""
    glossary = MASTER + [{"preferred": "kaffee", "deprecated": ["café"]}]
    terms = ["master", "white list", "café"]
    for cp in range(0x3000):
        ch = chr(cp)
        if "\ud800" <= ch <= "\udfff":
            continue
        for term in terms:
            for i in range(len(term) + 1):
                for text in (term[:i] + ch + term[i:], term[:i] + ch + term[i + 1:]):
                    result = drift(f"x {text} y", glossary)
                    assert result["state"] == "SUCCESS", (hex(cp), text, result.get("message"))
                    assert all(v["preferred"] in {"main", "kaffee"} for v in result["violations"]), (hex(cp), text)


def test_a_fence_inside_a_blockquote_is_code_and_ends_with_the_quote():
    """RV-H P3-3: `> ```md` ... `> ```` was prose, so the playbook inside it was reported."""
    assert drift("> ```md\n> playbook\n> ```\nreal playbook\n")["violations"][0]["line"] == 4
    assert [v["line"] for v in drift("> ```md\n> playbook\n> ```\n")["violations"]] == []
    assert [v["line"] for v in drift("> ```\n> playbook\n\nplaybook\n")["violations"]] == [4]  # the blank line ended the quote
    assert [v["line"] for v in drift("> > ~~~\n> > playbook\n> > ~~~\n> playbook\n")["violations"]] == [4]
    assert [v["line"] for v in drift("```\n> ```\nplaybook\n```\nplaybook\n")["violations"]] == [5]  # quoted fence line is content


# ---------------------------------------------------------------- registry shape

@pytest.fixture(scope="module")
def merged() -> dict:
    """The loaded registry with this fragment's entries added when the merge has not happened yet."""
    document = copy.deepcopy(load_document())
    agent = next(a for a in document["agents"] if a["id"] == "A15")
    present = {t["name"] for t in agent["registered_tools"]}
    for entry in json.loads(FRAGMENT.read_text(encoding="utf-8")) if FRAGMENT.exists() else []:
        if entry["name"] not in present:
            agent["registered_tools"].append(copy.deepcopy(entry))
    return document


def test_registry_with_these_entries_has_no_new_errors(merged, tmp_path):
    baseline = validate_registry()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(merged), encoding="utf-8")
    errors = validate_registry(path)
    assert [e for e in errors if e not in baseline] == []
    assert not [e for e in errors if any(name in e for name in NEW_TOOLS)]


def test_a15_stays_inside_the_tool_policy(merged):
    agent = next(a for a in merged["agents"] if a["id"] == "A15")
    names = [t["name"] for t in agent["registered_tools"]]
    assert set(NEW_TOOLS) <= set(names)
    assert len(names) == len(set(names))
    assert merged["capability_policy"]["tools"]["min"] <= len(names) <= merged["capability_policy"]["tools"]["max"]


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_contract_examples_behave_as_declared(merged, name):
    tool = next(t for t in next(a for a in merged["agents"] if a["id"] == "A15")["registered_tools"] if t["name"] == name)
    assert tool["permitted_callers"] == ["A15"] and tool["side_effect_tier"] == "READ"
    ex = tool["examples"]
    assert call_tool(name, "A15", ex["success"], data=merged)["state"] == "SUCCESS"
    assert call_tool(name, "A15", ex["invalid_input"], data=merged)["state"] == "INVALID_INPUT"
    assert call_tool(name, ex["unauthorized_caller"], ex["success"], data=merged)["state"] == "UNAUTHORIZED"
    assert call_tool(name, "A15", ex["dependency"], data=merged)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call_tool(name, "A15", ex["partial"], data=merged)["state"] == "PARTIAL_SUCCESS"
    assert call_tool(name, "A15", ex["timeout"], data=merged, timeout_s=0.05)["state"] == "TIMEOUT"


def test_oversized_and_empty_inputs_are_refused_by_the_schema(merged):
    assert call_tool("docs_runbook_schema_check", "A15", {"runbooks": []}, data=merged)["state"] == "INVALID_INPUT"
    assert call_tool("docs_digest_staleness", "A15", {"docs": [], "current": []}, data=merged)["state"] == "INVALID_INPUT"
    assert call_tool("docs_terminology_drift", "A15", {"glossary": [], "files": [{"path": "a", "text": ""}]}, data=merged)["state"] == "INVALID_INPUT"
    big = {"glossary": GLOSSARY, "files": [{"path": "a.md", "text": "x" * 200001}]}
    assert call_tool("docs_terminology_drift", "A15", big, data=merged)["state"] == "INVALID_INPUT"
