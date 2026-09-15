#!/usr/bin/env python3
"""A02 — lint acceptance criteria for machine-checkability.

Reads a Markdown or JSON criteria document (--file) or scans docs/ and every *.md
under --root that mentions "AC-". Checks Given/When/Then structure, unique AC ids,
measurable language (numbers/units in NFR-like clauses), vague words such as
"fast" or "user-friendly", and the share of manual-only criteria (> 30 % fails).
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding, derive_verdict  # noqa: E402

AC_RE = re.compile(r"\b(AC-\d[A-Za-z0-9_.]*)\b")
VAGUE = ["fast", "quick", "quickly", "user-friendly", "user friendly", "intuitive", "easy", "easily",
         "simple", "robust", "scalable", "efficient", "responsive", "appropriate", "adequate", "reasonable",
         "seamless", "as needed", "etc", "and/or", "if possible", "optimal", "good", "nice", "modern"]
MEASURE_RE = re.compile(r"\d+\s*(ms|s|sec|m|min|h|rps|qps|%|kb|mb|gb|px|users?|items?|x)\b|\b\d+\b|[<>=≤≥]", re.I)
MANUAL_RE = re.compile(r"\bmanual(ly)?\b|automated\W*:?\W*false", re.I)


def _blocks_from_markdown(text: str) -> list[dict]:
    """Split text into one block per AC id occurrence (text until the next AC id)."""
    hits = list(AC_RE.finditer(text))
    blocks = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else min(len(text), m.end() + 600)
        body = text[m.start():end]
        line = text.count("\n", 0, m.start()) + 1
        blocks.append({"id": m.group(1), "text": body, "line": line})
    return blocks


def _blocks_from_json(data) -> list[dict]:
    items = data.get("criteria", data.get("acceptance", [])) if isinstance(data, dict) else data
    blocks = []
    for i, c in enumerate(items or []):
        if not isinstance(c, dict):
            continue
        text = " ".join(f"{k}: {v}" for k, v in c.items())
        if c.get("automated") is False:
            text += " automated: false"
        blocks.append({"id": str(c.get("id", f"AC-?{i}")), "text": text, "line": i + 1,
                       "has_gwt": all(k in c for k in ("given", "when", "then"))})
    return blocks


def lint_file(path: Path, findings: list, counter: list[int]) -> dict:
    raw = path.read_text(errors="replace")
    if path.suffix == ".json":
        try:
            blocks = _blocks_from_json(json.loads(raw))
        except json.JSONDecodeError as e:
            counter[0] += 1
            findings.append(make_finding(f"RF-{counter[0]:03d}", "major", "input", f"invalid JSON: {e}",
                                         location=str(path), owner_suggestion="A02"))
            return {"file": str(path), "criteria": 0}
    else:
        blocks = _blocks_from_markdown(raw)
    seen, manual = {}, 0
    for b in blocks:
        loc = f"{path}:{b['line']}"
        low = b["text"].lower()

        def add(sev, kind, msg):
            counter[0] += 1
            findings.append(make_finding(f"RF-{counter[0]:03d}", sev, kind, msg, ac_ref=b["id"],
                                         location=loc, owner_suggestion="A02"))
        if b["id"] in seen:
            add("major", "duplicate-id", f"{b['id']} already defined at line {seen[b['id']]}")
        seen.setdefault(b["id"], b["line"])
        has_gwt = b.get("has_gwt", all(k in low for k in ("given", "when", "then")))
        if not has_gwt:
            missing = [k for k in ("given", "when", "then") if k not in low]
            add("major", "structure", f"{b['id']} lacks Given/When/Then (missing: {', '.join(missing)})")
        vague = [w for w in VAGUE if re.search(rf"\b{re.escape(w)}\b", low)]
        if vague:
            add("minor", "vague", f"{b['id']} uses vague words: {', '.join(vague)}")
        if re.search(r"\b(perf|latency|throughput|load|within|under|response time|p9\d)\b", low) \
                and not MEASURE_RE.search(b["text"]):
            add("major", "measurable", f"{b['id']} states a performance expectation without a number/unit")
        if MANUAL_RE.search(b["text"]):
            manual += 1
    if blocks and manual / len(blocks) > 0.30:
        counter[0] += 1
        findings.append(make_finding(f"RF-{counter[0]:03d}", "major", "manual-ratio",
                                     f"{manual}/{len(blocks)} criteria are manual-only (> 30 %)",
                                     location=str(path), owner_suggestion="A02"))
    return {"file": str(path), "criteria": len(blocks), "manual": manual}


def discover(root: Path) -> list[Path]:
    out = []
    for p in iter_files(root, exts=(".md", ".json")):
        if p.suffix == ".json" and "criteria" not in p.name and "acceptance" not in p.name:
            continue
        try:
            if "AC-" in p.read_text(errors="replace"):
                out.append(p)
        except OSError:
            continue
    return sorted(out)


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "summary": "dry-run: 3 criteria linted, 0 findings", "dry_run": True,
                "files": [{"file": "docs/acceptance.md", "criteria": 3, "manual": 0}], "findings": [],
                "verdict": "pass"}
    if args.file:
        files = [Path(args.file) if Path(args.file).is_absolute() else ctx.root / args.file]
        if not files[0].exists():
            return {"status": "fail", "summary": f"E-INPUT: {files[0]} not found", "findings": [
                make_finding("RF-000", "major", "input", f"file not found: {files[0]}", owner_suggestion="A02")]}
    else:
        files = discover(ctx.root)
    findings, counter, reports = [], [0], []
    for f in files:
        reports.append(lint_file(f, findings, counter))
    total = sum(r.get("criteria", 0) for r in reports)
    if not files:
        return {"status": "ok", "summary": "no acceptance-criteria documents found (nothing containing 'AC-')",
                "files": [], "findings": [], "verdict": "pass"}
    verdict = derive_verdict(findings)
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict, "files": reports,
            "findings": findings,
            "summary": f"req lint {verdict.upper()} — {total} criteria in {len(files)} file(s), {len(findings)} finding(s)"}


def add_args(p):
    p.add_argument("--file", help="criteria document (.md or .json); default: scan --root for files containing 'AC-'")


if __name__ == "__main__":
    sys.exit(AgentScript("A02", "req_lint", run, description=__doc__, add_args=add_args).main())
