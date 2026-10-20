#!/usr/bin/env python3
"""A27 - validate an audit findings file for required fields, severity consistency, proof-of-concept references and fix status, and aggregate it by category.

Scans `*findings*.json|yaml|yml` under --root (or the one file given with --path), checks every finding and reports problems as
findings with severity info|minor|major|blocker; the run status is "fail" when any major or blocker exists. The audit severity
scale inside a findings file is info|low|medium|high|critical (see swarm/tools/a27_tools.py, which supplies the shared rules).
Read-only and bounded: files over 1 MiB are skipped, at most 2000 files are visited, symlinks are never followed, binaries are
skipped, YAML needs PyYAML (otherwise `skipped:tool-missing`; JSON is always native). No external binary is required.
`--dry-run` prints deterministic canned output and reads nothing. Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.gates import make_finding  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a27_tools  # noqa: E402

MAX_FILE_BYTES = 1 << 20
MAX_FILES = 2000
MAX_REPORTED = 1000
MAX_DEDUPE = 200
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm"}
SUFFIXES = {".json", ".yaml", ".yml"}
REQUIRED = ("id", "title", "severity", "category", "location", "description", "recommendation", "status")
SERIOUS = ("critical", "high")
LEVELS = ("low", "medium", "high")


def _blank(value) -> bool:
    return not isinstance(value, str) or not value.strip()


_clean = a27_tools._clean  # secret-looking spans become [REDACTED]: a findings file may quote a key, token or credentialed URL


def _shown(value) -> str:
    """A caller-supplied value made safe to quote in a summary: cleaned, at most 80 characters, repr-quoted."""
    return repr(_clean(str(value))[:80])


def _yaml_module():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed on demand
    except ImportError:
        return None
    return yaml


def _discover(root: Path, target: Path) -> tuple[list[Path], bool]:
    """Candidate findings files: the target itself, or `*findings*` json/yaml files below it. (files, truncated)"""
    if target.is_file():
        return [target], False
    found: list[Path] = []
    visited = 0
    for current, dirs, names in os.walk(target, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(current, d)))
        for name in sorted(names):
            visited += 1
            if visited > MAX_FILES:
                return found, True
            path = Path(current) / name
            if path.suffix.lower() in SUFFIXES and "findings" in name.lower() and not path.is_symlink():
                found.append(path)
    return found, False


def _load(path: Path):
    """(document, skip_reason, error). Reads at most MAX_FILE_BYTES."""
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return None, "too-large", ""
    raw = path.read_bytes()
    if b"\0" in raw[:8192]:
        return None, "binary", ""
    text = raw.decode("utf-8", errors="replace")
    if path.suffix.lower() == ".json":
        try:
            return json.loads(text), "", ""
        except (ValueError, RecursionError) as exc:
            if isinstance(exc, ValueError) and hasattr(exc, "lineno"):
                return None, "", f"not valid JSON at line {exc.lineno} column {exc.colno}"
            return None, "", "not valid JSON"
    yaml = _yaml_module()
    if yaml is None:
        return None, "tool-missing", ""
    try:
        return yaml.safe_load(text), "", ""
    except Exception as exc:  # noqa: BLE001 - any parser failure is a finding, never a crash
        mark = getattr(exc, "problem_mark", None)  # PyYAML's message quotes the offending line: report the position only
        return None, "", f"not valid YAML at line {mark.line + 1} column {mark.column + 1}" if mark is not None else "not valid YAML"


def _poc_state(root: Path, reference: str) -> str:
    """found | missing | outside: where a proof-of-concept reference (path, optional ::test or #anchor suffix) points."""
    text = reference.split("::", 1)[0].split("#", 1)[0].strip()
    if not text:
        return "missing"
    try:
        candidate = (root / text).resolve()
        if not candidate.is_relative_to(root):
            return "outside"
        return "found" if candidate.is_file() else "missing"
    except (ValueError, OSError):  # an embedded NUL or a name longer than the file system allows cannot exist
        return "missing"


def _check_finding(root: Path, rel: str, index: int, item, seen: set[str], out: list[dict], stats: dict) -> None:
    label = f"#{index}"
    if not isinstance(item, dict):
        out.append(make_finding(f"bad-finding:{rel}:{label}", "major", "bad-finding", f"finding {label} is not an object", location=rel))
        return
    fid = item.get("id") if isinstance(item.get("id"), str) and item.get("id").strip() else label
    label = _clean(fid)[:80]

    def add(rule: str, severity: str, summary: str) -> None:
        out.append(make_finding(f"{rule}:{rel}:{label}", severity, rule, summary, location=rel))

    for key in REQUIRED:
        if _blank(item.get(key)):
            add("missing-field", "major", f"{label}: required field {key!r} is missing or empty")
    if isinstance(item.get("id"), str) and item["id"].strip():
        if item["id"] in seen:
            add("duplicate-id", "major", f"{label}: id is used by more than one finding")
        seen.add(item["id"])
    severity, status = item.get("severity"), item.get("status")
    severity_ok = severity in a27_tools.SEVERITIES
    status_ok = status in a27_tools.FIX_STATUSES
    if isinstance(severity, str) and severity.strip() and not severity_ok:
        add("invalid-severity", "major", f"{label}: severity {_shown(severity)} is not one of {', '.join(a27_tools.SEVERITIES)}")
    if isinstance(status, str) and status.strip() and not status_ok:
        add("invalid-status", "major", f"{label}: status {_shown(status)} is not one of {', '.join(a27_tools.FIX_STATUSES)}")
    category = item.get("category")
    cat = _clean(category.strip())[:80] if isinstance(category, str) and category.strip() else "(none)"
    bucket = stats["by_category"].setdefault(cat, {"total": 0, "open": 0, "by_severity": {}})
    bucket["total"] += 1
    sev_key = severity if severity_ok else "invalid"
    bucket["by_severity"][sev_key] = bucket["by_severity"].get(sev_key, 0) + 1
    stats["by_severity"][sev_key] = stats["by_severity"].get(sev_key, 0) + 1
    stat_key = status if status_ok else "invalid"
    stats["by_status"][stat_key] = stats["by_status"].get(stat_key, 0) + 1
    if status == "open":
        bucket["open"] += 1

    impact, likelihood = item.get("impact"), item.get("likelihood")
    if (impact is None) != (likelihood is None):
        add("incomplete-scoring", "minor", f"{label}: impact and likelihood must be given together")
    elif impact is not None:
        if impact not in LEVELS or likelihood not in LEVELS:
            add("invalid-scoring", "minor", f"{label}: impact and likelihood must each be one of {', '.join(LEVELS)}")
        elif severity_ok and a27_tools._matrix(impact, likelihood) != severity and item.get("severity_override_approved") is not True:
            expected = a27_tools._matrix(impact, likelihood)
            add("severity-mismatch", "major", f"{label}: severity {severity} differs from impact x likelihood = {expected}; an override needs L3 approval (severity_override_approved)")

    serious = severity in SERIOUS
    poc = item.get("poc")
    if _blank(poc):
        if serious:
            add("poc-missing", "major", f"{label}: {severity} finding has no proof-of-concept reference (poc)")
    else:
        state = _poc_state(root, poc)
        if state != "found":
            level = "major" if serious else "minor"
            add("poc-not-found" if state == "missing" else "poc-outside-root", level, f"{label}: poc {_shown(poc)} {'does not exist under the root' if state == 'missing' else 'points outside the root'}")

    commit = item.get("fix_commit")
    if status in ("fixed", "mitigated"):
        if _blank(commit):
            add("fix-commit-missing", "major", f"{label}: status {status} but no fix_commit")
        elif not a27_tools._is_commit(commit.strip()):
            add("fix-commit-invalid", "major", f"{label}: fix_commit {_shown(commit)} is not a git object id (7-40 or 64 hex characters)")
        if serious and not (item.get("regression_test") is True and item.get("reaudited") is True):
            add("fix-unverified", "minor", f"{label}: {severity} fix lacks a regression test or a re-audit (regression_test, reaudited)")
    elif status == "open":
        if severity == "critical":
            add("open-blocking", "blocker", f"{label}: critical finding is still open")
        elif severity == "high":
            add("open-high", "major", f"{label}: high finding is still open")
    elif status in ("acknowledged", "wontfix") and serious:
        if _blank(item.get("risk_acceptance")):
            add("risk-acceptance-missing", "major", f"{label}: {status} {severity} finding needs an L4 risk acceptance reference (risk_acceptance)")
        else:
            add("risk-accepted", "info", f"{label}: {status} {severity} finding carries risk acceptance {_shown(item['risk_acceptance'])}")


def _duplicates(rel: str, items: list, out: list[dict]) -> None:
    rows = []
    for item in items:
        if not isinstance(item, dict) or any(_blank(item.get(k)) for k in ("id", "title", "category", "location")) or item.get("severity") not in a27_tools.SEVERITIES:
            continue
        rows.append(
            {
                "id": item["id"], "title": item["title"], "category": item["category"], "file": item["location"].split(":", 1)[0] or item["location"],
                "severity": item["severity"],
            }
        )
    ids = [row["id"] for row in rows]
    rows = [row for index, row in enumerate(rows) if row["id"] not in ids[:index]]  # first occurrence wins; duplicate ids are reported separately
    if len(rows) > MAX_DEDUPE:
        out.append(make_finding(f"dedupe-truncated:{rel}", "info", "dedupe-truncated", f"duplicate detection ran on the first {MAX_DEDUPE} of {len(rows)} findings", location=rel))
        rows = rows[:MAX_DEDUPE]
    if not rows:
        return
    result = a27_tools.dedupe_findings({"findings": rows})
    for group in result.get("groups", []):
        out.append(
            make_finding(
                f"possible-duplicate:{rel}:{group['primary_id']}", "minor", "possible-duplicate",
                f"{group['primary_id']} duplicates {', '.join(group['duplicate_ids'])}" + ("; severities differ" if group["severity_conflict"] else ""), location=rel,
            )
        )


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "findings": [], "summary": "dry-run: audit_findings_check would validate findings files under --root",
            "files_checked": 0, "files_skipped": [], "findings_total": 0, "by_category": {}, "by_severity": {}, "by_status": {}, "truncated": False,
        }
    root = ctx.root
    target = (root / args.path).resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {_shown(args.path)} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {_shown(args.path)} does not exist under {root}")
    files, truncated = _discover(root, target)
    out: list[dict] = []
    skipped: list[str] = []
    stats = {"by_category": {}, "by_severity": {}, "by_status": {}}
    checked = total = 0
    for path in files:
        rel = _clean(path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name)
        try:
            document, skip, error = _load(path)
        except OSError as exc:
            skipped.append(f"skipped:unreadable: {rel} ({type(exc).__name__})")
            continue
        if skip:
            skipped.append(f"skipped:{skip}: {rel}" + (" (yaml needs PyYAML)" if skip == "tool-missing" else ""))
            continue
        checked += 1
        if error:
            out.append(make_finding(f"unparseable:{rel}", "major", "unparseable", f"{rel}: {error}", location=rel))
            continue
        items = document.get("findings") if isinstance(document, dict) else document
        if not isinstance(items, list):
            out.append(make_finding(f"bad-shape:{rel}", "major", "bad-shape", f"{rel}: expected a list of findings or an object with a 'findings' list", location=rel))
            continue
        seen: set[str] = set()
        for index, item in enumerate(items):
            _check_finding(root, rel, index, item, seen, out, stats)
        total += len(items)
        _duplicates(rel, items, out)
    if not files:
        out.append(make_finding("no-findings-file", "info", "no-findings-file", f"no *findings*.json|yaml|yml file found under {_clean(args.path)}"))
    rank = {name: index for index, name in enumerate(("info", "minor", "major", "blocker"))}
    out.sort(key=lambda f: (-rank[f["severity"]], f["id"]))
    majors = sum(1 for f in out if f["severity"] in ("major", "blocker"))
    failing = majors > 0
    capped = len(out) > MAX_REPORTED
    if capped:
        out = out[:MAX_REPORTED]
    summary = f"{total} finding(s) in {checked} file(s): " + (f"{majors} major or worse problem(s)" if failing else "no major or worse problem")
    return {
        "status": "fail" if failing else "ok",
        "findings": out,
        "summary": summary + (f"; {len(skipped)} file(s) skipped" if skipped else ""),
        "files_checked": checked,
        "files_skipped": skipped,
        "findings_total": total,
        "by_category": {key: stats["by_category"][key] for key in sorted(stats["by_category"])},
        "by_severity": {key: stats["by_severity"][key] for key in sorted(stats["by_severity"])},
        "by_status": {key: stats["by_status"][key] for key in sorted(stats["by_status"])},
        "truncated": truncated or capped,
    }


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="findings file, or directory under --root to scan for *findings*.json|yaml|yml")


if __name__ == "__main__":
    sys.exit(AgentScript("A27", "audit_findings_check", run, description=__doc__, add_args=add_args).main())
