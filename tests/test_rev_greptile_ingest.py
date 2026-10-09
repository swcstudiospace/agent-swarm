"""Greptile review threads -> advisory per-target findings (scripts/rev_greptile_ingest.py).

tests/fixtures/greptile/threads.json is built from the real review threads of PR #19 (a read-only GraphQL capture:
real badge markup, bold titles, Knowledge Base lines and details blocks) with resolved/outdated flags flipped and a few
synthetic threads added (P0, P3, no badge, no path, a non-Greptile author). The expectations below are written out
literally; they are not recomputed through the code under test.

Parity with the TypeScript twin lives here, as it does for orch_from_graph, so a parallel unit can edit
tests/test_ts_scripts.py without a merge conflict.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from swarm.verdicts import load_per_target

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "greptile"
THREADS = FIXTURES / "threads.json"
THREADS_LIST = FIXTURES / "threads_list.json"
BAD_SHAPE = FIXTURES / "bad_shape.json"
SCRIPT = ROOT / "scripts" / "rev_greptile_ingest.py"
TWIN = ROOT / "scripts" / "ts" / "rev_greptile_ingest.ts"
REV_GATE = ROOT / "scripts" / "rev_gate.py"
BUN = shutil.which("bun") or str(Path.home() / ".bun" / "bin" / "bun")
HAS_BUN = Path(BUN).exists()

# (id, severity, location, summary) in thread order, after the drops below
EXPECTED = [
    ("GP-001", "major", "scripts/build_agents.py:508", "Installed skills use wrong paths"),
    ("GP-002", "major", "scripts/_install_grokbot.py:116", "Missing exports delete working skills"),
    ("GP-003", "minor", "scripts/build_agents.py:442", "Runner routes to wrong seats"),
    ("GP-004", "blocker", "swarm/taskstore.py:212", "Manual APPROVED skips the issuer check"),
    ("GP-005", "major", "README.md:12", "Consider documenting why this flag defaults to off."),
    ("GP-006", "minor", "scripts/build_agents.py:948", "Selective builds include unrelated lanes"),
    ("GP-007", "info", "scripts/swarm_run.py:40", "Prefer a named constant"),
    ("GP-008", "minor", None, "Cross-file naming drift"),
]
COUNTS = {
    "threads": 11,
    "findings": 8,
    "by_severity": {"info": 1, "minor": 3, "major": 3, "critical": 0, "blocker": 1},
    "dropped": {"resolved": 1, "outdated": 1, "not_greptile": 1},
}


def _env(tmp_path: Path, **extra) -> dict:
    env = os.environ.copy()
    env["SWARM_DIR"] = str(tmp_path / "events")
    for key in list(env):
        if key.startswith("SUBSTRATE"):
            env.pop(key)
    env.update(extra)
    return env


def _run(tmp_path: Path, *args: str, script: Path = SCRIPT, cwd: Path = ROOT, env: dict | None = None):
    return subprocess.run([sys.executable, str(script), *args, "--json"], cwd=cwd, capture_output=True, text=True,
                          env=env or _env(tmp_path))


def _ok(proc) -> dict:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "ok"
    return data


def _input_error(proc) -> None:
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["error"]["code"] == "E-INPUT", proc.stdout


def _rows(findings: list[dict]) -> list[tuple]:
    return [(f["id"], f["severity"], f["location"], f["summary"]) for f in findings]


def _node(tid: str, body: str, *, login: str | None = "greptile-apps", resolved: bool = False, outdated: bool = False,
          path: str | None = "a.py", line: int | None = 1, orig: int | None = None) -> dict:
    author = {"login": login} if login else None
    return {"id": tid, "isResolved": resolved, "isOutdated": outdated, "path": path, "line": line, "originalLine": orig,
            "comments": {"nodes": [{"databaseId": 1, "author": author, "body": body, "createdAt": "2026-10-09T00:00:00Z"}]}}


def _badged(n: int | str, title: str = "A title", rest: str = "Some explanation.") -> str:
    return (f'<a href="#"><img alt="P{n}" src="https://greptile-static-assets.s3.amazonaws.com/badges/p{n}.svg?v=9" '
            f'align="top"></a> **{title}**\n\n{rest}')


def _write(tmp_path: Path, nodes, name: str = "threads.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(nodes), encoding="utf-8")
    return path


def _one(tmp_path: Path, body: str, **node) -> dict:
    """The single finding of one Greptile thread with this body."""
    data = _ok(_run(tmp_path, "--threads", str(_write(tmp_path, [_node("T1", body, **node)])), "--target", "T-be"))
    (finding,) = data["per_target_findings"]["T-be"]
    return finding


def _fake_gh(tmp_path: Path, *, rc: int = 0, stdout: str | None = None, stderr: str = "") -> tuple[Path, Path]:
    """A `gh` that records its argv and prints a canned reply; returns (bin dir, call log)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "gh_calls.jsonl"
    payload = THREADS.read_text(encoding="utf-8") if stdout is None else stdout
    gh = bindir / "gh"
    gh.write_text(f"#!{sys.executable}\nimport json, sys\n"
                  f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                  f"sys.stderr.write({stderr!r})\nsys.stdout.write({payload!r})\nsys.exit({rc})\n", encoding="utf-8")
    gh.chmod(0o755)
    return bindir, log


# ---------------------------------------------------------------------------------------------------------------------
# what is read, kept and dropped


def test_fixture_findings_and_counts(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be"))
    assert data["agent"] == "A09" and data["script"] == "rev_greptile_ingest"
    assert data["advisory"] is True and data["source"] == "file"
    assert list(data["per_target_findings"]) == ["T-be"]
    assert _rows(data["per_target_findings"]["T-be"]) == EXPECTED
    assert data["counts"] == COUNTS


def test_finding_shape_is_the_review_gate_shape(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be"))
    for f in data["per_target_findings"]["T-be"]:
        assert set(f) == {"id", "severity", "kind", "summary", "evidence", "ac_ref", "owner_suggestion", "location"}
        assert f["kind"] == "greptile" and f["owner_suggestion"] == "A05" and f["ac_ref"] is None
        assert 0 < len(f["summary"]) <= 200 and len(f["evidence"]) <= 1000
        assert "**" not in f["summary"] and "<" not in f["summary"]
        for leftover in ("<details", "</details", "Knowledge Base Used", "Source Used", "<img", "<a ", "<picture", "<source",
                         "](", "Prompt To Fix", "app.greptile.com"):
            assert leftover not in f["evidence"], (f["id"], leftover)
    first = data["per_target_findings"]["T-be"][0]
    assert "SWARM_ROOT" in first["evidence"] and not first["evidence"].startswith("**")


def test_unbadged_greptile_comment_fails_closed_as_major(tmp_path):
    f = _one(tmp_path, "Consider a constant here.")
    assert f["severity"] == "major" and f["summary"] == "Consider a constant here."
    assert f["evidence"].startswith("no severity badge; treated as major")


@pytest.mark.parametrize("priority,severity", [("0", "blocker"), ("1", "major"), ("2", "minor"), ("3", "info"), ("7", "info")])
def test_badge_to_severity(tmp_path, priority, severity):
    f = _one(tmp_path, _badged(priority))
    assert f["severity"] == severity
    assert not f["evidence"].startswith("no severity badge")  # P1 and "no badge" are both major: tell them apart


@pytest.mark.parametrize("alt", ['alt="p2"', "alt='P2'", 'ALT = "P2"'])
def test_badge_match_is_case_insensitive_and_quote_agnostic(tmp_path, alt):
    f = _one(tmp_path, _badged(2).replace('alt="P2"', alt))
    assert f["severity"] == "minor" and not f["evidence"].startswith("no severity badge")


def test_a_badge_quoted_later_does_not_count(tmp_path):
    body = 'Plain comment first.\n\n<a href="#"><img alt="P2" src="https://x/p2.svg"></a> quoted badge'
    assert _one(tmp_path, body)["severity"] == "major"


def test_resolved_outdated_and_non_greptile_are_dropped_in_that_order(tmp_path):
    nodes = [_node("R", _badged(1), resolved=True, outdated=True), _node("O", _badged(1), outdated=True),
             _node("H", _badged(1), login="someone"), _node("N", _badged(1), login=None), _node("K", _badged(2))]
    data = _ok(_run(tmp_path, "--threads", str(_write(tmp_path, nodes)), "--target", "T-be"))
    assert data["counts"]["dropped"] == {"resolved": 1, "outdated": 1, "not_greptile": 2}
    assert [f["id"] for f in data["per_target_findings"]["T-be"]] == ["GP-001"]


def test_only_the_first_comment_counts_and_replies_never_change_it(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be"))
    reply_thread = [f for f in data["per_target_findings"]["T-be"] if f["location"] == "scripts/build_agents.py:948"]
    assert [f["summary"] for f in reply_thread] == ["Selective builds include unrelated lanes"]


def test_author_option_replaces_the_default_logins(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--author", "cursor"))
    assert data["counts"]["dropped"] == {"resolved": 1, "outdated": 1, "not_greptile": 8}
    (f,) = data["per_target_findings"]["T-be"]
    assert f["location"] == "tests/test_grokbot_export.py:236" and f["severity"] == "major"


def test_bot_suffix_login_is_accepted_by_default(tmp_path):
    f = _one(tmp_path, _badged(1), login="greptile-apps[bot]")
    assert f["severity"] == "major"


def test_cleaning_keeps_angle_placeholders_and_drops_hostile_characters(tmp_path):
    rest = "Run `python3 scripts/<script>.py --json` \u202egnp.exe\u202c\u200b hidden\x07 [link text](https://e.test/x)\n\n\n\nend"
    f = _one(tmp_path, _badged(2, "Placeholder <seat-prefix> kept", rest))
    assert "scripts/<script>.py" in f["evidence"] and "<seat-prefix>" in f["summary"]
    for bad in ("\u202e", "\u202c", "\u200b", "\x07", "https://e.test/x", "\n\n\n"):
        assert bad not in f["evidence"], repr(bad)
    assert "link text" in f["evidence"]


def test_real_world_trailer_markup_is_removed(tmp_path):
    rest = ("Body text.\n\n**Source Used:** Linear \u2014 [\\[n6\\] B5: seat map](https://linear.app/x)\n\n"
            '<a href="https://app.greptile.com/ide/claude-code?prompt=a%20b%0Ac"><picture>'
            '<source media="(prefers-color-scheme: dark)" srcset="https://x/d.svg">'
            '<img alt="Fix in Claude Code" src="https://x/l.svg"></picture></a>')
    assert _one(tmp_path, _badged(1, "T", rest))["evidence"] == "Body text."


def test_link_text_with_escaped_brackets_is_flattened(tmp_path):
    f = _one(tmp_path, _badged(2, "T", "See [\\[n6\\] seat map](https://e.test/x) now."))
    assert f["evidence"] == "See [n6] seat map now."


def test_markdown_code_is_never_cleaned(tmp_path):
    """Greptile quotes code in its explanations. Markup removal must not touch inline code or fenced blocks, and a
    backticked <details> must not swallow the rest of the comment."""
    rest = ('Use `<a href="/settings">` and `<details>` carefully.\n\n```html\n<details>\n<b>x</b> [a](b)\n```\n\n'
            "~~~\n<img alt='P1'>\n~~~\n\nDone, see [docs](https://e.test/d).")
    f = _one(tmp_path, _badged(2, "T", rest))
    assert f["evidence"] == ('Use `<a href="/settings">` and `<details>` carefully.\n\n```html\n<details>\n<b>x</b> [a](b)\n```\n\n'
                             "~~~\n<img alt='P1'>\n~~~\n\nDone, see docs.")


def test_double_backtick_span_and_unterminated_fence_are_kept(tmp_path):
    f = _one(tmp_path, _badged(2, "T", "Write ``a ` <b>`` here.\n\n```py\nprint('<b>')"))
    assert f["evidence"] == "Write ``a ` <b>`` here.\n\n```py\nprint('<b>')"


def test_a_long_link_keeps_its_label_and_the_following_explanation(tmp_path):
    """A URL cap left the tail of a long artifact link in the evidence and cut the explanation off."""
    url = "https://artifacts.example/" + ("a" * 4000)
    rest = f"See [the artifact]({url}) and then the explanation that must survive."
    evidence = _one(tmp_path, _badged(2, "T", rest))["evidence"]
    assert evidence == "See the artifact and then the explanation that must survive."
    assert "https://" not in evidence


def test_a_nested_label_and_a_parenthesis_in_the_url_keep_the_explanation(tmp_path):
    """Stopping at the first ] and the first ) left the address in the evidence and hid the explanation."""
    rest = "[the [build] report](https://example/report_(1)) then the explanation"
    evidence = _one(tmp_path, _badged(2, "T", rest))["evidence"]
    assert evidence == "the [build] report then the explanation"
    assert "the [build] report" in evidence and "then the explanation" in evidence
    assert "report_(1)" not in evidence and "https://" not in evidence


def test_a_url_longer_than_2000_characters_is_still_removed(tmp_path):
    """A 2,000-character URL cap left the tail of the address in the evidence and cut off the explanation."""
    url = "https://artifacts.example/" + ("b" * 2001)
    assert len(url) > 2000
    rest = f"See [the artifact]({url}) and then the explanation that must survive."
    evidence = _one(tmp_path, _badged(2, "T", rest))["evidence"]
    assert evidence == "See the artifact and then the explanation that must survive."
    assert "the artifact" in evidence and "and then the explanation that must survive." in evidence
    assert "https://" not in evidence


def test_an_escaped_bracket_does_not_end_the_label(tmp_path):
    """An escaped \\] is literal. Ending the label there would leave the URL in the evidence."""
    rest = "See [pre\\] post](https://example.com/secret) then the explanation"
    evidence = _one(tmp_path, _badged(2, "T", rest))["evidence"]
    assert evidence == "See pre] post then the explanation"
    assert "https://" not in evidence and "secret" not in evidence


def test_an_escaped_parenthesis_does_not_end_the_url(tmp_path):
    """An escaped \\) is literal. Ending the URL there would leave the tail of the address in the evidence."""
    rest = "See [the report](https://example.com/a\\)b) then the explanation"
    evidence = _one(tmp_path, _badged(2, "T", rest))["evidence"]
    assert evidence == "See the report then the explanation"
    assert "https://" not in evidence and "a\\)b" not in evidence and "b)" not in evidence


def test_a_backtick_on_the_opening_line_is_inline_code_not_a_fence(tmp_path):
    """```a ` <b>``` is one inline span. Reading it as a fence kept the prompt block that follows."""
    rest = ("Before ```a ` <b>``` after.\n\n"
            "<details><summary>Prompt To Fix With AI</summary>\nhidden prompt\n</details>\n\n"
            "Kept.\n\n~~~`lang`\n<img alt='P1'>\n~~~")
    evidence = _one(tmp_path, _badged(2, "T", rest))["evidence"]
    assert evidence == "Before ```a ` <b>``` after.\n\nKept.\n\n~~~`lang`\n<img alt='P1'>\n~~~"
    assert "hidden prompt" not in evidence


def test_a_fence_inside_the_prompt_block_is_removed_with_it(tmp_path):
    rest = "Visible.\n\n<details><summary>Prompt To Fix With AI</summary>\n\n`````markdown\nhidden <b>\n`````\n</details>\n\nAfter."
    assert _one(tmp_path, _badged(2, "T", rest))["evidence"] == "Visible.\n\nAfter."


def test_pathological_markup_stays_fast():
    """A body that is mostly brackets made the link pattern quadratic (10,000 of them took 2 s)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("rev_greptile_ingest_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for body in ("[" * 30000 + "a" + "]" * 10, "`" * 30000, "<details>" * 5000, "```\n" * 8000, "[a](" * 8000):
        start = time.perf_counter()
        mod._clean(body)
        assert time.perf_counter() - start < 2, body[:12]


def test_unterminated_details_block_is_dropped_to_the_end(tmp_path):
    f = _one(tmp_path, _badged(2, "T", "Visible.\n\n<details><summary>Prompt</summary>\n\nsecret-looking tail without a close"))
    assert f["evidence"] == "Visible."


def test_long_title_and_body_are_clipped_with_an_ellipsis(tmp_path):
    f = _one(tmp_path, _badged(2, "t" * 500, "e" * 5000))
    assert len(f["summary"]) == 200 and f["summary"].endswith("\u2026")
    assert len(f["evidence"]) == 1000 and f["evidence"].endswith("\u2026")


def test_location_variants(tmp_path):
    assert _one(tmp_path, _badged(2), path="a.py", line=7, orig=3)["location"] == "a.py:7"
    assert _one(tmp_path, _badged(2), path="a.py", line=None, orig=3)["location"] == "a.py:3"
    assert _one(tmp_path, _badged(2), path="a.py", line=None, orig=None)["location"] == "a.py"
    assert _one(tmp_path, _badged(2), path=None, line=None, orig=None)["location"] is None


def test_bare_list_input_equals_graphql_response_input(tmp_path):
    full = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be"))
    bare = _ok(_run(tmp_path, "--threads", str(THREADS_LIST), "--target", "T-be"))
    assert bare["per_target_findings"] == full["per_target_findings"] and bare["counts"] == full["counts"]


# ---------------------------------------------------------------------------------------------------------------------
# targets and --map


def _ids(data: dict) -> dict[str, list[str]]:
    return {t: [f["id"] for f in fs] for t, fs in data["per_target_findings"].items()}


def test_without_map_every_target_gets_every_finding(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--target", "T-fe"))
    every = [row[0] for row in EXPECTED]
    assert _ids(data) == {"T-be": every, "T-fe": every}


def test_map_attributes_by_glob_and_unmatched_goes_to_every_target(tmp_path):
    mapping = tmp_path / "map.json"
    mapping.write_text(json.dumps({"T-be": ["swarm/*"], "T-fe": ["README.md"]}), encoding="utf-8")
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--target", "T-fe", "--map", str(mapping)))
    # GP-004 swarm/taskstore.py -> T-be only; GP-005 README.md -> T-fe only; the rest match no glob (scripts/*, no path)
    # and go to both targets
    assert _ids(data) == {"T-be": ["GP-001", "GP-002", "GP-003", "GP-004", "GP-006", "GP-007", "GP-008"],
                          "T-fe": ["GP-001", "GP-002", "GP-003", "GP-005", "GP-006", "GP-007", "GP-008"]}


def test_duplicate_targets_are_listed_once(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--target", "T-be"))
    assert list(data["per_target_findings"]) == ["T-be"] and len(data["per_target_findings"]["T-be"]) == 8


# ---------------------------------------------------------------------------------------------------------------------
# output file, and the review gate that consumes it


def test_out_is_written_atomically_and_loads_as_per_target_findings(tmp_path):
    out = tmp_path / "sub" / "findings.json"
    data = _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--target", "T-fe", "--out", str(out)))
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n") and json.loads(text) == data["per_target_findings"]
    assert sorted(p.name for p in out.parent.iterdir()) == ["findings.json"]
    loaded = load_per_target(str(out), 0)
    assert set(loaded) == {"T-be", "T-fe"} and [f["id"] for f in loaded["T-be"]] == [r[0] for r in EXPECTED]


def test_no_out_means_no_findings_file(tmp_path):
    _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be"))
    assert not list(tmp_path.glob("**/findings*.json"))


def _rev_gate(tmp_path: Path, findings: Path):
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(REV_GATE), "--root", str(root), "--per-target-findings", str(findings),
                           "--json"], cwd=ROOT, capture_output=True, text=True, env=_env(tmp_path))
    return proc.returncode, json.loads(proc.stdout)


def test_rev_gate_fails_on_major_and_passes_on_minor_only(tmp_path):
    out = tmp_path / "all.json"
    _ok(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--out", str(out)))
    rc, gate = _rev_gate(tmp_path, out)
    assert rc == 1 and gate["verdict"] == "fail"

    minor_only = _write(tmp_path, [_node("M", _badged(2, "Small thing")), _node("I", _badged(3, "Tiny thing"))], "minor.json")
    out2 = tmp_path / "minor_out.json"
    _ok(_run(tmp_path, "--threads", str(minor_only), "--target", "T-be", "--out", str(out2)))
    rc, gate = _rev_gate(tmp_path, out2)
    assert rc == 0 and gate["verdict"] == "pass"


# ---------------------------------------------------------------------------------------------------------------------
# malformed input


def _bad_text(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("text", [
    "not json at all",
    "42",
    json.dumps([1, 2]),
    json.dumps({"data": {"repository": {"pullRequest": None}}}),
    json.dumps([{"id": "x", "comments": "nope"}]),
    json.dumps([{"id": "x", "comments": {"nodes": [{"body": 5, "author": {"login": "greptile-apps"}}]}}]),
    json.dumps([{"id": "x", "isResolved": "yes", "comments": {"nodes": []}}]),
    json.dumps([{"id": "x", "isOutdated": 1, "comments": {"nodes": []}}]),
    json.dumps([{"id": "x", "path": 7, "comments": {"nodes": []}}]),
    json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": True}, "nodes": []}}}}}),
])
def test_malformed_threads_are_e_input(tmp_path, text):
    _input_error(_run(tmp_path, "--threads", str(_bad_text(tmp_path, text)), "--target", "T-be"))


_NODE = {"id": "x", "isResolved": False, "isOutdated": False, "path": "a.py", "line": 1, "originalLine": 1,
         "comments": {"nodes": [{"author": None, "body": "lost author lookup"}]}}


def _response(**extra) -> str:
    threads = {"pageInfo": {"hasNextPage": False}, "nodes": [_NODE]}
    return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": threads}}}, **extra})


@pytest.mark.parametrize("errors", [[{"message": "Could not resolve to a User"}], "boom", {"message": "x"}])
def test_a_response_with_errors_is_not_a_complete_export(tmp_path, errors):
    """Partial data plus errors (a failed author lookup leaves author null) must not pass as a clean export, or a
    real finding would be dropped as 'not Greptile' and a successful findings file written."""
    _input_error(_run(tmp_path, "--threads", str(_bad_text(tmp_path, _response(errors=errors))), "--target", "T-be"))


def test_an_empty_errors_list_is_fine(tmp_path):
    data = _ok(_run(tmp_path, "--threads", str(_bad_text(tmp_path, _response(errors=[]))), "--target", "T-be"))
    assert data["counts"]["dropped"]["not_greptile"] == 1


def test_pr_mode_rejects_a_response_with_errors(tmp_path):
    bindir, _ = _fake_gh(tmp_path, stdout=_response(errors=[{"message": "Could not resolve to a User"}]))
    env = _env(tmp_path, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _input_error(_run(tmp_path, "--pr", "o/r#1", "--target", "T-be", env=env))


def test_wrong_shape_fixture_and_missing_file_are_e_input(tmp_path):
    _input_error(_run(tmp_path, "--threads", str(BAD_SHAPE), "--target", "T-be"))
    _input_error(_run(tmp_path, "--threads", str(tmp_path / "missing.json"), "--target", "T-be"))


def test_argument_errors_are_e_input(tmp_path):
    good = ["--threads", str(THREADS)]
    _input_error(_run(tmp_path, *good))  # no --target
    _input_error(_run(tmp_path, *good, "--target", "../x"))  # unsafe task id
    _input_error(_run(tmp_path, "--target", "T-be"))  # no source
    _input_error(_run(tmp_path, *good, "--pr", "o/r#1", "--target", "T-be"))  # two sources
    _input_error(_run(tmp_path, "--pr", "not-a-pr", "--target", "T-be"))  # bad --pr
    _input_error(_run(tmp_path, *good, "--target", "T-be", "--author", ""))  # empty author


@pytest.mark.parametrize("text", ["{", "[1]", json.dumps({"T-be": "scripts/*"}), json.dumps({"T-be": [1]}),
                                  json.dumps({"T-other": ["a/*"]})])
def test_bad_map_is_e_input(tmp_path, text):
    _input_error(_run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--map", str(_bad_text(tmp_path, text))))


# ---------------------------------------------------------------------------------------------------------------------
# --pr: one read-only gh call


def test_pr_mode_makes_one_read_only_graphql_call(tmp_path):
    bindir, log = _fake_gh(tmp_path)
    env = _env(tmp_path, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    data = _ok(_run(tmp_path, "--pr", "swcstudiospace/agent-swarm#19", "--target", "T-be", env=env))
    assert data["source"] == "pr" and _rows(data["per_target_findings"]["T-be"]) == EXPECTED
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:2] == ["api", "graphql"]
    assert "-X" not in argv and not any(a.startswith("--method") for a in argv)
    query = next(a for a in argv if a.startswith("query="))
    assert "mutation" not in query.lower() and "reviewThreads" in query
    assert {"owner=swcstudiospace", "repo=agent-swarm", "number=19"} <= set(argv)


def test_pr_mode_without_gh_is_e_dep(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = _run(tmp_path, "--pr", "o/r#1", "--target", "T-be", env=_env(tmp_path, PATH=str(empty)))
    assert proc.returncode == 2 and json.loads(proc.stdout)["error"]["code"] == "E-DEP"


def test_pr_mode_failing_gh_is_e_dep(tmp_path):
    bindir, _ = _fake_gh(tmp_path, rc=3, stdout="", stderr="boom: HTTP 502")
    env = _env(tmp_path, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    proc = _run(tmp_path, "--pr", "o/r#1", "--target", "T-be", env=env)
    assert proc.returncode == 2 and json.loads(proc.stdout)["error"]["code"] == "E-DEP"
    assert "HTTP 502" in proc.stdout


@pytest.mark.parametrize("reply", ["not json", json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
    "pageInfo": {"hasNextPage": True}, "nodes": []}}}}})])
def test_pr_mode_unusable_reply_is_e_input(tmp_path, reply):
    bindir, _ = _fake_gh(tmp_path, stdout=reply)
    env = _env(tmp_path, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    _input_error(_run(tmp_path, "--pr", "o/r#1", "--target", "T-be", env=env))


# ---------------------------------------------------------------------------------------------------------------------
# advisory only


def test_nothing_is_recorded_or_approved(tmp_path):
    out = tmp_path / "findings.json"
    proc = _run(tmp_path, "--threads", str(THREADS), "--target", "T-be", "--out", str(out))
    data = _ok(proc)
    assert not (tmp_path / "events" / "tasks.db").exists()
    assert not {"verdict", "approved", "approved_by"} & set(data)
    for text in (data["summary"], json.dumps(data["counts"]), json.dumps({k: v for k, v in data.items() if k != "per_target_findings"})):
        assert "APPROVED" not in text and "DONE" not in text
    assert set(json.loads(out.read_text(encoding="utf-8"))) == {"T-be"}


def test_dry_run_is_canned_and_writes_no_findings_file(tmp_path):
    out = tmp_path / "findings.json"
    data = _ok(subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--out", str(out), "--json"], cwd=ROOT,
                              capture_output=True, text=True, env=_env(tmp_path)))
    assert data["per_target_findings"] == {} and data["advisory"] is True
    assert not out.exists()


# ---------------------------------------------------------------------------------------------------------------------
# Bun twin


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_twin_matches_python(tmp_path):
    args = ["--threads", str(THREADS), "--target", "T-be", "--target", "T-fe"]
    py = _ok(_run(tmp_path, *args))
    ts = _ok(subprocess.run([BUN, str(TWIN), *args, "--json"], cwd=ROOT, capture_output=True, text=True, env=_env(tmp_path)))
    assert ts["per_target_findings"] == py["per_target_findings"] and ts["counts"] == py["counts"]


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_twin_resolves_relative_paths_against_the_callers_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy2(THREADS, work / "threads.json")
    (work / "map.json").write_text(json.dumps({"T-be": ["swarm/*"]}), encoding="utf-8")
    proc = subprocess.run([BUN, str(TWIN), "--threads", "threads.json", "--map", "map.json", "--target", "T-be",
                           "--out", "out.json", "--json"], cwd=work, capture_output=True, text=True, env=_env(tmp_path))
    data = _ok(proc)
    assert (work / "out.json").is_file() and not (ROOT / "out.json").exists()
    assert json.loads((work / "out.json").read_text(encoding="utf-8")) == data["per_target_findings"]


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_twin_keeps_a_numeric_filename_in_the_callers_directory(tmp_path):
    """`--threads -1` is a file named -1. Skipping every value that starts with `-` made the twin read the checkout."""
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy2(THREADS, work / "-1")
    proc = subprocess.run([BUN, str(TWIN), "--threads", "-1", "--out", "-2", "--target", "T-be", "--json"],
                          cwd=work, capture_output=True, text=True, env=_env(tmp_path))
    data = _ok(proc)
    assert (work / "-2").is_file() and not (ROOT / "-2").exists()
    assert json.loads((work / "-2").read_text(encoding="utf-8")) == data["per_target_findings"]


@pytest.mark.skipif(not HAS_BUN, reason="bun not installed")
def test_twin_absolutize_pins_numeric_paths_and_does_not_add_a_second_root(tmp_path):
    raw = json.dumps(["--threads", "-1", "--out=-2", "--root", "-3", "--json", "--map", "--nope"])
    proc = subprocess.run([BUN, "-e",
                           "import { absolutize } from './scripts/ts/rev_greptile_ingest.ts';"
                           "const args = JSON.parse(process.argv.at(-1) ?? '[]');"
                           "console.log(JSON.stringify(absolutize(args, '/caller')))",
                           raw],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == [
        "--threads", "/caller/-1", "--out=/caller/-2", "--root", "/caller/-3", "--json", "--map", "--nope",
    ]
