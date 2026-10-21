#!/usr/bin/env python3
"""A17 - lint service-catalog descriptors (catalog-info.yaml or JSON) in a repository for owner, lifecycle, system, tier, dependsOn and docs annotations.

Scans catalog-info.{yaml,yml,json} (also catalog-info.<name>.yaml, in any letter case) under --root (or the file/directory given by --path), flattens
every Backstage-style entity and applies the same rules as the registry tool plat_catalog_lint (swarm/tools/a17_tools.py), plus a
repository-wide dependsOn graph check (cycles, dangling targets, deprecated or experimental dependencies) and an apiVersion check.
Bounded: files over 1 MiB, binaries and symlinks that leave the root are skipped and listed under `skipped` (at most 100 entries, then
a `skipped:more:<n>` marker); at most 2000 descriptors are read and the dependsOn graph check covers at most 300 entities. YAML needs
PyYAML: without it a YAML file that is not also valid JSON is reported as `skipped:tool-missing`; JSON descriptors are always handled natively.
Every skipped file, a truncated scan, a skipped graph check and an empty scan (no descriptor analysed) add a major `scan-incomplete`
finding, so `status` is `fail` until every in-scope input is analysed (the shared rule in swarm/scan_status.py).
Read-only: it reads files under --root and writes nothing. Stdlib only; problems are findings (severity info|minor|major|blocker,
status "fail" when any major or blocker exists) and it never crashes on malformed input. Findings never echo descriptor values:
annotation values (except a `tier` annotation, compared and not echoed) and link URLs are not read, and parse errors report only the
error class and position. `--dry-run` prints deterministic canned
output and reads nothing.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a17_tools  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_DESCRIPTORS = 2000
MAX_VISITED = 100_000
MAX_SKIPPED = 100
MAX_GRAPH_ENTRIES = 300
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", "target", "vendor"})
DESCRIPTOR_NAME = re.compile(r"catalog-info(\.[A-Za-z0-9_-]+)?\.(ya?ml|json)", re.IGNORECASE)
API_VERSION_PREFIXES = ("backstage.io/", "scaffolder.backstage.io/")
GRAPH_RULES = ("dependency-cycle", "self-dependency", "dangling-dependency", "deprecated-dependency", "experimental-dependency")
DEFAULT_TIERS = ",".join(a17_tools.DEFAULT_TIERS)


def _yaml_module():
    """PyYAML when importable, else None (the YAML-dependent checks are then reported as skipped:tool-missing)."""
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_list(value) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _discover(target: Path) -> tuple[list[Path], bool]:
    """Descriptor files in deterministic order, and whether a bound cut the walk. Directory symlinks are never descended."""
    if target.is_file():
        return [target], False
    found: list[Path] = []
    visited = 0
    truncated = False
    for current, dirs, files in os.walk(target, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            visited += 1
            if visited > MAX_VISITED or len(found) >= MAX_DESCRIPTORS:
                truncated = True
                break
            if DESCRIPTOR_NAME.fullmatch(name):
                found.append(Path(current) / name)
        if truncated:
            break
    return found, truncated


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read(root: Path, path: Path, skipped: list[str]) -> str | None:
    """File text, or None (with a skipped note) for symlinks leaving the root, FIFOs and devices, oversized files, binaries and unreadable files."""
    rel = _rel(root, path)
    if path.is_symlink() and not path.resolve().is_relative_to(root):
        skipped.append(f"skipped:symlink-outside-root:{rel}")
        return None
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):  # a FIFO or device named catalog-info*.yaml would block the read forever
            skipped.append(f"skipped:unreadable:{rel}: NotRegularFile")
            return None
        if info.st_size > MAX_FILE_BYTES:
            skipped.append(f"skipped:too-large:{rel}")
            return None
        data = path.read_bytes()
    except OSError as exc:
        skipped.append(f"skipped:unreadable:{rel}: {exc.__class__.__name__}")
        return None
    if b"\0" in data[:8192]:
        skipped.append(f"skipped:binary:{rel}")
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        skipped.append(f"skipped:binary:{rel}")
        return None


def _documents(text: str, is_yaml: bool, yaml_module) -> tuple[list, str]:
    """Parsed documents and a status: ok | parse-error:<where> | tool-missing. JSON is native; JSON is also tried first for YAML files.
    The status never carries file content: only the error class and its line and column."""
    try:
        parsed = json.loads(text)
        return (parsed if isinstance(parsed, list) else [parsed]), "ok"
    except json.JSONDecodeError as exc:
        if not is_yaml:
            return [], f"parse-error:JSON {exc.msg} at line {exc.lineno}, column {exc.colno}"
    except (ValueError, RecursionError) as exc:
        if not is_yaml:
            return [], f"parse-error:JSON {exc.__class__.__name__}"
    if yaml_module is None:
        return [], "tool-missing"
    try:
        return list(yaml_module.safe_load_all(text)), "ok"
    except Exception as exc:  # noqa: BLE001 - any parser failure is a finding, never a crash
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        return [], f"parse-error:YAML {exc.__class__.__name__}{where}"


def _tier(metadata: dict, annotations: dict) -> str:
    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    if _text(labels.get("tier")):
        return _text(labels["tier"])
    for key in sorted(k for k in annotations if isinstance(k, str)):
        if key.rsplit("/", 1)[-1] == "tier" and _text(annotations[key]):
            return _text(annotations[key])
    return ""


def _raw(value) -> str:
    """A string field exactly as written (a padded name stays padded and fails the name rule); anything else is empty."""
    return value if isinstance(value, str) else ""


def _flatten(doc: dict) -> dict:
    """One descriptor document as the flattened entity the lint rules read. Annotation values and link URLs are never read."""
    metadata = doc["metadata"]
    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
    annotations = metadata.get("annotations") if isinstance(metadata.get("annotations"), dict) else {}
    links = metadata.get("links") if isinstance(metadata.get("links"), list) else []
    entry = {
        "kind": _raw(doc.get("kind")),
        "name": _raw(metadata.get("name")),
        "namespace": _raw(metadata.get("namespace")),
        "type": _text(spec.get("type")),
        "lifecycle": _text(spec.get("lifecycle")),
        "owner": _text(spec.get("owner")),
        "system": _text(spec.get("system")),
        "tier": _tier(metadata, annotations),
        "description": _text(metadata.get("description")),
        "depends_on": _string_list(spec.get("dependsOn")),
        "annotations": [k for k in annotations if isinstance(k, str)],
        "links": [_text(link.get("title")) for link in links if isinstance(link, dict)],
    }
    return entry


def _finding(rule: str, subject: str, severity: str, summary: str, location: str) -> dict:
    """Like the tool's finding, but the subject is a repository path or a grammar-valid entity id the script built itself."""
    return {"id": f"{rule}@{subject}" if subject else rule, "severity": severity, "summary": summary[: a17_tools.MAX_SUMMARY], "location": location}


def lint_tree(root: Path, target: Path, tiers: tuple[str, ...]) -> dict:
    skipped: list[str] = []
    incomplete_reasons: list[str] = []
    files, truncated = _discover(target)
    yaml_module = _yaml_module()
    findings: list[dict] = []
    entries: list[dict] = []
    where: list[str] = []
    refs: dict[str, str] = {}
    scanned = 0
    for path in files:
        text = _read(root, path, skipped)
        if text is None:
            continue
        rel = _rel(root, path)
        docs, status = _documents(text, path.suffix.lower() in (".yaml", ".yml"), yaml_module)
        if status == "tool-missing":
            skipped.append(f"skipped:tool-missing:{rel}")
            findings.append(_finding("yaml-skipped", rel, "info", "skipped:tool-missing: PyYAML is not importable, so this YAML descriptor was not linted", rel))
            continue
        scanned += 1
        if status != "ok":
            findings.append(_finding("parse-error", rel, "blocker", f"descriptor does not parse: {status[len('parse-error:'):]}", rel))
            continue
        docs = [d for d in docs if d is not None]
        if not docs:
            findings.append(_finding("descriptor-empty", rel, "major", "file holds no entity document", rel))
        for number, doc in enumerate(docs, start=1):
            location = rel if len(docs) == 1 else f"{rel}#doc{number}"
            if not isinstance(doc, dict) or not _text(doc.get("kind")) or not isinstance(doc.get("metadata"), dict):
                findings.append(_finding("descriptor-invalid", location, "major", "document is not an entity: kind and metadata are required", location))
                continue
            api_version = _text(doc.get("apiVersion"))
            subject = a17_tools._label(f"{_text(doc.get('kind')).lower()}:{_text(doc['metadata'].get('name')).lower()}", location)
            if not api_version:
                findings.append(_finding("apiversion-missing", subject, "major", "apiVersion is required", location))
            elif not api_version.startswith(API_VERSION_PREFIXES):
                findings.append(_finding("apiversion-unknown", subject, "minor", "apiVersion is not a Backstage catalog apiVersion (backstage.io/... or scaffolder.backstage.io/...)", location))
            entry = _flatten(doc)
            spec = doc.get("spec")
            depends = spec.get("dependsOn") if isinstance(spec, dict) else None
            if depends is not None and not (isinstance(depends, list) and all(isinstance(item, str) for item in depends)):
                findings.append(_finding("dependson-invalid", subject, "major", "dependsOn must be a list of entity references: a value that is not a reference string is not checked", location))
            entries.append(entry)
            where.append(location)
            ref = a17_tools._parse_ref(f"{entry['kind']}:{entry['namespace'] or 'default'}/{entry['name']}", "component")
            if ref is not None:
                refs.setdefault(a17_tools._ref_text(ref), location)
    for index, finding in a17_tools._lint_entries(entries, tiers):
        findings.append({**finding, "location": where[index]})
    if entries and len(entries) <= MAX_GRAPH_ENTRIES:
        graph, graph_reason = _graph_findings(entries, refs)
        findings.extend(graph)
        if graph_reason:
            incomplete_reasons.append(graph_reason)
    elif len(entries) > MAX_GRAPH_ENTRIES:
        findings.append(_finding("graph-skipped", "", "info", f"dependency graph check skipped: {len(entries)} entities exceed {MAX_GRAPH_ENTRIES}", "."))
        incomplete_reasons.append(f"dependency graph check skipped: {len(entries)} entities exceed {MAX_GRAPH_ENTRIES}")
    if truncated:
        findings.append(_finding("scan-truncated", "", "minor", f"scan stopped at the bound ({MAX_DESCRIPTORS} descriptors or {MAX_VISITED} files)", "."))
        incomplete_reasons.append(f"scan stopped at the bound ({MAX_DESCRIPTORS} descriptors or {MAX_VISITED} files)")
    if not files:
        findings.append(_finding("no-descriptors", "", "info", "no catalog-info descriptor found", "."))
    for incomplete in incomplete_findings([*skipped, *incomplete_reasons], scanned):
        findings.append({**incomplete, "summary": incomplete["summary"][: a17_tools.MAX_SUMMARY], "location": "."})
    findings.sort(key=lambda f: (a17_tools.SEVERITY_RANK[f["severity"]], f["location"], f["id"], f["summary"]))
    blocking = sum(1 for f in findings if f["severity"] in ("blocker", "major"))
    listed = sorted(skipped)
    if len(listed) > MAX_SKIPPED:
        listed = [*listed[:MAX_SKIPPED], f"skipped:more:{len(listed) - MAX_SKIPPED}"]
    return {
        "status": scan_status(findings),
        "scanned_files": scanned,
        "entity_count": len(entries),
        "finding_count": len(findings),
        "findings": a17_tools._cut(findings),
        "skipped": listed,
        "truncated": truncated,
        "summary": f"{scanned} descriptor file(s), {len(entries)} entit(ies), {len(findings)} finding(s), {blocking} major or blocker, {len(skipped)} skipped",
    }


def _graph_findings(entries: list[dict], refs: dict[str, str]) -> tuple[list[dict], str | None]:
    """Cross-file dependsOn findings from the tool's graph analysis, on entities whose references are well-formed. A graph the tool did not complete is a reason, never a silent clean result."""
    graph_entries = []
    for entry in entries:
        parsed = a17_tools._parse_ref(f"{entry['kind']}:{entry['namespace'] or 'default'}/{entry['name']}", "component")
        if parsed is None:
            continue
        deps = [d for d in entry["depends_on"] if a17_tools._parse_ref(d, "component", parsed[1]) is not None]
        graph_entries.append({"ref": a17_tools._ref_text(parsed), "lifecycle": entry["lifecycle"], "owner": entry["owner"], "depends_on": deps})
    unique: dict[str, dict] = {}
    for g in graph_entries:
        unique.setdefault(g["ref"], g)  # duplicates are reported by the lint rules; the first definition wins here
    result = a17_tools.catalog_graph({"entries": list(unique.values())})
    if result.get("state") != "SUCCESS":
        return [], f"dependency graph check did not complete: {result.get('state')}"
    out = []
    for f in result["findings"]:
        rule, _, subject = f["id"].partition("@")
        if rule in GRAPH_RULES:
            out.append({**f, "location": refs.get(subject, ".")})
    return out, None


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "scanned_files": 0, "entity_count": 0, "finding_count": 0, "findings": [], "skipped": [], "truncated": False,
            "summary": "dry-run: plat_catalog_lint would lint catalog-info descriptors under --root/--path",
        }
    target = (ctx.root / args.path).resolve()
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {ctx.root}")
    tiers = tuple(t.strip() for t in args.tiers.split(",") if t.strip())
    if not tiers:
        raise SwarmError(ErrorCode.E_INPUT, "--tiers must list at least one tier")
    return lint_tree(ctx.root, target, tiers)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")
    p.add_argument("--tiers", default=DEFAULT_TIERS, help="comma list of accepted tier label values")


if __name__ == "__main__":
    sys.exit(AgentScript("A17", "plat_catalog_lint", run, description=__doc__, add_args=add_args).main())
