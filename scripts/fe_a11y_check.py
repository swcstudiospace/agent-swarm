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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402

EXTS = (".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte")
TAG = re.compile(r"<(img|button|a|input|select|textarea|html|div|span)\b([^>]*)>", re.I | re.S)
LABEL_FOR = re.compile(r"<label\b[^>]*\bfor\s*=\s*[\"'{]([^\"'}]+)", re.I)
HAS_TEXT = re.compile(r"[A-Za-z0-9]|\{[^}]*\}|<(?:svg|img|i|span|Icon|Trans|FormattedMessage)\b", re.I)


def _attr(attrs: str, name: str) -> str | None:
    n = re.escape(name)
    m = re.search(r"(?:^|\s)" + n + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{([^}]*)\})", attrs, re.I)
    if m:
        return next((g for g in m.groups() if g is not None), "")
    return "" if re.search(r"(?:^|\s)" + n + r"(?=\s|$|/)", attrs, re.I) else None


def _labelled(attrs: str) -> bool:
    return any(_attr(attrs, a) for a in ("aria-label", "aria-labelledby", "title", ":aria-label", "v-bind:aria-label"))


def _inner(text: str, tag: str, start: int) -> str:
    end = re.compile(rf"</{tag}\s*>", re.I).search(text, start)
    return text[start:end.start()] if end else ""


def check_text(text: str, rel: str) -> list[dict]:
    issues: list[dict] = []
    label_ids = set(LABEL_FOR.findall(text))
    stripped = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), text, flags=re.S)

    def add(sev: str, kind: str, msg: str, pos: int):
        issues.append({"sev": sev, "kind": kind, "msg": msg, "loc": f"{rel}:{stripped.count(chr(10), 0, pos) + 1}"})

    for m in TAG.finditer(stripped):
        tag, attrs, pos = m.group(1).lower(), m.group(2), m.start()
        if tag == "html" and _attr(attrs, "lang") is None:
            add("serious", "lang", "<html> missing lang attribute", pos)
        elif tag == "img" and _attr(attrs, "alt") is None and not _labelled(attrs) and _attr(attrs, "role") != "presentation":
            add("serious", "img-alt", "<img> without alt text", pos)
        elif tag in ("button", "a"):
            inner = _inner(stripped, tag, m.end())
            if tag == "a" and _attr(attrs, "href") is None and not _attr(attrs, "onClick") and not _attr(attrs, "@click"):
                continue  # anchor used as a plain container/target
            if not _labelled(attrs) and not HAS_TEXT.search(re.sub(r"<[^>]+>", "", inner) or inner):
                add("serious", "name", f"<{tag}> has no accessible name (text, aria-label or aria-labelledby)", pos)
        elif tag in ("input", "select", "textarea"):
            typ = (_attr(attrs, "type") or "").lower()
            if typ in ("hidden", "submit", "button", "reset", "image"):
                continue
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
        ti = _attr(attrs, "tabindex") or _attr(attrs, "tabIndex")
        if ti and ti.strip().lstrip("+").isdigit() and int(ti.strip()) > 0:
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
