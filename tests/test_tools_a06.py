"""Behaviour of the four A06 tools added in v2.2 and the registry contract of their intake fragment."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm.tool_registry import call_tool, load_document, validate_registry

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT = ROOT / "registry" / "intake" / "a06-frontend.tools.json"
NEW_TOOLS = ("fe_token_literal_scan", "fe_state_parity_check", "fe_cwv_budget_check", "fe_client_security_scan")


def _fragment_entries() -> list[dict]:
    """The new entries: from the intake fragment before the merge, from agents.json after it."""
    if FRAGMENT.is_file():
        return json.loads(FRAGMENT.read_text(encoding="utf-8"))
    doc = load_document()
    agent = next(a for a in doc["agents"] if a["id"] == "A06")
    return [t for t in agent["registered_tools"] if t["name"] in NEW_TOOLS]


def _merged_doc() -> dict:
    """A deep copy of the registry with every fragment entry that is not already registered."""
    doc = copy.deepcopy(load_document())
    agent = next(a for a in doc["agents"] if a["id"] == "A06")
    present = {t["name"] for t in agent["registered_tools"]}
    agent["registered_tools"].extend(copy.deepcopy(t) for t in _fragment_entries() if t["name"] not in present)
    return doc


@pytest.fixture(scope="module")
def doc() -> dict:
    return _merged_doc()


@pytest.fixture()
def call(doc):
    def _call(name: str, payload: dict, caller: str = "A06") -> dict:
        return call_tool(name, caller, payload, data=doc)

    return _call


def test_fragment_registers_the_four_tools_in_a_valid_registry(tmp_path):
    merged = _merged_doc()
    path = tmp_path / "agents.json"
    path.write_text(json.dumps(merged), encoding="utf-8")
    assert validate_registry(path) == []
    agent = next(a for a in merged["agents"] if a["id"] == "A06")
    names = [t["name"] for t in agent["registered_tools"]]
    assert set(NEW_TOOLS) <= set(names) and len(names) == len(set(names)) == 12


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_contract_examples_reach_their_declared_states(doc, name):
    tool = next(t for t in _fragment_entries() if t["name"] == name)
    ex = tool["examples"]
    assert tool["permitted_callers"] == ["A06"] and tool["side_effect_tier"] == "READ"
    assert call_tool(name, "A06", ex["success"], data=doc)["state"] == "SUCCESS"
    assert call_tool(name, "A06", ex["invalid_input"], data=doc)["state"] == "INVALID_INPUT"
    assert call_tool(name, ex["unauthorized_caller"], ex["success"], data=doc)["state"] == "UNAUTHORIZED"
    assert call_tool(name, "A06", ex["dependency"], data=doc)["state"] == "DEPENDENCY_UNAVAILABLE"
    assert call_tool(name, "A06", ex["timeout"], data=doc)["state"] == "TIMEOUT"
    assert call_tool(name, "A06", ex["partial"], data=doc)["state"] == "PARTIAL_SUCCESS"


# --------------------------------------------------------------------- fe_token_literal_scan


def _decls(*pairs: tuple[str, str]) -> dict:
    return {"declarations": [{"id": f"d{i}", "property": p, "value": v} for i, (p, v) in enumerate(pairs)]}


def test_token_scan_flags_hex_and_colour_functions(call):
    out = call("fe_token_literal_scan", _decls(
        ("color", "#fff"), ("background", "#0a5fd8cc"), ("border-color", "rgb(0 0 0 / 50%)"),
        ("fill", "hsl(210, 50%, 40%)"), ("outline-color", "oklch(60% 0.1 200)"),
    ))
    assert out["by_kind"] == {"color": 5, "spacing": 0}
    assert out["literal_ids"] == ["d0", "d1", "d2", "d3", "d4"] and out["tokens_ok"] is False


def test_token_scan_flags_nonzero_lengths_but_not_zero_auto_or_percent(call):
    out = call("fe_token_literal_scan", _decls(
        ("margin", "0 auto"), ("padding", "0px"), ("width", "100%"), ("gap", "1.5rem"),
        ("margin-left", "-4px"), ("font-size", "1em"), ("line-height", "1.5"),
    ))
    assert out["literal_ids"] == ["d3", "d4", "d5"]
    assert out["by_kind"] == {"color": 0, "spacing": 3} and out["literal_count"] == 3


def test_token_scan_accepts_token_references_and_calc_of_tokens(call):
    out = call("fe_token_literal_scan", _decls(
        ("color", "var(--color-text)"), ("padding", "calc(var(--space-2) * 2)"),
        ("margin", "theme(spacing.4)"), ("background", "$color-surface"), ("gap", "token(space.3)"),
        ("border-color", "var(--c, #fff)"),
    ))
    assert out["literal_count"] == 0 and out["tokens_ok"] is True and out["declaration_count"] == 6


def test_token_scan_finds_a_literal_beside_a_token_inside_calc(call):
    out = call("fe_token_literal_scan", _decls(("padding", "calc(var(--space-2) + 4px)")))
    assert out["literal_ids"] == ["d0"] and out["by_kind"]["spacing"] == 1


def test_token_scan_mixed_declaration_counts_both_kinds_once_per_id(call):
    out = call("fe_token_literal_scan", _decls(("border", "1px solid #ccc")))
    assert out["literal_ids"] == ["d0"] and out["literal_count"] == 2
    assert out["by_kind"] == {"color": 1, "spacing": 1}


def test_token_scan_ignores_urls_and_quoted_strings(call):
    out = call("fe_token_literal_scan", _decls(
        ("background", "url(img/#fff.png) no-repeat"), ("content", '"12px #fff"'), ("font-family", "'Inter', sans-serif"),
    ))
    assert out["tokens_ok"] is True


def test_token_scan_named_colours_only_count_in_colour_properties(call):
    out = call("fe_token_literal_scan", _decls(
        ("color", "red"), ("background", "transparent"), ("font-family", "red"), ("border", "solid navy"),
    ))
    assert out["literal_ids"] == ["d0", "d3"]


def test_token_scan_custom_reference_patterns(call):
    payload = _decls(("margin", "tok(4px)"))
    assert call("fe_token_literal_scan", payload)["literal_ids"] == ["d0"]
    assert call("fe_token_literal_scan", {**payload, "token_ref_patterns": ["tok("]})["tokens_ok"] is True


def test_token_scan_rejects_bad_input_naming_the_field(call):
    dup = {"declarations": [{"id": "a", "property": "color", "value": "red"}] * 2}
    assert call("fe_token_literal_scan", dup)["field"] == "declarations"
    assert call("fe_token_literal_scan", _decls(("color", "   ")))["field"] == "declarations"
    assert call("fe_token_literal_scan", {"declarations": []})["field"] == "declarations"
    assert call("fe_token_literal_scan", {**_decls(("color", "red")), "token_ref_patterns": [" "]})["field"] == "token_ref_patterns"
    assert call("fe_token_literal_scan", {"declarations": [{"id": "a", "property": "color", "value": "x" * 501}]})["field"] == "declarations"


# --------------------------------------------------------------------- fe_state_parity_check


def _screens(*rows: tuple[str, list[str], list[str]]) -> dict:
    return {"screens": [{"id": i, "spec_states": s, "implemented_states": m} for i, s, m in rows]}


def test_state_parity_exact_match_passes_regardless_of_order(call):
    out = call("fe_state_parity_check", _screens(("SCR-Checkout", ["idle", "loading", "error", "success"], ["success", "error", "loading", "idle"])))
    assert out == {"state": "SUCCESS", "screen_count": 1, "missing": {}, "extra": {}, "parity_ok": True}


def test_state_parity_lists_missing_and_extra_sorted(call):
    out = call("fe_state_parity_check", _screens(
        ("SCR-B", ["idle", "error"], ["idle", "zeta", "alpha"]),
        ("SCR-A", ["empty"], ["empty"]),
        ("SCR-C", ["idle", "busy"], ["idle"]),
    ))
    assert out["missing"] == {"SCR-B": ["error"], "SCR-C": ["busy"]}
    assert out["extra"] == {"SCR-B": ["alpha", "zeta"]}
    assert list(out["missing"]) == ["SCR-B", "SCR-C"] and out["parity_ok"] is False


def test_state_parity_dotted_substates_are_distinct_states(call):
    out = call("fe_state_parity_check", _screens(("form", ["form", "form.error"], ["form", "form.error.network"])))
    assert out["missing"] == {"form": ["form.error"]} and out["extra"] == {"form": ["form.error.network"]}


def test_state_parity_is_case_sensitive(call):
    out = call("fe_state_parity_check", _screens(("s", ["loading"], ["Loading"])))
    assert out["missing"] == {"s": ["loading"]} and out["extra"] == {"s": ["Loading"]}


def test_state_parity_nothing_implemented_means_everything_missing(call):
    out = call("fe_state_parity_check", _screens(("s", ["b", "a"], [])))
    assert out["missing"] == {"s": ["a", "b"]} and out["extra"] == {}


def test_state_parity_rejects_duplicate_ids_repeated_and_blank_states(call):
    assert call("fe_state_parity_check", _screens(("s", ["a"], ["a"]), ("s", ["a"], ["a"])))["field"] == "screens"
    repeated = call("fe_state_parity_check", _screens(("s", ["a"], ["a", "a"])))
    assert repeated["state"] == "INVALID_INPUT" and "implemented_states" in repeated["message"]
    assert call("fe_state_parity_check", _screens(("s", ["a", " "], ["a"])))["field"] == "screens"


def test_state_parity_requires_spec_states_and_at_least_one_screen(call):
    assert call("fe_state_parity_check", _screens(("s", [], ["a"])))["field"] == "screens"
    assert call("fe_state_parity_check", {"screens": []})["field"] == "screens"
    assert call("fe_state_parity_check", {})["field"] == "screens"


# --------------------------------------------------------------------- fe_cwv_budget_check

GOOD = {"cwv": {"lcp_s": 1.9, "inp_ms": 140, "cls": 0.02}, "lighthouse": {"perf": 93, "a11y": 100, "bp": 100, "seo": 95}}


def test_cwv_prompt_example_passes(call):
    out = call("fe_cwv_budget_check", GOOD)
    assert out == {"state": "SUCCESS", "breaches": [], "breach_count": 0, "cwv_ok": True, "lighthouse_ok": True, "perf_ok": True}


def test_cwv_values_equal_to_the_limits_pass(call):
    out = call("fe_cwv_budget_check", {"cwv": {"lcp_s": 2.5, "inp_ms": 200, "cls": 0.1}, "lighthouse": {"perf": 90, "a11y": 100}})
    assert out["perf_ok"] is True and out["breach_count"] == 0


def test_cwv_reports_each_breach_with_value_and_limit_cwv_first(call):
    out = call("fe_cwv_budget_check", {
        "cwv": {"lcp_s": 2.51, "inp_ms": 201, "cls": 0.11}, "lighthouse": {"perf": 89, "a11y": 99},
    })
    assert [b["metric"] for b in out["breaches"]] == ["lcp_s", "inp_ms", "cls", "perf", "a11y"]
    assert out["breaches"][0] == {"metric": "lcp_s", "value": 2.51, "limit": 2.5}
    assert out["breaches"][4] == {"metric": "a11y", "value": 99, "limit": 100}
    assert (out["cwv_ok"], out["lighthouse_ok"], out["perf_ok"]) == (False, False, False)


def test_cwv_lighthouse_breach_alone_keeps_cwv_ok(call):
    out = call("fe_cwv_budget_check", {**GOOD, "lighthouse": {"perf": 93, "a11y": 98}})
    assert (out["cwv_ok"], out["lighthouse_ok"], out["perf_ok"]) == (True, False, False)


def test_cwv_thresholds_override_the_defaults(call):
    payload = {"cwv": {"lcp_s": 3.5, "inp_ms": 140, "cls": 0.02}, "lighthouse": {"perf": 85, "a11y": 100}}
    assert call("fe_cwv_budget_check", payload)["breach_count"] == 2
    relaxed = call("fe_cwv_budget_check", {**payload, "thresholds": {"lcp_s": 4, "perf_min": 80}})
    assert relaxed["perf_ok"] is True


def test_cwv_optional_scores_are_gated_only_when_a_minimum_is_given(call):
    payload = {"cwv": GOOD["cwv"], "lighthouse": {"perf": 93, "a11y": 100, "bp": 70, "seo": 60}}
    assert call("fe_cwv_budget_check", payload)["perf_ok"] is True
    gated = call("fe_cwv_budget_check", {**payload, "thresholds": {"bp_min": 90}})
    assert gated["breaches"] == [{"metric": "bp", "value": 70, "limit": 90}]


def test_cwv_missing_metric_is_invalid_input_naming_the_field(call):
    assert call("fe_cwv_budget_check", {"cwv": {"lcp_s": 1.9, "cls": 0.02}, "lighthouse": GOOD["lighthouse"]})["field"] == "cwv"
    assert call("fe_cwv_budget_check", {"cwv": GOOD["cwv"], "lighthouse": {"perf": 93}})["field"] == "lighthouse"
    assert call("fe_cwv_budget_check", {"cwv": GOOD["cwv"]})["field"] == "lighthouse"


def test_cwv_out_of_range_and_unmeasured_threshold_scores_are_rejected(call):
    assert call("fe_cwv_budget_check", {**GOOD, "lighthouse": {"perf": 101, "a11y": 100}})["field"] == "lighthouse"
    assert call("fe_cwv_budget_check", {**GOOD, "cwv": {"lcp_s": -1, "inp_ms": 1, "cls": 0}})["field"] == "cwv"
    missing_score = call("fe_cwv_budget_check", {**GOOD, "lighthouse": {"perf": 93, "a11y": 100}, "thresholds": {"seo_min": 90}})
    assert missing_score["state"] == "INVALID_INPUT" and missing_score["field"] == "lighthouse"


# --------------------------------------------------------------------- fe_client_security_scan


def _scan(call, text: str, **extra) -> dict:
    return call("fe_client_security_scan", {"files": [{"id": "f.tsx", "text": text}], **extra})


def _kinds(out: dict) -> list[tuple[str, int]]:
    return [(f["kind"], f["line"]) for f in out["findings"]]


def test_security_flags_raw_html_sinks_with_line_numbers(call):
    text = "const a = 1;\n<div dangerouslySetInnerHTML={{ __html: x }} />\nel.innerHTML = y;\n<p v-html=\"z\"></p>\nel.innerHTML === y;"
    out = _scan(call, text)
    assert _kinds(out) == [("dangerous_html", 2), ("inner_html", 3), ("dangerous_html", 4)]
    assert out["by_kind"] == {"dangerous_html": 2, "inner_html": 1} and out["secure_ok"] is False


def test_security_sanitizer_allowlist_covers_the_same_and_next_two_lines_only(call):
    text = "<div dangerouslySetInnerHTML={{\n  __html: DOMPurify.sanitize(x),\n}} />"
    assert _scan(call, text)["by_kind"] == {"dangerous_html": 1}
    assert _scan(call, text, sanitizer_names=["DOMPurify.sanitize"])["secure_ok"] is True
    assert _scan(call, text, sanitizer_names=["sanitizeHtml"])["secure_ok"] is False
    far = "el.innerHTML = x;\n\n\nDOMPurify.sanitize(y)"
    assert _scan(call, far, sanitizer_names=["DOMPurify"])["secure_ok"] is False


def test_security_sanitizer_never_excuses_eval(call):
    out = _scan(call, "eval(DOMPurify.sanitize(x));\nnew Function('a', code);\ndocument.write(x);", sanitizer_names=["DOMPurify"])
    assert _kinds(out) == [("eval", 1), ("eval", 2), ("document_write", 3)]


def test_security_ignores_comments_method_lookalikes_and_reads(call):
    text = "// eval(x)\n/* el.innerHTML = x */\n * document.write(y)\nmodel.eval(x);\nretrieval(y);\nconst h = el.innerHTML;"
    assert _scan(call, text)["secure_ok"] is True


def test_security_inline_scripts_and_external_assets_without_integrity(call):
    text = "\n".join([
        "<script>alert(1)</script>",
        '<script src="/static/app.js"></script>',
        '<script type="application/ld+json">{"a":1}</script>',
        '<script src="https://cdn.example/x.js"></script>',
        '<script src="https://cdn.example/y.js" integrity="sha384-abc" crossorigin="anonymous"></script>',
        '<link rel="stylesheet" href="//cdn.example/s.css">',
        '<link rel="stylesheet" href="/local.css">',
        '<link rel="icon" href="https://cdn.example/i.png">',
    ])
    assert _kinds(_scan(call, text)) == [("inline_script", 1), ("external_no_sri", 4), ("external_no_sri", 6)]


def test_security_multiline_tag_reports_its_opening_line(call):
    text = "<div/>\n<script\n  async\n  src='https://cdn.example/x.js'\n></script>"
    assert _kinds(_scan(call, text)) == [("external_no_sri", 2)]


def test_security_inline_handlers_in_markup_but_not_jsx_props(call):
    out = _scan(call, '<button onclick="go()">x</button>\n<Button onClick={go}>y</Button>\n<a ONMOUSEOVER="z">')
    assert _kinds(out) == [("inline_handler", 1)]


def test_security_plain_js_variables_named_on_something_are_not_inline_handlers(call):
    """RV-D finding 5 repro: `let online = 'yes';` and `const onboarding = "step1";` each gave [('inline_handler', 1)]."""
    out = _scan(call, "let online = 'yes';\nconst onboarding = \"step1\";\nconst a = b<c ? online = 'x' : 1;")
    assert out["findings"] == [] and out["secure_ok"] is True
    markup = _scan(call, "const t = `\n<img\n  src=x\n  onerror=\"steal()\">`;")
    assert _kinds(markup) == [("inline_handler", 4)]
    assert _scan(call, "<!-- <b onclick=\"x\"> -->")["secure_ok"] is True


def test_security_pii_and_credential_keys_in_web_storage(call):
    text = "\n".join([
        "localStorage.setItem('userEmail', e);",
        "sessionStorage['password'] = p;",
        "window.localStorage.accessToken = t;",
        "localStorage.setItem('theme', 'dark');",
        "localStorage.setItem('className', c);",
        "localStorage.setItem('adobeCache', c);",
        "localStorage.setItem(dynamicKey, v);",
        "localStorage.setItem('date_of_birth', v);",
    ])
    assert _kinds(_scan(call, text)) == [("pii_storage", 1), ("pii_storage", 2), ("pii_storage", 3), ("pii_storage", 8)]


def test_security_collapses_repeats_on_one_line_and_orders_by_file(call):
    out = call("fe_client_security_scan", {"files": [
        {"id": "b.tsx", "text": "eval(a); eval(b);"},
        {"id": "a.tsx", "text": "ok\nel.innerHTML = 1; eval(c);"},
    ]})
    assert [(f["id"], f["kind"], f["line"]) for f in out["findings"]] == [
        ("b.tsx", "eval", 1), ("a.tsx", "eval", 2), ("a.tsx", "inner_html", 2),
    ] and out["file_count"] == 2


def test_security_clean_source_and_finding_cap(call):
    assert _scan(call, "export const x = 1;")["secure_ok"] is True
    flood = _scan(call, "\n".join("eval(x);" for _ in range(501)))
    assert len(flood["findings"]) == 500 and flood["findings_truncated"] is True and flood["by_kind"] == {"eval": 501}


def test_security_rejects_bad_input_naming_the_field(call):
    one = {"id": "a", "text": "x"}
    assert call("fe_client_security_scan", {"files": [one, one]})["field"] == "files"
    assert call("fe_client_security_scan", {"files": []})["field"] == "files"
    assert call("fe_client_security_scan", {"files": [{"id": "a", "text": "x" * 100_001}]})["field"] == "files"
    assert call("fe_client_security_scan", {"files": [{"id": str(i), "text": ""} for i in range(51)]})["field"] == "files"
    assert call("fe_client_security_scan", {"files": [one], "sanitizer_names": [" "]})["field"] == "sanitizer_names"
