#!/usr/bin/env python3
"""A19 - compare a base and a head event schema file for compatibility and lint AsyncAPI or CloudEvents descriptors found in a repository.

Two jobs, both read-only (nothing is written under --root):
  1. `--base FILE --head FILE`: schema compatibility (JSON Schema or Avro, `--mode backward|forward|full`) through the
     `event_schema_compat` rules of swarm/tools/a19_tools.py.
  2. A bounded scan of --path (default the whole root) for descriptors: AsyncAPI documents (`asyncapi:` key) are linted with
     `event_asyncapi_lint`, CloudEvents samples (`specversion` key) with `event_cloudevents_check`, `.avsc` files are checked for
     a resolvable Avro structure (types, names, fields, symbols; not name syntax, default values or logical-type parameters).
     Secret-shaped values are never echoed into a finding.

Bounds: files over 1 MiB and binary files are skipped, the scan stops after 2000 files, symbolic links are never followed.
YAML descriptors need PyYAML; without it they are reported as `skipped:tool-missing` and JSON handling is unaffected.
Findings use severity info|minor|major|blocker; status is `fail` when any major or blocker exists.
`--dry-run` prints deterministic canned output and reads nothing.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a19_tools import _safe, asyncapi_lint, cloudevents_check, schema_compat  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_TEXT_CHARS = 200000  # the handlers' input bound
MAX_FINDINGS = 500
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".tox", ".mypy_cache"}
SUFFIXES = {".json", ".yaml", ".yml", ".avsc"}
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
CE_CORE_STRINGS = {"id", "source", "specversion", "type", "datacontenttype", "dataschema", "subject", "time"}
AVRO_KINDS = {"record", "enum", "fixed", "error"}


def _yaml_module():
    """PyYAML when importable, else None (YAML-dependent checks are then reported as skipped:tool-missing)."""
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _bounded_dumps(document, limit: int = MAX_TEXT_CHARS) -> str | None:
    """JSON text of `document`, or None when it exceeds `limit` characters (guards alias expansion in YAML) or is not
    representable as JSON (circular, non-string keys of unsupported types)."""
    parts, size = [], 0
    try:
        for chunk in json.JSONEncoder(default=str).iterencode(document):
            size += len(chunk)
            if size > limit:
                return None
            parts.append(chunk)
    except (TypeError, ValueError):
        return None
    return "".join(parts)


def _read_text(path: Path) -> tuple[str | None, str]:
    """(text, reason): reason is '' on success, else large|binary|unreadable."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, "large"
        raw = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if b"\x00" in raw[:8192]:
        return None, "binary"
    try:
        return raw.decode("utf-8"), ""
    except UnicodeDecodeError:
        return None, "binary"


def _parse(path: Path, text: str):
    """(document, error). error is 'tool-missing' for YAML without PyYAML, 'invalid' for a parse failure, else ''."""
    if path.suffix.lower() in (".yaml", ".yml"):
        yaml = _yaml_module()
        if yaml is None:
            return None, "tool-missing"
        try:
            return yaml.safe_load(text), ""
        except yaml.YAMLError:
            return None, "invalid"
    try:
        return json.loads(text), ""
    except ValueError:
        return None, "invalid"


def _walk(start: Path, stats: dict):
    """Candidate files under `start` in sorted order, bounded by MAX_FILES visited files; symlinks are never followed."""
    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        keep = []
        for name in sorted(dirnames):
            if name in SKIP_DIRS:
                continue
            if (Path(dirpath) / name).is_symlink():
                stats["skipped_symlink"] += 1
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            if stats["files_visited"] >= MAX_FILES:
                stats["truncated"] = True
                return
            stats["files_visited"] += 1
            path = Path(dirpath) / name
            if path.is_symlink():
                stats["skipped_symlink"] += 1
            elif path.suffix.lower() in SUFFIXES:
                yield path


def _finding(severity: str, rule: str, summary: str, location: str) -> dict:
    return {"severity": severity, "rule": rule, "summary": summary[:300], "location": location}


def _from_handler(result: dict, rule: str, location: str, findings: list) -> bool:
    """Turn a handler result into findings; returns True when the handler produced SUCCESS."""
    if result.get("state") != "SUCCESS":
        findings.append(_finding("blocker", rule, f"{result.get('state')}: {result.get('message', result.get('field', ''))}", location))
        return False
    findings.extend(_finding(f["severity"], rule, f["summary"], location) for f in result["findings"])
    return True


def _ce_attributes(event: dict, location: str, findings: list) -> list[dict] | None:
    attributes = []
    if len(event) > 100:
        findings.append(_finding("major", "cloudevents-attribute-count", "more than 100 attributes: not validated", location))
        return None
    for name in sorted((k for k in event if k not in ("data", "data_base64")), key=str):  # YAML keys need not be strings
        value = event[name]
        if not isinstance(name, str):
            findings.append(_finding("blocker", "cloudevents-attribute-name",
                                     f"attribute name must be a string, not {type(name).__name__}", location))
            continue
        label = repr(_safe(name, 40))
        if not 0 < len(name) <= 64:
            findings.append(_finding("blocker", "cloudevents-attribute-name", f"attribute name {label} is empty or longer than 64", location))
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, date) and name == "time":
            text = value.isoformat()  # an unquoted YAML timestamp is a string in the document, parsed by PyYAML into a date
        elif isinstance(value, (int, float, str)):
            text = str(value)
        else:
            findings.append(_finding("blocker", "cloudevents-attribute-type",
                                     f"attribute {label} must be a string, boolean or number, not {type(value).__name__}", location))
            continue
        if name in CE_CORE_STRINGS and not isinstance(value, (str, date)):
            findings.append(_finding("major", "cloudevents-attribute-type", f"core attribute {label} must be a string", location))
        if len(text) > 2048:
            findings.append(_finding("minor", "cloudevents-attribute-size", f"attribute {label} longer than 2048 characters: not validated", location))
            continue
        attributes.append({"name": name, "value": text})
    return attributes or None


def _lint_cloudevent(event: dict, location: str, findings: list) -> None:
    attributes = _ce_attributes(event, location, findings)
    if attributes is None:
        return
    result = cloudevents_check({"attributes": attributes, "has_data": "data" in event or "data_base64" in event})
    _from_handler(result, "cloudevents", location, findings)


def _scan_document(document, rel: str, stats: dict, findings: list) -> None:
    if isinstance(document, dict) and isinstance(document.get("asyncapi"), (str, int, float)):
        stats["asyncapi"] += 1
        text = _bounded_dumps(document)
        if text is None:
            findings.append(_finding("minor", "asyncapi-size", f"document exceeds {MAX_TEXT_CHARS} characters: not linted", rel))
        else:
            _from_handler(asyncapi_lint({"document": text}), "asyncapi", rel, findings)
    elif isinstance(document, dict) and "specversion" in document:
        stats["cloudevents"] += 1
        _lint_cloudevent(document, rel, findings)
    elif isinstance(document, list) and document and all(isinstance(e, dict) and "specversion" in e for e in document):
        for index, event in enumerate(document[:200]):
            stats["cloudevents"] += 1
            _lint_cloudevent(event, f"{rel}[{index}]", findings)


def scan(root: Path, start: Path) -> tuple[dict, list, list]:
    stats = {"files_visited": 0, "candidates": 0, "asyncapi": 0, "cloudevents": 0, "avro": 0, "skipped_large": 0,
             "skipped_binary": 0, "skipped_symlink": 0, "unparsable": 0, "truncated": False}
    findings: list[dict] = []
    skipped: list[dict] = []
    yaml_missing = 0
    for path in _walk(start, stats):
        rel = path.relative_to(root).as_posix()
        text, reason = _read_text(path)
        if text is None:
            stats["skipped_large" if reason == "large" else "skipped_binary"] += 1
            continue
        avro = path.suffix.lower() == ".avsc"
        if not avro and "asyncapi" not in text and "specversion" not in text:
            continue
        stats["candidates"] += 1
        document, error = _parse(path, text)
        if error == "tool-missing":
            yaml_missing += 1
            continue
        if error:
            stats["unparsable"] += 1
            if avro:
                findings.append(_finding("blocker", "avro-invalid", "the .avsc file is not valid JSON", rel))
            continue
        if avro:
            stats["avro"] += 1
            if len(text) > MAX_TEXT_CHARS:
                findings.append(_finding("minor", "avro-size", f"schema exceeds {MAX_TEXT_CHARS} characters: not validated", rel))
            else:
                result = schema_compat({"format": "avro", "mode": "backward", "base_schema": text, "head_schema": text})
                if result.get("state") != "SUCCESS":
                    findings.append(_finding("blocker", "avro-invalid", f"not a valid Avro schema: {result.get('message')}", rel))
        else:
            _scan_document(document, rel, stats, findings)
    if yaml_missing:
        skipped.append({"check": "yaml-descriptors", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": yaml_missing})
        findings.append(_finding("info", "tool-missing", f"skipped:tool-missing: PyYAML is not installed, {yaml_missing} YAML descriptor "
                                                         "candidate(s) were not checked", "."))
    if stats["truncated"]:
        findings.append(_finding("info", "scan-truncated", f"the scan stopped after {MAX_FILES} files", "."))
    return stats, findings, skipped


def _inside(root: Path, value: str, flag: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"{flag} {value!r} leaves the repository root")
    if not path.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"{flag} {value!r} does not exist under {root}")
    return path


def _detect_format(path: Path, document) -> str:
    if path.suffix.lower() == ".avsc":
        return "avro"
    if isinstance(document, (str, list)):
        return "avro"
    if isinstance(document, dict) and (document.get("type") in AVRO_KINDS or "fields" in document):
        return "avro"
    return "json-schema"


def compare(root: Path, args, findings: list, skipped: list) -> dict:
    base_path, head_path = _inside(root, args.base, "--base"), _inside(root, args.head, "--head")
    rels = (base_path.relative_to(root).as_posix(), head_path.relative_to(root).as_posix())
    location = f"{rels[0]} -> {rels[1]}"
    report = {"performed": False, "mode": args.mode, "format": "", "backward_compatible": False, "forward_compatible": False,
              "compatible": False, "base": rels[0], "head": rels[1]}
    documents = []
    for path, rel in ((base_path, rels[0]), (head_path, rels[1])):
        if not path.is_file():
            raise SwarmError(ErrorCode.E_INPUT, f"{rel} is not a file")
        text, reason = _read_text(path)
        if text is None:
            raise SwarmError(ErrorCode.E_INPUT, f"{rel} cannot be compared: file is {reason}")
        document, error = _parse(path, text)
        if error == "tool-missing":
            skipped.append({"check": "schema-compat", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 1})
            findings.append(_finding("info", "tool-missing", f"skipped:tool-missing: PyYAML is needed to read {rel}", rel))
            return report
        if error:
            findings.append(_finding("blocker", "schema-invalid", f"{rel} is not parseable", rel))
            return report
        documents.append(document)
    fmt = args.format if args.format != "auto" else _detect_format(base_path, documents[0])
    texts = [_bounded_dumps(d) for d in documents]
    if any(t is None for t in texts):
        findings.append(_finding("major", "schema-too-large", f"a schema exceeds {MAX_TEXT_CHARS} characters: not compared", location))
        return report
    result = schema_compat({"format": fmt, "mode": args.mode, "base_schema": texts[0], "head_schema": texts[1]})
    if _from_handler(result, "schema-compat", location, findings):
        report.update({"performed": True, "format": fmt, "backward_compatible": result["backward_compatible"],
                       "forward_compatible": result["forward_compatible"], "compatible": result["compatible"]})
    return report


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True,
                "scanned": {"files_visited": 0, "candidates": 0, "asyncapi": 0, "cloudevents": 0, "avro": 0, "skipped_large": 0,
                            "skipped_binary": 0, "skipped_symlink": 0, "unparsable": 0, "truncated": False},
                "comparison": {"performed": False, "mode": args.mode, "format": "", "backward_compatible": True,
                               "forward_compatible": True, "compatible": True, "base": "", "head": ""},
                "skipped": [], "findings": [],
                "summary": "dry-run: event_schema_check would compare --base with --head and lint AsyncAPI/CloudEvents descriptors under --path"}
    root = ctx.root
    start = _inside(root, args.path, "--path")
    if not start.is_dir():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} is not a directory")
    if bool(args.base) != bool(args.head):
        raise SwarmError(ErrorCode.E_INPUT, "--base and --head must be given together")
    findings: list[dict] = []
    skipped: list[dict] = []
    comparison = compare(root, args, findings, skipped) if args.base else {"performed": False, "mode": args.mode, "format": "",
                                                                          "backward_compatible": False, "forward_compatible": False,
                                                                          "compatible": False, "base": "", "head": ""}
    stats, scan_findings, scan_skipped = scan(root, start)
    findings += scan_findings
    skipped += scan_skipped
    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], f["location"], f["rule"], f["summary"]))
    dropped = max(0, len(findings) - MAX_FINDINGS)
    findings = findings[:MAX_FINDINGS]
    if dropped:
        findings.append(_finding("info", "findings-truncated", f"{dropped} further findings omitted", "."))
    for number, finding in enumerate(findings, 1):
        finding["id"] = f"EV-{number:03d}"
    failing = any(f["severity"] in ("blocker", "major") for f in findings)
    return {"status": "fail" if failing else "ok", "scanned": stats, "comparison": comparison, "skipped": skipped,
            "findings": findings,
            "summary": f"{stats['asyncapi']} AsyncAPI, {stats['cloudevents']} CloudEvents, {stats['avro']} Avro file(s) checked; "
                       f"{len(findings)} finding(s)"
                       + (f"; schema compatibility ({comparison['mode']}): {'compatible' if comparison['compatible'] else 'INCOMPATIBLE'}"
                          if comparison["performed"] else "")}


def add_args(p) -> None:
    p.add_argument("--base", default="", help="base (old) schema file under --root")
    p.add_argument("--head", default="", help="head (new) schema file under --root")
    p.add_argument("--mode", choices=["backward", "forward", "full"], default="backward", help="compatibility direction to enforce")
    p.add_argument("--format", choices=["auto", "json-schema", "avro"], default="auto", help="schema language of --base/--head")
    p.add_argument("--path", default=".", help="directory under --root scanned for AsyncAPI/CloudEvents/Avro descriptors")


if __name__ == "__main__":
    sys.exit(AgentScript("A19", "event_schema_check", run, description=__doc__, add_args=add_args).main())
