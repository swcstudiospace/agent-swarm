#!/usr/bin/env python3
"""A15 — index markdown docs, validate links/headings/titles, measure coverage, build changelog.

Walks every *.md under --root (or --docs-dir), checks that internal relative links
resolve, that heading levels never skip (h1 → h3), and that each file has a title.
Computes docs coverage: every top-level source directory must own a README or be
referenced from some doc. Writes .swarm/docs_index.json and, with --changelog, a
Keep-a-Changelog section grouped by conventional-commit type from `git log --since`
(`skipped:tool-missing` when git is absent). Broken links are major findings;
missing titles, skipped headings and coverage gaps are minor.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, sh, which, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SRC_EXTS = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".sh"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", "tests", "test", "docs"}
CC_GROUPS = {"feat": "Added", "fix": "Fixed", "perf": "Changed", "refactor": "Changed", "revert": "Removed",
             "docs": "Documentation", "security": "Security"}
CC_RE = re.compile(r"^(\w+)(\([^)]*\))?(!)?:\s*(.+)$")


def strip_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def analyse_md(path: Path, root: Path) -> tuple[dict, list[dict]]:
    text = path.read_text(errors="ignore")
    rel = str(path.relative_to(root))
    body = strip_code(text)
    headings, findings, prev = [], [], 0
    title = None
    fm = re.match(r"^---\n(.*?)\n---", text, re.S)
    if fm:
        m = re.search(r"^title:\s*(.+)$", fm.group(1), re.M)
        title = m.group(1).strip().strip("\"'") if m else None
    for lineno, line in enumerate(body.splitlines(), 1):
        h = HEAD_RE.match(line)
        if not h:
            continue
        level = len(h.group(1))
        headings.append({"level": level, "text": h.group(2), "line": lineno})
        if title is None and level == 1:
            title = h.group(2)
        if prev and level > prev + 1:
            findings.append(make_finding("", "minor", "heading", f"heading level skips h{prev} → h{level}",
                                         location=f"{rel}:{lineno}", owner_suggestion="A15"))
        prev = level
    if title is None:
        findings.append(make_finding("", "minor", "no-title", "file has no H1 or front-matter title", location=rel, owner_suggestion="A15"))
    links, broken = [], []
    for m in LINK_RE.finditer(body):
        target = m.group(1)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(("#", "<")):
            continue  # external URL, mailto, in-page anchor
        clean = target.split("#", 1)[0].split("?", 1)[0]
        if not clean:
            continue
        dest = (root / clean.lstrip("/")) if target.startswith("/") else (path.parent / clean)
        ok = dest.exists()
        links.append({"target": target, "resolved": ok})
        if not ok:
            broken.append(target)
            findings.append(make_finding("", "major", "broken-link", f"link target not found: {target}",
                                         location=rel, evidence=str(dest), owner_suggestion="A15"))
    return {"path": rel, "title": title, "headings": headings, "links": len(links), "broken_links": broken,
            "words": len(body.split())}, findings


def coverage(root: Path, docs: list[dict], all_text: str) -> dict:
    pkgs = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS):
        if not any(f.suffix in SRC_EXTS for f in d.rglob("*") if f.is_file()):
            continue
        has_readme = any(f.name.lower().startswith("readme") for f in d.iterdir())
        referenced = bool(re.search(rf"(?<![\w/]){re.escape(d.name)}/", all_text))
        pkgs[d.name] = {"readme": has_readme, "referenced": referenced, "covered": has_readme or referenced}
    covered = sum(1 for v in pkgs.values() if v["covered"])
    return {"packages": pkgs, "packages_documented_pct": round(100 * covered / len(pkgs)) if pkgs else 100,
            "docs_with_title_pct": round(100 * sum(1 for d in docs if d["title"]) / len(docs)) if docs else 100}


def changelog(root: Path, since: str | None) -> str:
    if not which("git") or sh(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, timeout=30).returncode != 0:
        return "skipped:tool-missing"
    cmd = ["git", "log", "--no-merges", "--format=%h%x00%s"] + ([f"--since={since}"] if since else ["-n", "200"])
    proc = sh(cmd, cwd=root, timeout=60)
    if proc.returncode != 0:
        return "skipped:tool-missing"
    groups: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        sha, _, subject = line.partition("\0")
        m = CC_RE.match(subject)
        kind, breaking, desc = (m.group(1).lower(), bool(m.group(3)), m.group(4)) if m else ("other", False, subject)
        group = "Changed" if breaking else CC_GROUPS.get(kind, "Other")
        groups.setdefault(group, []).append(f"- {desc}{' **BREAKING**' if breaking else ''} ({sha})")
    out = [f"## [Unreleased]{' - since ' + since if since else ''}"]
    for g in ("Added", "Changed", "Fixed", "Removed", "Security", "Documentation", "Other"):
        if g in groups:
            out += [f"### {g}", *groups[g], ""]
    return "\n".join(out).rstrip() + "\n"


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "index": [{"path": "README.md", "title": "Project", "links": 3, "broken_links": []}],
                "coverage": {"packages": {}, "packages_documented_pct": 100, "docs_with_title_pct": 100},
                "changelog_md": "## [Unreleased]\n### Added\n- canned entry (0000000)\n" if args.changelog else None,
                "findings": [], "summary": "dry-run: 1 doc indexed, 0 broken links, coverage 100%"}
    scan_root = (ctx.root / args.docs_dir).resolve() if args.docs_dir else ctx.root
    docs, findings, texts = [], [], []
    for p in sorted(iter_files(scan_root, exts=(".md", ".markdown"))):
        entry, f = analyse_md(p, ctx.root)
        docs.append(entry)
        findings.extend(f)
        texts.append(strip_code(p.read_text(errors="ignore")))
    cov = coverage(ctx.root, docs, "\n".join(texts))
    for name, v in cov["packages"].items():
        if not v["covered"]:
            findings.append(make_finding("", "minor", "coverage", f"top-level package '{name}/' has no README and is not referenced by any doc",
                                         location=f"{name}/", owner_suggestion="A15"))
    for i, f in enumerate(findings):
        f["id"] = f"DF-{i+1:03d}"
    log = changelog(ctx.root, args.since) if args.changelog else None
    index = {"root": str(ctx.root), "docs": docs, "coverage": cov, "findings": findings, "changelog_md": log}
    SWARM_DIR.mkdir(parents=True, exist_ok=True)
    (SWARM_DIR / "docs_index.json").write_text(json.dumps(index, indent=2))
    broken = sum(len(d["broken_links"]) for d in docs)
    status = "fail" if any(f["severity"] in ("major", "critical", "blocker") for f in findings) else "ok"
    return {"status": status, "index": docs, "coverage": cov, "changelog_md": log, "findings": findings,
            "summary": f"{len(docs)} docs indexed, {broken} broken link(s), {len(findings)} finding(s), "
                       f"package coverage {cov['packages_documented_pct']}%"}


def add_args(p):
    p.add_argument("--docs-dir", help="subdirectory to index instead of the whole root (coverage still uses root)")
    p.add_argument("--changelog", action="store_true", help="build a Keep-a-Changelog section from git log")
    p.add_argument("--since", help="git --since value for --changelog (e.g. 2026-08-01 or '2 weeks ago')")


if __name__ == "__main__":
    sys.exit(AgentScript("A15", "docs_bundle", run, description=__doc__, add_args=add_args).main())
