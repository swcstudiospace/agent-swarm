"""Behaviour of the eight pre-v2.2 A06 lane tools: fe_a11y_scan, fe_heading_sequence_check, fe_alt_text_check,
fe_form_label_check, fe_tab_order_check, fe_bundle_budget_check, fe_prop_type_coverage, fe_target_size_check.

Every assertion is on output values for specific inputs. Tools are called through the registry as A06 and, for
the pure handlers, directly as functions.
"""
from __future__ import annotations

import importlib

import pytest

CALLER = "A06"


def _call(name: str, payload: dict, caller: str = CALLER) -> dict:
    """conftest evicts swarm* modules between tests, so resolve the registry from the current module graph."""
    return importlib.import_module("swarm.tool_registry").call_tool(name, caller, payload)


def _handler(name: str):
    return getattr(importlib.import_module("swarm.tools.a06_tools"), name)


def _invalid(result: dict, field: str) -> None:
    assert result["state"] == "INVALID_INPUT"
    assert result["field"] == field
    assert result["message"]


def _scan(html: str) -> int:
    result = _call("fe_a11y_scan", {"html": html})
    assert result["state"] == "SUCCESS"
    return result["issue_count"]


# --------------------------------------------------------------------------- fe_a11y_scan


def test_a11y_scan_clean_markup_reports_zero_with_exact_output_shape():
    assert _call("fe_a11y_scan", {"html": "<button>Save</button>"}) == {"state": "SUCCESS", "issue_count": 0}


@pytest.mark.parametrize(
    "html, count",
    [
        ('<img src="a.png">', 1),  # no alt attribute
        ('<img src="a.png" alt="">', 0),  # empty alt marks the image decorative
        ('<img src="a" role="presentation">', 0),
        ('<img src="a" aria-label="Logo">', 0),
        ('<img src="a"><img src="b">', 2),  # one issue per element
        ("<html>", 1),
        ('<html lang="en">', 0),
    ],
)
def test_a11y_scan_images_and_document_language(html, count):
    assert _scan(html) == count


@pytest.mark.parametrize(
    "html, count",
    [
        ("<button></button>", 1),
        ("<button>   </button>", 1),  # whitespace is no accessible name
        ('<button aria-label="Save"></button>', 0),
        ("<button><svg></svg></button>", 0),  # an embedded svg counts as content
        ('<a href="/"></a>', 1),
        ("<a>x</a>", 0),
        ('<a name="top"></a>', 0),  # anchor target, not a link
    ],
)
def test_a11y_scan_buttons_and_links_need_an_accessible_name(html, count):
    assert _scan(html) == count


@pytest.mark.parametrize(
    "html, count",
    [
        ('<input type="text">', 1),
        ('<input type="hidden">', 0),
        ('<input type="submit">', 0),
        ('<input aria-label="Name">', 0),
        ('<input id="n"><label for="n">Name</label>', 0),
        ("<label>Name <input></label>", 0),  # wrapping label
        ('<input type="text" placeholder="Name">', 1),  # placeholder-only is a (moderate) issue
        ("<select></select>", 1),
        ("<textarea></textarea>", 1),
    ],
)
def test_a11y_scan_form_controls_need_a_label(html, count):
    assert _scan(html) == count


@pytest.mark.parametrize(
    "html, count",
    [
        ("<div onClick={go}>x</div>", 1),
        ('<div onClick={go} role="button">x</div>', 1),  # role but no keyboard handler
        ('<div onClick={go} role="button" onKeyDown={k}>x</div>', 0),
        ('<span @click="go">x</span>', 1),
        ("<div>x</div>", 0),
        ('<button tabindex="5">Go</button>', 1),
        ('<div tabindex="3">x</div>', 1),
        ('<button tabindex="0">Go</button>', 0),
        ('<button tabindex="-1">Go</button>', 0),
    ],
)
def test_a11y_scan_click_handlers_and_positive_tabindex(html, count):
    assert _scan(html) == count


def test_a11y_scan_ignores_commented_out_markup_and_plain_text():
    assert _scan('<!-- <img src="a"> --><p>Save</p>') == 0
    assert _scan("Save") == 0


def test_a11y_scan_sums_issues_across_a_document():
    html = '<html><img src="a"><input type="text"><a href="/"></a><div onClick={x}>x</div><button tabindex="3">Go</button>'
    assert _scan(html) == 6  # lang, alt, label, link name, click handler, positive tabindex


def test_a11y_scan_rejects_bad_input_naming_the_field():
    _invalid(_call("fe_a11y_scan", {}), "html")
    _invalid(_call("fe_a11y_scan", {"html": ""}), "html")
    _invalid(_call("fe_a11y_scan", {"html": 5}), "html")
    _invalid(_call("fe_a11y_scan", {"html": "<b>x</b>", "extra": 1}), "extra")


def test_a11y_scan_is_restricted_to_a06():
    result = _call("fe_a11y_scan", {"html": "<b>x</b>"}, caller="A05")
    assert result["state"] == "UNAUTHORIZED" and result["caller"] == "A05"


def test_a11y_scan_arrow_handler_before_aria_label_is_not_a_false_positive():
    assert _scan('<input onChange={(e) => set(e)} aria-label="Name">') == 0


def test_a11y_scan_flags_click_handler_written_as_arrow_function():
    assert _scan("<div onClick={() => go()}>x</div>") == 1


def test_a11y_scan_flags_positive_tabindex_on_any_element():
    assert _scan('<li tabindex="3">x</li>') == 1
    assert _scan('<p tabindex="2">x</p>') == 1
    assert _scan("<section tabIndex={5}>x</section>") == 1
    assert _scan('<li tabindex="0">x</li><p tabindex="-1">y</p>') == 0
    assert _scan('<a tabindex="2">anchor target</a>') == 1  # plain anchors are skipped by the name check, not by tabindex
    assert _scan('<input type="hidden" tabindex="2">') == 1


@pytest.mark.parametrize(
    "html, count",
    [
        ('<input onChange={(e) => set(e > 1)} aria-label="Name">', 0),
        ("<input onChange={(e) => set(e)} placeholder=\"x\">", 1),  # placeholder-only is still moderate
        ('<img onLoad={() => ok()} alt="Logo">', 0),
        ('<img onLoad={() => ok()}>', 1),
        ('<div title="a > b" onClick={() => go()}>x</div>', 1),
        ("<div onClick={() => { if (a > b) { go(); } }} role=\"button\" onKeyDown={() => go()}>x</div>", 0),
        ("<button onClick={() => save()}>Save</button>", 0),
        ("<button onClick={() => save()}></button>", 1),
        ("<span onClick={() => go()}\n>x</span>", 1),
        ("<div onClick={`a>b ${x}`}>x</div>", 1),
    ],
)
def test_a11y_scan_ignores_gt_inside_quotes_and_jsx_expressions(html, count):
    assert _scan(html) == count


def test_a11y_scan_unterminated_constructs_do_not_hang_or_crash():
    for html in ('<div onClick={() => go()', '<a title="x', "<a '", "<img {" * 3000, "<a " + "{" * 200_000):
        assert _scan(html) >= 0


def test_a11y_scan_pathological_200kb_inputs_run_in_linear_time():
    import time

    n = 200_000
    cases = ["<a {" * (n // 4), '<a "' * (n // 4), "<a>" * (n // 3), "<button>" * (n // 8) + "</button>",
             "<img>" * (n // 5), '<p tabindex="' + "9" * n + '">', '<div title="' + ">" * n + '">x</div>', "<" * n]
    for html in cases:
        start = time.perf_counter()
        _call("fe_a11y_scan", {"html": html})
        assert time.perf_counter() - start < 5.0, html[:12]


def _fe():
    return importlib.import_module("swarm.tools.support").script("fe_a11y_check")


def _findings(text: str, rel: str) -> list[tuple[str, str]]:
    return [(i["kind"], i["loc"]) for i in _fe().check_text(text, rel)]


@pytest.mark.parametrize(
    "html, count",
    [
        ('<Toolbar icon={<img src="logo.png" />} />', 1),  # nested <img> without alt (was skipped by the single pass)
        ('<Toolbar icon={<img src="logo.png" alt="Logo" />} />', 0),
        ("<Section action={<input/>} />", 1),
        ("<Menu trigger={<button></button>} />", 1),
        ("<Menu trigger={<button>Open</button>} />", 0),
        ('<A p={<B q={<img src="x" />} />} />', 1),  # two levels deep
        ('<A p={cond ? <img src="a.png" /> : <img src="b.png" alt="b" />} />', 1),
        ('<div role="button" onClick={() => a<b && c} onKeyDown={k}>x</div>', 0),  # `a<b` is not an element
        ('<A p={a<b && c>d} />', 0),
        ('<img onLoad={() => n<3} alt="x">', 0),
    ],
)
def test_a11y_scan_visits_elements_nested_in_jsx_attribute_expressions(html, count):
    assert _scan(html) == count


def test_a11y_iter_tags_yields_nested_elements_in_source_order_and_keeps_outer_attrs():
    tags = list(_fe().iter_tags('<Toolbar icon={<img src="logo.png" />} id="t" /><p>x</p>'))
    assert [t[0] for t in tags] == ["Toolbar", "img", "p"]
    assert 'id="t"' in tags[0][1] and "<img" in tags[0][1]  # enclosing tag still sees its whole attribute text
    assert tags[1][1].strip() == 'src="logo.png" /'


_APOSTROPHE = """<div
// Keep Atlas's footer: it must not swallow the markup below
className="x"
/>
<img src="a.png" />
<button></button>
<p>it's done</p>
"""


def test_a11y_comment_between_jsx_attributes_does_not_swallow_following_markup():
    expected = [("img-alt", "x.tsx:5"), ("name", "x.tsx:6")]
    assert _findings(_APOSTROPHE, "x.tsx") == expected
    assert _findings(_APOSTROPHE, "x.jsx") == [(k, loc.replace("x.tsx", "x.jsx")) for k, loc in expected]
    block = _APOSTROPHE.replace("// Keep Atlas's footer: it must not swallow the markup below",
                                "/* Keep Atlas's footer */")
    assert _findings(block, "x.tsx") == expected


def test_a11y_comments_inside_jsx_expressions_and_lone_apostrophes_in_jsx_text():
    in_expr = '<div className={cn(\n  // can\'t drop this\n  "a",\n  /* it\'s */ "b"\n)} />\n<img src="a.png" />\n'
    assert _findings(in_expr, "x.tsx") == [("img-alt", "x.tsx:6")]
    text = '<Dialog body={<>This can\'t be undone.</>} />\n<img src="a.png" />\n'
    assert _findings(text, "x.tsx") == [("img-alt", "x.tsx:2")]
    jsx_comment = '<Dialog body={<>{/* it\'s fine */}</>} />\n<img src="a.png" />\n'
    assert _findings(jsx_comment, "x.tsx") == [("img-alt", "x.tsx:2")]


def test_a11y_comment_skipping_is_jsx_only_and_needs_whitespace_before_the_comment():
    # In .html a `//` between attributes is not a comment: the old quote rule still applies there.
    html = '<a href=x//y title="t">link</a><img src="a.png">'
    assert _findings(html, "x.html") == [("img-alt", "x.html:1")]
    url = '<a href="https://example.com/a" aria-label="x">y</a><img src="a.png" />'
    assert _findings(url, "x.tsx") == [("img-alt", "x.tsx:1")]


def test_a11y_nested_element_scanning_keeps_linear_time_on_200kb_inputs():
    import time

    n = 200_000
    cases = ["<A p={" * (n // 6), "<A p={<img/>" * (n // 12), "<a p={" + "x<y " * (n // 4), "<a " + "// it's\n" * (n // 8),
             "<a " + "/* ' */ " * (n // 8), "<a p={" + "'\\'" * (n // 3), "<a p={" + "can't\n" * (n // 6),
             "<a p={" + "a<b && c}" * (n // 9), '<A p={<img src="x" />}>' * (n // 23)]
    for text in cases:
        for rel in ("x.tsx", "x.html"):
            start = time.perf_counter()
            _fe().check_text(text, rel)
            assert time.perf_counter() - start < 5.0, text[:14]


# --------------------------------------------------------------------------- fe_heading_sequence_check


def test_heading_sequence_clean_outline():
    assert _call("fe_heading_sequence_check", {"levels": [1, 2, 3, 2]}) == {
        "state": "SUCCESS",
        "heading_count": 4,
        "first_level_ok": True,
        "skip_count": 0,
        "skip_indexes": [],
        "sequence_ok": True,
    }


def test_heading_sequence_reports_each_skipping_index():
    result = _call("fe_heading_sequence_check", {"levels": [1, 3, 4, 6, 2]})
    assert result["skip_indexes"] == [1, 3]  # h1->h3 and h4->h6
    assert result["skip_count"] == 2 and result["sequence_ok"] is False and result["first_level_ok"] is True


def test_heading_sequence_must_start_at_h1():
    result = _call("fe_heading_sequence_check", {"levels": [2, 3]})
    assert result["first_level_ok"] is False and result["skip_count"] == 0 and result["sequence_ok"] is False


def test_heading_sequence_ascending_by_more_than_one_is_not_a_skip_when_descending():
    # going back UP the outline (any distance) is allowed; only deeper-by-more-than-one is a skip
    result = _call("fe_heading_sequence_check", {"levels": [1, 2, 3, 4, 5, 6, 1, 2]})
    assert result["skip_count"] == 0 and result["sequence_ok"] is True
    assert _call("fe_heading_sequence_check", {"levels": [1, 2, 3, 1, 3]})["skip_indexes"] == [4]


def test_heading_sequence_combines_bad_start_and_skips():
    result = _call("fe_heading_sequence_check", {"levels": [2, 4, 3, 5, 6, 1, 3]})
    assert (result["first_level_ok"], result["skip_indexes"], result["sequence_ok"]) == (False, [1, 3, 6], False)
    assert result["heading_count"] == 7


def test_heading_sequence_single_heading_boundaries():
    assert _call("fe_heading_sequence_check", {"levels": [1]})["sequence_ok"] is True
    only_h6 = _call("fe_heading_sequence_check", {"levels": [6]})
    assert only_h6["first_level_ok"] is False and only_h6["skip_count"] == 0


def test_heading_sequence_accepts_the_500_item_maximum():
    result = _call("fe_heading_sequence_check", {"levels": [1] * 500})
    assert result["heading_count"] == 500 and result["sequence_ok"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"levels": []},
        {"levels": [0]},
        {"levels": [7]},
        {"levels": [1, "2"]},
        {"levels": [1.5]},
        {"levels": [1] * 501},
        {"levels": "1,2"},
    ],
)
def test_heading_sequence_rejects_malformed_levels_naming_the_field(payload):
    _invalid(_call("fe_heading_sequence_check", payload), "levels")


def test_heading_sequence_rejects_unknown_field_and_unauthorized_caller():
    _invalid(_call("fe_heading_sequence_check", {"levels": [1], "x": 1}), "x")
    assert _call("fe_heading_sequence_check", {"levels": [1]}, caller="A04")["state"] == "UNAUTHORIZED"


def test_heading_sequence_handler_called_directly_does_not_mutate_the_payload():
    payload = {"levels": [1, 3]}
    result = _handler("fe_heading_sequence_check")(payload)
    assert result["skip_indexes"] == [1] and payload == {"levels": [1, 3]}


# --------------------------------------------------------------------------- fe_alt_text_check


def _img(id_: str, alt: str | None = "", decorative: bool = False) -> dict:
    return {"id": id_, "has_alt": alt is not None, "alt": alt or "", "decorative": decorative}


def test_alt_text_prompt_example_passes():
    result = _call("fe_alt_text_check", {"images": [_img("hero", "Team reviewing a dashboard"), _img("divider", "", True)]})
    assert result == {
        "state": "SUCCESS",
        "image_count": 2,
        "checked_count": 1,
        "missing_count": 0,
        "empty_count": 0,
        "generic_count": 0,
        "decorative_conflict_count": 0,
        "flagged_ids": [],
        "alt_ok": True,
    }


def test_alt_text_missing_empty_generic_and_conflict_are_counted_separately_in_input_order():
    images = [
        _img("a", None),  # missing
        _img("b", "   "),  # blank
        _img("c", "Photo"),  # generic
        _img("d", "banner.PNG"),  # file name
        _img("e", "Quarterly revenue chart"),  # fine
        _img("f", "Decoration", decorative=True),  # decorative with alt
    ]
    result = _call("fe_alt_text_check", {"images": images})
    assert result["image_count"] == 6 and result["checked_count"] == 5
    assert (result["missing_count"], result["empty_count"], result["generic_count"], result["decorative_conflict_count"]) == (1, 1, 2, 1)
    assert result["flagged_ids"] == ["a", "b", "c", "d", "f"]
    assert result["alt_ok"] is False


@pytest.mark.parametrize("alt", ["image", "IMG", "Photo", "picture", "icon", "GRAPHIC", "untitled", "  icon  "])
def test_alt_text_generic_words_are_flagged_case_insensitively_after_trim(alt):
    result = _call("fe_alt_text_check", {"images": [_img("x", alt)]})
    assert result["generic_count"] == 1 and result["flagged_ids"] == ["x"]


@pytest.mark.parametrize("alt", ["logo.png", "a.JPG", "b.jpeg", "c.gif", "d.svg", "e.webp"])
def test_alt_text_trailing_image_suffix_is_generic(alt):
    assert _call("fe_alt_text_check", {"images": [_img("x", alt)]})["generic_count"] == 1


@pytest.mark.parametrize("alt", ["Photo of the team", "image gallery", "icon set", "PNG format explained", "report.pdf"])
def test_alt_text_generic_word_inside_a_longer_description_is_fine(alt):
    result = _call("fe_alt_text_check", {"images": [_img("x", alt)]})
    assert result["generic_count"] == 0 and result["alt_ok"] is True


def test_alt_text_decorative_images_are_never_counted_as_checked_and_blank_alt_is_ok():
    images = [_img("d1", "", True), _img("d2", None, True), _img("d3", "   ", True)]
    result = _call("fe_alt_text_check", {"images": images})
    assert result["checked_count"] == 0 and result["decorative_conflict_count"] == 0 and result["alt_ok"] is True


def test_alt_text_has_alt_false_with_text_is_still_missing_for_non_decorative():
    result = _call("fe_alt_text_check", {"images": [{"id": "x", "has_alt": False, "alt": "Chart", "decorative": False}]})
    assert result["missing_count"] == 1 and result["empty_count"] == 0


def test_alt_text_decorative_with_has_alt_false_but_text_is_not_a_conflict():
    result = _call("fe_alt_text_check", {"images": [{"id": "x", "has_alt": False, "alt": "Chart", "decorative": True}]})
    assert result["decorative_conflict_count"] == 0 and result["flagged_ids"] == []


def test_alt_text_duplicate_ids_are_invalid_input():
    result = _call("fe_alt_text_check", {"images": [_img("a", "Chart"), _img("a", "Other")]})
    _invalid(result, "images")
    assert "'a'" in result["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"images": []},
        {"images": [{"id": "a", "has_alt": True, "alt": "x"}]},  # decorative missing
        {"images": [{"id": "", "has_alt": True, "alt": "x", "decorative": False}]},
        {"images": [{"id": "a", "has_alt": "yes", "alt": "x", "decorative": False}]},
        {"images": [{"id": "a", "has_alt": True, "alt": "x" * 301, "decorative": False}]},
        {"images": [{"id": "a" * 81, "has_alt": True, "alt": "x", "decorative": False}]},
        {"images": [{"id": str(i), "has_alt": True, "alt": "x", "decorative": False} for i in range(301)]},
    ],
)
def test_alt_text_rejects_malformed_input_naming_the_field(payload):
    _invalid(_call("fe_alt_text_check", payload), "images")


def test_alt_text_accepts_boundary_lengths():
    ids = [str(i) for i in range(300)]
    images = [{"id": i, "has_alt": True, "alt": "x" * 300, "decorative": False} for i in ids]
    result = _call("fe_alt_text_check", {"images": images})
    assert result["image_count"] == 300 and result["alt_ok"] is True


# --------------------------------------------------------------------------- fe_form_label_check


def _ctl(id_: str, type_: str = "text", label: bool = False, aria: str = "", labelledby: bool = False) -> dict:
    return {"id": id_, "type": type_, "has_label": label, "aria_label": aria, "aria_labelledby": labelledby}


def test_form_label_all_named_controls_pass():
    controls = [_ctl("email", "email", label=True), _ctl("q", "search", aria="Search"), _ctl("s", "select", labelledby=True)]
    assert _call("fe_form_label_check", {"controls": controls}) == {
        "state": "SUCCESS",
        "control_count": 3,
        "checked_count": 3,
        "hidden_skipped_count": 0,
        "unlabeled_count": 0,
        "unlabeled_ids": [],
        "labels_ok": True,
    }


def test_form_label_lists_unlabeled_ids_in_order_and_skips_hidden():
    controls = [
        _ctl("a"),
        _ctl("tok", "hidden"),  # hidden: never checked
        _ctl("b", label=True),
        _ctl("c", "checkbox"),
        _ctl("d", "textarea", aria="   "),  # blank aria-label is no name
    ]
    result = _call("fe_form_label_check", {"controls": controls})
    assert result["control_count"] == 5 and result["checked_count"] == 4 and result["hidden_skipped_count"] == 1
    assert result["unlabeled_ids"] == ["a", "c", "d"] and result["unlabeled_count"] == 3 and result["labels_ok"] is False


def test_form_label_each_name_source_is_sufficient_alone():
    for ctl in (_ctl("x", label=True), _ctl("x", aria="Name"), _ctl("x", labelledby=True)):
        assert _call("fe_form_label_check", {"controls": [ctl]})["labels_ok"] is True
    assert _call("fe_form_label_check", {"controls": [_ctl("x")]})["unlabeled_ids"] == ["x"]


def test_form_label_only_hidden_controls_means_nothing_checked_and_ok():
    result = _call("fe_form_label_check", {"controls": [_ctl("h1", "hidden"), _ctl("h2", "hidden")]})
    assert result["checked_count"] == 0 and result["hidden_skipped_count"] == 2 and result["labels_ok"] is True


@pytest.mark.parametrize("kind", ["submit", "button", "reset", "file", "radio", "date"])
def test_form_label_non_hidden_types_are_all_checked_including_buttons(kind):
    result = _call("fe_form_label_check", {"controls": [_ctl("x", kind)]})
    assert result["checked_count"] == 1 and result["unlabeled_ids"] == ["x"]


def test_form_label_duplicate_ids_are_invalid_input():
    result = _call("fe_form_label_check", {"controls": [_ctl("x", label=True), _ctl("x")]})
    _invalid(result, "controls")
    assert "'x'" in result["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"controls": []},
        {"controls": [{"id": "x", "type": "color", "has_label": True, "aria_label": "", "aria_labelledby": False}]},
        {"controls": [{"id": "x", "type": "text", "has_label": True, "aria_label": ""}]},
        {"controls": [_ctl("x", aria="a" * 201)]},
        {"controls": [{"id": "x", "type": "text", "has_label": "y", "aria_label": "", "aria_labelledby": False}]},
        {"controls": [_ctl(str(i)) for i in range(301)]},
    ],
)
def test_form_label_rejects_malformed_input_naming_the_field(payload):
    _invalid(_call("fe_form_label_check", payload), "controls")


def test_form_label_unauthorized_caller():
    result = _call("fe_form_label_check", {"controls": [_ctl("x")]}, caller="A08")
    assert result["state"] == "UNAUTHORIZED" and result["target"] == "fe_form_label_check"


# --------------------------------------------------------------------------- fe_tab_order_check


def _el(id_: str, tabindex: int) -> dict:
    return {"id": id_, "tabindex": tabindex}


def test_tab_order_prompt_example():
    result = _call("fe_tab_order_check", {"elements": [_el("nav", 0), _el("modal", -1), _el("save", 0)]})
    assert result == {
        "state": "SUCCESS",
        "element_count": 3,
        "focusable_count": 2,
        "positive_count": 0,
        "positive_ids": [],
        "inversion_count": 0,
        "order_ok": True,
    }


def test_tab_order_counts_pairwise_inversions_among_positive_values_only():
    # positives in DOM order: 3, 1, 2 -> pairs (3,1) and (3,2) are inversions
    elements = [_el("a", 3), _el("zero", 0), _el("b", 1), _el("neg", -1), _el("c", 2)]
    result = _call("fe_tab_order_check", {"elements": elements})
    assert result["positive_ids"] == ["a", "b", "c"] and result["positive_count"] == 3
    assert result["inversion_count"] == 2 and result["order_ok"] is False
    assert result["focusable_count"] == 4  # tabindex >= 0 counts, -1 does not


def test_tab_order_ascending_positive_values_are_not_inversions_but_still_not_ok():
    result = _call("fe_tab_order_check", {"elements": [_el("a", 1), _el("b", 2), _el("c", 3)]})
    assert result["inversion_count"] == 0 and result["positive_count"] == 3 and result["order_ok"] is False


def test_tab_order_equal_positive_values_are_not_inversions():
    assert _call("fe_tab_order_check", {"elements": [_el("a", 2), _el("b", 2)]})["inversion_count"] == 0


def test_tab_order_fully_reversed_sequence_has_n_choose_2_inversions():
    elements = [_el(f"e{i}", n) for i, n in enumerate([5, 4, 3, 2, 1])]
    assert _call("fe_tab_order_check", {"elements": elements})["inversion_count"] == 10


def test_tab_order_tabindex_boundaries():
    result = _call("fe_tab_order_check", {"elements": [_el("lo", -1), _el("hi", 32767)]})
    assert result["focusable_count"] == 1 and result["positive_ids"] == ["hi"]
    _invalid(_call("fe_tab_order_check", {"elements": [_el("x", -2)]}), "elements")
    _invalid(_call("fe_tab_order_check", {"elements": [_el("x", 32768)]}), "elements")


def test_tab_order_all_negative_means_zero_focusable():
    result = _call("fe_tab_order_check", {"elements": [_el("a", -1), _el("b", -1)]})
    assert result["focusable_count"] == 0 and result["order_ok"] is True


def test_tab_order_duplicate_ids_are_invalid_input():
    result = _call("fe_tab_order_check", {"elements": [_el("a", 0), _el("a", 1)]})
    _invalid(result, "elements")
    assert "'a'" in result["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"elements": []},
        {"elements": [{"id": "a"}]},
        {"elements": [{"id": "a", "tabindex": 1.5}]},
        {"elements": [{"id": "", "tabindex": 0}]},
        {"elements": [_el(str(i), 0) for i in range(301)]},
        {"elements": [{"id": "a", "tabindex": "0"}]},
    ],
)
def test_tab_order_rejects_malformed_input_naming_the_field(payload):
    _invalid(_call("fe_tab_order_check", payload), "elements")


def test_tab_order_handler_direct_call_matches_registry_call():
    payload = {"elements": [_el("a", 2), _el("b", 1)]}
    assert _handler("fe_tab_order_check")(payload) == _call("fe_tab_order_check", payload)


# --------------------------------------------------------------------------- fe_bundle_budget_check


def _chunk(name: str, size: int) -> dict:
    return {"name": name, "bytes": size}


def test_bundle_prompt_example_within_budget_and_regression_limit():
    result = _call(
        "fe_bundle_budget_check",
        {"chunks": [_chunk("main", 120000), _chunk("vendor", 310000)], "budget_bytes": 500000, "baseline_bytes": 420000, "regression_limit_pct": 5},
    )
    assert result == {
        "state": "SUCCESS",
        "total_bytes": 430000,
        "headroom_bytes": 70000,
        "utilization_pct": 86.0,
        "over_budget": False,
        "largest_chunk": "vendor",
        "regression_checked": True,
        "regression_pct": 2.38,
        "regression_breach": False,
        "budget_ok": True,
    }


def test_bundle_over_budget_reports_negative_headroom_and_fails():
    result = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 600), _chunk("b", 600)], "budget_bytes": 1000})
    assert result["total_bytes"] == 1200 and result["headroom_bytes"] == -200 and result["utilization_pct"] == 120.0
    assert result["over_budget"] is True and result["budget_ok"] is False
    assert result["regression_checked"] is False and result["regression_pct"] == 0.0 and result["regression_breach"] is False


def test_bundle_exactly_at_budget_is_ok_one_byte_over_is_not():
    at = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1000)], "budget_bytes": 1000})
    assert at["over_budget"] is False and at["headroom_bytes"] == 0 and at["utilization_pct"] == 100.0 and at["budget_ok"] is True
    over = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1001)], "budget_bytes": 1000})
    assert over["over_budget"] is True and over["budget_ok"] is False


def test_bundle_regression_exactly_at_the_limit_is_not_a_breach_just_over_is():
    base = {"chunks": [_chunk("a", 1050)], "budget_bytes": 10000, "baseline_bytes": 1000}
    at = _call("fe_bundle_budget_check", {**base, "regression_limit_pct": 5})
    assert at["regression_pct"] == 5.0 and at["regression_breach"] is False and at["budget_ok"] is True
    over = _call("fe_bundle_budget_check", {**base, "chunks": [_chunk("a", 1051)], "regression_limit_pct": 5})
    assert over["regression_pct"] == 5.1 and over["regression_breach"] is True and over["budget_ok"] is False
    assert over["over_budget"] is False  # regression alone fails the gate


def test_bundle_default_regression_limit_is_five_percent():
    ok = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1050)], "budget_bytes": 5000, "baseline_bytes": 1000})
    bad = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1060)], "budget_bytes": 5000, "baseline_bytes": 1000})
    assert ok["regression_breach"] is False and bad["regression_breach"] is True


def test_bundle_shrinking_below_baseline_gives_negative_regression_and_no_breach():
    result = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 800)], "budget_bytes": 1000, "baseline_bytes": 1000})
    assert result["regression_pct"] == -20.0 and result["regression_breach"] is False and result["regression_checked"] is True


def test_bundle_zero_regression_limit_flags_any_growth():
    grow = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1001)], "budget_bytes": 5000, "baseline_bytes": 1000, "regression_limit_pct": 0})
    same = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1000)], "budget_bytes": 5000, "baseline_bytes": 1000, "regression_limit_pct": 0})
    assert grow["regression_breach"] is True and same["regression_breach"] is False


def test_bundle_regression_limit_is_ignored_without_a_baseline():
    result = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 900)], "budget_bytes": 1000, "regression_limit_pct": 0})
    assert result["regression_checked"] is False and result["regression_breach"] is False and result["budget_ok"] is True


def test_bundle_largest_chunk_is_the_first_of_equal_sizes_and_zero_byte_chunks_are_allowed():
    result = _call("fe_bundle_budget_check", {"chunks": [_chunk("z", 0), _chunk("first", 10), _chunk("second", 10)], "budget_bytes": 100})
    assert result["largest_chunk"] == "first" and result["total_bytes"] == 20
    only_zero = _call("fe_bundle_budget_check", {"chunks": [_chunk("empty", 0)], "budget_bytes": 100})
    assert only_zero["largest_chunk"] == "empty" and only_zero["utilization_pct"] == 0.0 and only_zero["headroom_bytes"] == 100


def test_bundle_duplicate_chunk_names_are_invalid_input():
    result = _call("fe_bundle_budget_check", {"chunks": [_chunk("a", 1), _chunk("a", 2)], "budget_bytes": 10})
    _invalid(result, "chunks")
    assert "'a'" in result["message"]


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"budget_bytes": 10}, "chunks"),
        ({"chunks": [], "budget_bytes": 10}, "chunks"),
        ({"chunks": [_chunk("a", -1)], "budget_bytes": 10}, "chunks"),
        ({"chunks": [_chunk("a", 1.5)], "budget_bytes": 10}, "chunks"),
        ({"chunks": [_chunk("a", 1)]}, "budget_bytes"),
        ({"chunks": [_chunk("a", 1)], "budget_bytes": 0}, "budget_bytes"),
        ({"chunks": [_chunk("a", 1)], "budget_bytes": 10, "baseline_bytes": 0}, "baseline_bytes"),
        ({"chunks": [_chunk("a", 1)], "budget_bytes": 10, "baseline_bytes": 5, "regression_limit_pct": 101}, "regression_limit_pct"),
        ({"chunks": [_chunk("a", 1)], "budget_bytes": 10, "baseline_bytes": 5, "regression_limit_pct": -1}, "regression_limit_pct"),
        ({"chunks": [_chunk(str(i), 1) for i in range(201)], "budget_bytes": 10}, "chunks"),
    ],
)
def test_bundle_rejects_malformed_input_naming_the_field(payload, field):
    _invalid(_call("fe_bundle_budget_check", payload), field)


# --------------------------------------------------------------------------- fe_prop_type_coverage


def _prop(name: str, type_: str) -> dict:
    return {"name": name, "declared_type": type_}


def test_prop_coverage_prompt_example_is_fully_typed():
    components = [
        {"name": "Button", "props": [_prop("variant", "'primary' | 'ghost'"), _prop("onClick", "() => void")]},
        {"name": "Badge", "props": [_prop("label", "string")]},
    ]
    assert _call("fe_prop_type_coverage", {"components": components}) == {
        "state": "SUCCESS",
        "component_count": 2,
        "prop_count": 3,
        "typed_prop_count": 3,
        "coverage_ratio": 1.0,
        "failing_components": [],
        "components_ok": True,
    }


@pytest.mark.parametrize("declared", ["", "   ", "any", " any ", "unknown", "\tunknown\n"])
def test_prop_coverage_blank_any_and_unknown_are_untyped(declared):
    result = _call("fe_prop_type_coverage", {"components": [{"name": "C", "props": [_prop("p", declared), _prop("q", "string")]}]})
    assert result["typed_prop_count"] == 1 and result["coverage_ratio"] == 0.5
    assert result["failing_components"] == ["C"] and result["components_ok"] is False


@pytest.mark.parametrize("declared", ["any[]", "Record<string, any>", "unknownThing", "object", "never", "Any"])
def test_prop_coverage_only_the_exact_untyped_words_count_as_untyped(declared):
    result = _call("fe_prop_type_coverage", {"components": [{"name": "C", "props": [_prop("p", declared)]}]})
    assert result["typed_prop_count"] == 1 and result["components_ok"] is True


def test_prop_coverage_default_min_ratio_requires_every_prop_typed_and_rounds_to_four_places():
    components = [
        {"name": "Good", "props": [_prop("a", "string")]},
        {"name": "Mixed", "props": [_prop("a", "string"), _prop("b", "any"), _prop("c", "number")]},
        {"name": "Bad", "props": [_prop("a", "unknown")]},
    ]
    result = _call("fe_prop_type_coverage", {"components": components})
    assert result["failing_components"] == ["Mixed", "Bad"]
    assert (result["prop_count"], result["typed_prop_count"], result["coverage_ratio"]) == (5, 3, 0.6)
    two_thirds = _call("fe_prop_type_coverage", {"components": [components[1]]})
    assert two_thirds["coverage_ratio"] == 0.6667


def test_prop_coverage_min_ratio_is_inclusive_per_component():
    component = {"name": "C", "props": [_prop("a", "string"), _prop("b", "any")]}  # ratio 0.5
    at = _call("fe_prop_type_coverage", {"components": [component], "min_ratio": 0.5})
    above = _call("fe_prop_type_coverage", {"components": [component], "min_ratio": 0.51})
    zero = _call("fe_prop_type_coverage", {"components": [component], "min_ratio": 0})
    assert at["components_ok"] is True and above["failing_components"] == ["C"] and zero["components_ok"] is True


def test_prop_coverage_component_without_props_counts_as_fully_covered():
    result = _call("fe_prop_type_coverage", {"components": [{"name": "Empty", "props": []}], "min_ratio": 1})
    assert (result["prop_count"], result["typed_prop_count"], result["coverage_ratio"]) == (0, 0, 1.0)
    assert result["failing_components"] == [] and result["components_ok"] is True


def test_prop_coverage_ratio_is_aggregate_over_props_not_mean_of_components():
    components = [
        {"name": "Big", "props": [_prop(f"p{i}", "string") for i in range(9)]},
        {"name": "Tiny", "props": [_prop("x", "any")]},
    ]
    result = _call("fe_prop_type_coverage", {"components": components, "min_ratio": 0.5})
    assert result["coverage_ratio"] == 0.9 and result["failing_components"] == ["Tiny"]


def test_prop_coverage_duplicate_component_or_prop_names_are_invalid_input():
    dup_component = _call("fe_prop_type_coverage", {"components": [{"name": "A", "props": []}, {"name": "A", "props": []}]})
    _invalid(dup_component, "components")
    assert "'A'" in dup_component["message"]
    dup_prop = _call("fe_prop_type_coverage", {"components": [{"name": "A", "props": [_prop("p", "string"), _prop("p", "number")]}]})
    _invalid(dup_prop, "components")
    assert "A" in dup_prop["message"] and "'p'" in dup_prop["message"]


def test_prop_coverage_same_prop_name_in_different_components_is_fine():
    components = [{"name": "A", "props": [_prop("id", "string")]}, {"name": "B", "props": [_prop("id", "string")]}]
    assert _call("fe_prop_type_coverage", {"components": components})["prop_count"] == 2


@pytest.mark.parametrize(
    "payload, field",
    [
        ({}, "components"),
        ({"components": []}, "components"),
        ({"components": [{"name": "A"}]}, "components"),
        ({"components": [{"name": "", "props": []}]}, "components"),
        ({"components": [{"name": "A", "props": [{"name": "p"}]}]}, "components"),
        ({"components": [{"name": "A", "props": [_prop("p", "x" * 201)]}]}, "components"),
        ({"components": [{"name": "A", "props": []}], "min_ratio": 1.1}, "min_ratio"),
        ({"components": [{"name": "A", "props": []}], "min_ratio": -0.1}, "min_ratio"),
    ],
)
def test_prop_coverage_rejects_malformed_input_naming_the_field(payload, field):
    _invalid(_call("fe_prop_type_coverage", payload), field)


# --------------------------------------------------------------------------- fe_target_size_check


def _target(id_: str, w: float, h: float) -> dict:
    return {"id": id_, "width_px": w, "height_px": h}


def test_target_size_prompt_example_flags_the_short_link():
    result = _call("fe_target_size_check", {"targets": [_target("close", 32, 32), _target("inline-link", 60, 18)]})
    assert result == {
        "state": "SUCCESS",
        "target_count": 2,
        "undersized_count": 1,
        "undersized_ids": ["inline-link"],
        "size_ok": False,
    }


def test_target_size_exactly_the_default_minimum_passes_just_under_fails():
    assert _call("fe_target_size_check", {"targets": [_target("a", 24, 24)]})["size_ok"] is True
    for w, h in ((23.9, 24), (24, 23.9), (23, 23)):
        result = _call("fe_target_size_check", {"targets": [_target("a", w, h)]})
        assert result["undersized_ids"] == ["a"] and result["size_ok"] is False


def test_target_size_either_side_failing_is_enough_and_ids_keep_input_order():
    targets = [_target("wide", 100, 10), _target("ok", 44, 44), _target("tall", 10, 100), _target("zero", 0, 0)]
    result = _call("fe_target_size_check", {"targets": targets})
    assert result["undersized_ids"] == ["wide", "tall", "zero"] and result["undersized_count"] == 3 and result["target_count"] == 4


def test_target_size_min_px_override_both_directions():
    targets = [_target("a", 30, 30), _target("b", 44, 44)]
    assert _call("fe_target_size_check", {"targets": targets, "min_px": 44})["undersized_ids"] == ["a"]
    assert _call("fe_target_size_check", {"targets": targets, "min_px": 1})["size_ok"] is True
    assert _call("fe_target_size_check", {"targets": targets, "min_px": 45})["undersized_ids"] == ["a", "b"]


def test_target_size_all_pass_has_empty_id_list():
    result = _call("fe_target_size_check", {"targets": [_target("a", 24, 100), _target("b", 10000, 10000)]})
    assert result["undersized_ids"] == [] and result["undersized_count"] == 0 and result["size_ok"] is True


def test_target_size_duplicate_ids_are_invalid_input():
    result = _call("fe_target_size_check", {"targets": [_target("a", 30, 30), _target("a", 10, 10)]})
    _invalid(result, "targets")
    assert "'a'" in result["message"]


@pytest.mark.parametrize(
    "payload, field",
    [
        ({}, "targets"),
        ({"targets": []}, "targets"),
        ({"targets": [{"id": "a", "width_px": 5}]}, "targets"),
        ({"targets": [_target("a", -1, 5)]}, "targets"),
        ({"targets": [_target("a", 5, 10001)]}, "targets"),
        ({"targets": [_target("a", "5", 5)]}, "targets"),
        ({"targets": [_target(str(i), 30, 30) for i in range(501)]}, "targets"),
        ({"targets": [_target("a", 5, 5)], "min_px": 0}, "min_px"),
        ({"targets": [_target("a", 5, 5)], "min_px": 201}, "min_px"),
        ({"targets": [_target("a", 5, 5)], "min_px": 24.5}, "min_px"),
    ],
)
def test_target_size_rejects_malformed_input_naming_the_field(payload, field):
    _invalid(_call("fe_target_size_check", payload), field)


def test_target_size_unauthorized_caller_does_not_run():
    result = _call("fe_target_size_check", {"targets": [_target("a", 5, 5)]}, caller="A05")
    assert result["state"] == "UNAUTHORIZED" and "undersized_ids" not in result
