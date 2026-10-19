#!/usr/bin/env python3
"""A06 — static accessibility lint over HTML / JSX / TSX / Vue / Svelte sources.

Checks (each finding carries file:line):
  * <img> without alt                          (serious)
  * <button> / <a> with no text, aria-label or aria-labelledby   (serious)
  * <input>/<select>/<textarea> without id+<label for>, aria-label, wrapping label or title (serious)
  * <html> without lang                         (serious)
  * positive tabindex                           (moderate)
  * onClick/@click on non-interactive <div>/<span> without role + keyboard handler (moderate)
serious ⇒ major finding (blocks IN_REVIEW per A06 self-gate)
moderate ⇒ minor.
This is a static pre-check
axe-core in component tests remains the authority.
"""
from __future__ import annotations
import re
import sys
from bisect import bisect_left
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402

EXTS = (".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte")
TAG_START = re.compile(r"<([A-Za-z][A-Za-z0-9:._-]*)(?=[\s/>])")
_OUT_STOP = re.compile(r"[\"'{}>]")  # outside braces: attribute quote, JSX expression start, stray '}', end of tag
_OUT_STOP_JSX = re.compile(r"[\"'{}>/]")  # same plus '/' (a `//` or `/* */` comment can sit between JSX attributes)
_IN_STOP = re.compile(r"[\"'`{}<]")  # inside a JSX expression: string, nested brace, possible nested element
_IN_STOP_JSX = re.compile(r"[\"'`{}</]")  # same plus '/' (comments inside the expression)
_STRING = {
    '"': re.compile(r'"(?:[^"\\\n]|\\.)*"', re.S),  # JS quote strings do not span lines (a lone quote is JSX text)
    "'": re.compile(r"'(?:[^'\\\n]|\\.)*'", re.S),
    "`": re.compile(r"`(?:[^`\\]|\\.)*`", re.S),
}
LABEL_FOR = re.compile(r"<label\b[^>]*\bfor\s*=\s*[\"'{]([^\"'}]+)", re.I)
HAS_TEXT = re.compile(r"[A-Za-z0-9]|\{[^}]*\}|<(?:svg|img|i|span|Icon|Trans|FormattedMessage)\b", re.I)


def _scan_tag(text: str, open_m: re.Match, jsx: bool, out: list) -> int:
    """Scan the tag opened by `open_m` (its slot is out[-1]) to its closing '>' and return that index, or -1 when
    it cannot be scanned (unterminated quote, brace or comment). '>' inside quoted attribute values and inside
    {...} JSX expressions (arrow functions, comparisons, strings) does not close the tag. An element that starts
    inside such an expression (`icon={<img src="a.png" />}`) is a tag of its own: the scan of the enclosing tag is
    parked on an explicit stack, the nested element gets its own slot in `out`, and the enclosing scan resumes where
    the nested element ended. Every character is visited once, so the cost stays linear."""
    stack: list[tuple[int, int]] = []  # parked enclosing (slot, brace depth)
    cur, depth, i = len(out) - 1, 0, open_m.end()
    while True:
        m = (_IN_STOP_JSX if jsx else _IN_STOP) if depth else (_OUT_STOP_JSX if jsx else _OUT_STOP)
        m = m.search(text, i)
        if m is None:
            return -1
        ch, k = m.group(0), m.start()
        if ch == ">":
            out[cur][3] = k
            if not stack:
                return k
            cur, depth = stack.pop()
            i = k + 1
        elif ch == "{":
            depth += 1
            i = m.end()
        elif ch == "}":
            if depth:
                depth -= 1
            elif stack:  # `a<b && c}`: the '<b' was a comparison, not an element; discard it, close the brace
                out[cur][3] = -2
                cur, depth = stack.pop()
                depth -= 1
            i = m.end()
        elif ch == "<":
            t = TAG_START.match(text, k)
            if t is None:
                i = m.end()
            else:
                stack.append((cur, depth))
                out.append([t.group(1), t.end(), t.start(), -1])
                cur, depth, i = len(out) - 1, 0, t.end()
        elif ch == "/":  # JSX only: a `//` or `/* */` comment is skipped, so quotes inside it do not count. At depth
            # 0 it must follow whitespace; inside {...} it may also follow an opening bracket or separator (`{/* c */}`)
            nxt = text[k + 1:k + 2]
            prev = text[k - 1:k] if k else ""
            if nxt in ("/", "*") and (prev.isspace() or (depth and prev in ("{", "(", ",", ";", "["))):
                close = text.find("\n" if nxt == "/" else "*/", k + 2)
                if close < 0:
                    return -1
                i = close + (1 if nxt == "/" else 2)
            else:
                i = m.end()
        elif depth:
            q = _STRING[ch].match(text, k)
            if q is not None:
                i = q.end()
            elif ch == "`":
                return -1
            else:  # lone ' or " in JSX text (`can't`) inside an expression: ignore the rest of that line
                eol = text.find("\n", k)
                if eol < 0:
                    return -1
                i = eol + 1
        else:  # plain HTML attribute value: no escape sequences
            close = text.find(ch, m.end())
            if close < 0:
                return -1
            i = close + 1


def iter_tags(text: str, jsx: bool = False):
    """Yield (tag_name, attrs, start, end) for every opening tag in source order, including elements nested inside
    the {...} attribute expressions of another tag (`icon={<img />}`); the enclosing tag's attrs still contain the
    nested text. With jsx=True (.jsx/.tsx) a `//` or `/* */` comment between attributes or inside an expression is
    skipped (limit: a `//` inside a regex literal in an expression is also read as a comment). After a tag that
    cannot be scanned to its end (stray quote or brace) the scanner degrades to the first '>' for the rest of the
    file, which bounds the cost of unterminated constructs to one extra pass instead of one pass per tag."""
    out: list[list] = []  # slots [name, attrs_from, start, '>' index (-1 open, -2 discarded)]
    pos, strict = 0, True
    while True:
        m = TAG_START.search(text, pos)
        if m is None:
            break
        slot = len(out)
        out.append([m.group(1), m.end(), m.start(), -1])
        end = _scan_tag(text, m, jsx, out) if strict else -1
        if end < 0:
            strict = False
            del out[slot + 1:]
            end = text.find(">", m.end())
            if end < 0:
                del out[slot:]
                break
            out[slot][3] = end
        pos = end + 1
    for name, a0, start, a1 in out:
        if a1 >= 0:
            yield name, text[a0:a1], start, a1 + 1


def _attr(attrs: str, name: str) -> str | None:
    n = re.escape(name)
    m = re.search(r"(?:^|\s)" + n + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{([^}]*)\})", attrs, re.I)
    if m:
        return next((g for g in m.groups() if g is not None), "")
    return "" if re.search(r"(?:^|\s)" + n + r"(?=\s|$|/)", attrs, re.I) else None


def _labelled(attrs: str) -> bool:
    return any(_attr(attrs, a) for a in ("aria-label", "aria-labelledby", "title", ":aria-label", "v-bind:aria-label"))


INNER_CAP = 2_000  # first accessible-name search window
INNER_WIDE = 50_000  # wider retry (e.g. long inline <svg> before the label), at most WIDE_RETRIES times per file
WIDE_RETRIES = 200  # bounds total work on runs of unclosed <a>/<button> tags; beyond it the narrow result stands (fail closed)


def _digits(value: str | None) -> str | None:
    """Positive integer text of a tabindex value (no int(): input may be arbitrarily long), else None."""
    v = (value or "").strip().lstrip("+")
    return v if v.isascii() and v.isdigit() and v.strip("0") else None


def check_text(text: str, rel: str) -> list[dict]:
    issues: list[dict] = []
    label_ids = set(LABEL_FOR.findall(text))
    stripped = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), text, flags=re.S)
    newlines = [m.start() for m in re.finditer("\n", stripped)]
    closers: dict[str, list[int]] = {}
    wide_left = [WIDE_RETRIES]

    def add(sev: str, kind: str, msg: str, pos: int):
        issues.append({"sev": sev, "kind": kind, "msg": msg, "loc": f"{rel}:{bisect_left(newlines, pos) + 1}"})

    def has_name_text(tag: str, start: int) -> bool:
        if tag not in closers:
            closers[tag] = [m.start() for m in re.finditer(rf"</{tag}\s*>", stripped, re.I)]
        starts = closers[tag]
        k = bisect_left(starts, start)
        if k >= len(starts):
            return False
        stop = starts[k]
        for cap in (INNER_CAP, INNER_WIDE):
            body = stripped[start:min(stop, start + cap)]
            if HAS_TEXT.search(re.sub(r"<[^>]+>", "", body) or body):
                return True
            if stop - start <= cap:
                return False  # whole element examined
            if cap == INNER_CAP:
                if wide_left[0] <= 0:
                    return False
                wide_left[0] -= 1
        return False

    def check(tag: str, attrs: str, pos: int, end: int) -> None:
        if tag == "html" and _attr(attrs, "lang") is None:
            add("serious", "lang", "<html> missing lang attribute", pos)
        elif tag == "img" and _attr(attrs, "alt") is None and not _labelled(attrs) and _attr(attrs, "role") != "presentation":
            add("serious", "img-alt", "<img> without alt text", pos)
        elif tag in ("button", "a"):
            if tag == "a" and _attr(attrs, "href") is None and not _attr(attrs, "onClick") and not _attr(attrs, "@click"):
                return  # anchor used as a plain container/target
            if not _labelled(attrs) and not has_name_text(tag, end):
                add("serious", "name", f"<{tag}> has no accessible name (text, aria-label or aria-labelledby)", pos)
        elif tag in ("input", "select", "textarea"):
            typ = (_attr(attrs, "type") or "").lower()
            if typ in ("hidden", "submit", "button", "reset", "image"):
                return
            ident = _attr(attrs, "id")
            wrapped = re.search(r"<label\b[^>]*>(?:(?!</label>).)*$", stripped[max(0, pos - 400):pos], re.I | re.S)
            if not (_labelled(attrs) or (ident and ident in label_ids) or wrapped or _attr(attrs, "placeholder")):
                add("serious", "label", f"<{tag}> has no associated label / aria-label", pos)
            elif not (_labelled(attrs) or (ident and ident in label_ids) or wrapped):
                add("moderate", "label", f"<{tag}> relies on placeholder only; add a visible label", pos)
        elif tag in ("div", "span"):
            handler = _attr(attrs, "onClick") or _attr(attrs, "@click") or _attr(attrs, "v-on:click") or _attr(attrs, "on:click")
            if handler is not None and _attr(attrs, "role") is None:
                add("moderate", "interactive", f"click handler on non-interactive <{tag}> without role/tabindex/key handler", pos)
            elif handler is not None and not (_attr(attrs, "onKeyDown") or _attr(attrs, "onKeyUp") or _attr(attrs, "onKeyPress")
                                              or _attr(attrs, "@keydown") or _attr(attrs, "@keyup") or _attr(attrs, "on:keydown")):
                add("moderate", "interactive", f"clickable <{tag} role> lacks keyboard handler", pos)

    for raw_tag, attrs, pos, end in iter_tags(stripped, rel.lower().endswith((".jsx", ".tsx"))):
        tag = raw_tag.lower()
        check(tag, attrs, pos, end)
        # positive tabindex breaks focus order on any element, not only the ones checked above
        ti = _attr(attrs, "tabindex") or _attr(attrs, "tabIndex")
        if _digits(ti):
            add("moderate", "tabindex", f"positive tabindex={ti.strip()} breaks natural focus order", pos)
    return issues


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "files_scanned": 4, "serious": 0, "moderate": 0, "findings": [], "dry_run": True,
                "summary": "dry-run: 4 files scanned, 0 serious / 0 moderate a11y issues"}
    files = [Path(f) for f in args.file] if args.file else list(iter_files(ctx.root, exts=EXTS))
    findings, scanned = [], 0
    for f in files:
        f = f if f.is_absolute() else ctx.root / f
        if not f.exists():
            continue
        try:
            rel = str(f.relative_to(ctx.root))
        except ValueError:
            rel = str(f)
        if any(p in ("coverage", "storybook-static", ".next", "out") for p in Path(rel).parts):
            continue
        scanned += 1
        for it in check_text(f.read_text(errors="ignore"), rel):
            sev = "major" if it["sev"] == "serious" else "minor"
            findings.append(make_finding(f"A11Y-{len(findings)+1:03d}", sev, it["kind"], it["msg"],
                                         location=it["loc"], owner_suggestion="A06"))
    serious = sum(1 for f in findings if f["severity"] == "major")
    moderate = len(findings) - serious
    status = "fail" if serious else "ok"
    return {"status": status, "files_scanned": scanned, "serious": serious, "moderate": moderate, "findings": findings,
            "summary": f"a11y static check {status.upper()} — {scanned} files, {serious} serious, {moderate} moderate"}


def add_args(p):
    p.add_argument("--file", action="append", help="specific file(s) to check (repeatable); default: scan --root")


if __name__ == "__main__":
    sys.exit(AgentScript("A06", "fe_a11y_check", run, description=__doc__, add_args=add_args).main())
