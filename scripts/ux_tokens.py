#!/usr/bin/env python3
"""A04 — validate W3C design-tokens JSON and compute WCAG contrast ratios.

Walks a tokens file (--file, or every *tokens*.json under --root): each leaf must
carry `$value` and a `$type` (own or inherited from its group)
colour values must
be valid hex (#rgb/#rrggbb/#rrggbbaa) or an alias `{path.to.token}` that resolves.
Contrast: `--fg/--bg` checks one pair
otherwise every colour token is checked
against the background token (--background, default: first token whose path
contains "background"/"bg"). Ratio < --min-ratio (4.5, WCAG AA) is a major finding.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding, derive_verdict  # noqa: E402

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ALIAS_RE = re.compile(r"^\{([^}]+)\}$")
BG_HINTS = ("background", "bg", "surface", "canvas")


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h[:3])
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def luminance(h: str) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in hex_to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    l1, l2 = sorted((luminance(fg), luminance(bg)), reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


def walk(node, path: list[str], inherited: str | None, out: dict):
    if not isinstance(node, dict):
        return
    if "$value" in node:
        out[".".join(path)] = {"type": node.get("$type", inherited), "value": node["$value"],
                               "explicit_type": "$type" in node}
        return
    group_type = node.get("$type", inherited)
    for k, v in node.items():
        if not k.startswith("$"):
            walk(v, path + [k], group_type, out)


def resolve(value, tokens: dict, depth=0):
    while isinstance(value, str) and ALIAS_RE.match(value) and depth < 10:
        ref = ALIAS_RE.match(value).group(1)
        if ref not in tokens:
            return None
        value, depth = tokens[ref]["value"], depth + 1
    return value


def check_file(path: Path, args, findings: list, fid) -> dict:
    loc = str(path)
    try:
        data = json.loads(path.read_text(errors="replace"))
    except json.JSONDecodeError as e:
        findings.append(make_finding(fid(), "major", "input", f"invalid JSON: {e}", location=loc, owner_suggestion="A04"))
        return {"file": loc, "tokens": 0}
    tokens: dict = {}
    walk(data, [], None, tokens)
    colours = {}
    for name, t in tokens.items():
        if t["type"] is None:
            findings.append(make_finding(fid(), "minor", "schema", f"{name}: no $type (own or inherited)", location=loc))
        val = resolve(t["value"], tokens)
        if val is None:
            findings.append(make_finding(fid(), "major", "alias", f"{name}: alias {t['value']} does not resolve", location=loc))
            continue
        if t["type"] == "color":
            if isinstance(val, str) and HEX_RE.match(val):
                colours[name] = val
            else:
                findings.append(make_finding(fid(), "major", "color", f"{name}: invalid colour value {val!r}", location=loc))
    if not tokens:
        findings.append(make_finding(fid(), "minor", "schema", "no tokens ($value leaves) found", location=loc))
    bg_name = args.background or next((n for n in colours if any(h in n.lower() for h in BG_HINTS)), None)
    pairs = []
    if bg_name and bg_name in colours:
        bg = colours[bg_name]
        for name, val in colours.items():
            if name == bg_name or any(h in name.lower() for h in BG_HINTS):
                continue
            ratio = contrast(val, bg)
            pairs.append({"fg": name, "bg": bg_name, "ratio": ratio, "aa": ratio >= args.min_ratio})
            if ratio < args.min_ratio:
                findings.append(make_finding(fid(), "major", "contrast",
                                             f"{name} ({val}) on {bg_name} ({bg}) = {ratio}:1 < {args.min_ratio}:1",
                                             location=loc, owner_suggestion="A04"))
    elif args.background:
        findings.append(make_finding(fid(), "major", "input", f"background token {args.background!r} not found", location=loc))
    return {"file": loc, "tokens": len(tokens), "colours": len(colours), "background": bg_name, "contrast": pairs}


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "summary": "dry-run: 12 tokens valid, contrast 12.6:1", "dry_run": True, "verdict": "pass",
                "files": [{"file": "design/tokens.json", "tokens": 12, "colours": 4, "background": "color.background",
                           "contrast": [{"fg": "color.text", "bg": "color.background", "ratio": 12.6, "aa": True}]}],
                "findings": []}
    counter = [0]

    def fid():
        counter[0] += 1
        return f"UF-{counter[0]:03d}"
    findings, reports = [], []
    if args.fg or args.bg:
        if not (args.fg and args.bg and HEX_RE.match(args.fg) and HEX_RE.match(args.bg)):
            return {"status": "fail", "summary": "E-INPUT: --fg and --bg must both be valid hex colours",
                    "findings": [make_finding("UF-000", "major", "input", "bad --fg/--bg", owner_suggestion="A04")]}
        ratio = contrast(args.fg, args.bg)
        if ratio < args.min_ratio:
            findings.append(make_finding(fid(), "major", "contrast", f"{args.fg} on {args.bg} = {ratio}:1 < {args.min_ratio}:1",
                                         owner_suggestion="A04"))
        reports.append({"fg": args.fg, "bg": args.bg, "ratio": ratio, "aa": ratio >= args.min_ratio,
                        "aaa": ratio >= 7.0, "large_text_aa": ratio >= 3.0})
    else:
        if args.file:
            files = [Path(args.file) if Path(args.file).is_absolute() else ctx.root / args.file]
            if not files[0].exists():
                return {"status": "fail", "summary": f"E-INPUT: {files[0]} not found", "findings": [
                    make_finding("UF-000", "major", "input", f"file not found: {files[0]}", owner_suggestion="A04")]}
        else:
            files = sorted(p for p in iter_files(ctx.root, exts=(".json",)) if "token" in p.name.lower())
        if not files:
            return {"status": "ok", "summary": "no design-token files (*tokens*.json) found", "files": [],
                    "findings": [], "verdict": "pass"}
        for f in files:
            reports.append(check_file(f, args, findings, fid))
    verdict = derive_verdict(findings)
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict, "files": reports, "findings": findings,
            "summary": f"ux tokens {verdict.upper()} — {len(reports)} check(s), {len(findings)} finding(s)"}


def add_args(p):
    p.add_argument("--file", help="design tokens JSON (default: scan --root for *tokens*.json)")
    p.add_argument("--background", help="token path used as background for contrast checks")
    p.add_argument("--fg", help="ad-hoc foreground hex colour")
    p.add_argument("--bg", help="ad-hoc background hex colour")
    p.add_argument("--min-ratio", type=float, default=4.5, help="WCAG AA threshold (4.5 normal text)")


if __name__ == "__main__":
    sys.exit(AgentScript("A04", "ux_tokens", run, description=__doc__, add_args=add_args).main())
