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
Findings use severity info|minor|major|blocker. A skipped, truncated or unparsable input adds a major `scan-incomplete`
finding, so status is `fail` until every in-scope input is analysed (the shared rule in swarm/scan_status.py).
`--dry-run` prints deterministic canned output and reads nothing.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a19_tools import ASYNCAPI_WALK_STOPPED, _safe, asyncapi_lint, cloudevents_check, schema_compat  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_TEXT_CHARS = 200000  # the handlers' input bound
MAX_FINDINGS = 500
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".tox", ".mypy_cache"}
SUFFIXES = {".json", ".yaml", ".yml", ".avsc"}
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
CE_CORE_STRINGS = {"id", "source", "specversion", "type", "datacontenttype", "dataschema", "subject", "time"}
AVRO_KINDS = {"record", "enum", "fixed", "error"}
AVRO_ONLY_TYPES = {"int", "long", "float", "double", "bytes"}  # Avro primitives that are not JSON Schema types
MAX_CLOUDEVENTS_PER_ARRAY = 200
MAX_DETECT_DEPTH = 100
_UNREAD = {"large": f"larger than {MAX_FILE_BYTES} bytes", "binary": "binary", "unreadable": "unreadable"}


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
    except (TypeError, ValueError, RecursionError):  # nesting past the encoder's stack is not representable either
        return None
    return "".join(parts)


def _read_text(path: Path) -> tuple[str | None, str]:
    """(text, reason): reason is '' on success, else large|binary|unreadable. A FIFO or device is unreadable, never opened."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):  # a FIFO or device named *.json would block the read forever
            return None, "unreadable"
        if info.st_size > MAX_FILE_BYTES:
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
    except (ValueError, RecursionError):  # nesting too deep for the parser is an input problem, not a crash
        return None, "invalid"


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _walk(root: Path, start: Path, stats: dict, reasons: list):
    """Candidate files under `start` in sorted order, bounded by MAX_FILES visited files. Symlinks are never followed; each
    skipped symlink and a stopped scan is a reason."""

    def unlistable(error: OSError) -> None:  # a directory that cannot be listed is never read: a reason, never a silent pass
        reasons.append(f"{_rel(root, Path(error.filename or start))}: directory could not be listed ({type(error).__name__}); its files were not scanned")

    for dirpath, dirnames, filenames in os.walk(start, followlinks=False, onerror=unlistable):
        keep = []
        for name in sorted(dirnames):
            if name in SKIP_DIRS:
                continue
            if (Path(dirpath) / name).is_symlink():
                stats["skipped_symlink"] += 1
                reasons.append(f"{_rel(root, Path(dirpath) / name)}: symbolic link not followed")
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            if stats["files_visited"] >= MAX_FILES:
                stats["truncated"] = True
                reasons.append(f"scan stopped after {MAX_FILES} files")
                return
            stats["files_visited"] += 1
            path = Path(dirpath) / name
            if path.is_symlink():
                stats["skipped_symlink"] += 1
                reasons.append(f"{_rel(root, path)}: symbolic link not followed")
            elif path.suffix.lower() in SUFFIXES:
                yield path


_SUMMARY_MAX = 300  # the summary maxLength the registered A19 tools declare for findings (agents.json)


class _Stop(str):
    """A walk-stop reason held apart from the per-file reasons. `file` is the scanned file's name, taken from the scan itself, never
    from splitting the text: a name that holds ': ' is still measured as the name (R9-1). The text is the stop sentence alone."""

    def __new__(cls, file: str, sentence: str):
        self = super().__new__(cls, f"{file}: {sentence}")
        self.file, self.sentence = file, sentence
        return self


def _incomplete_prefix_len() -> int:
    """Length of the shared scan-incomplete prefix, read from the shared rule so that the label budget follows it."""
    return len(incomplete_findings(["x"], 1)[0]["summary"]) - 1


def _fit_label(file: str, sentence: str, others: list) -> str:
    """The file label of a stop reason, the longest head-and-tail form of the name for which the shared rule's summary, with this stop
    and `others` in it, stays within _SUMMARY_MAX. A name that fits is kept whole. The search measures the rule, so no offset can drift
    (R7-2, R8-1, R9-1)."""
    def summary_with(label: str) -> int:
        return _joined_length([*others, f"{label}: {sentence}"])

    if summary_with(file) <= _SUMMARY_MAX:
        return file
    lo, hi = 0, len(file)
    best = None
    while lo <= hi:
        keep = (lo + hi) // 2
        head = (keep + 1) // 2
        label = f"{file[:head]}…{file[len(file) - (keep - head):]}" if keep >= 1 else "…"
        if summary_with(label) <= _SUMMARY_MAX:
            best = label
            lo = keep + 1
        else:
            hi = keep - 1
    return best if best is not None else "…"


def _joined_length(reasons: list) -> int:
    """The length the shared rule gives the summary for these reasons, measured by the rule itself (R9-2)."""
    return len(incomplete_findings(reasons, 1)[0]["summary"])


def _count_clause(omitted: int) -> list:
    """The count of items the summary cannot show, in one neutral clause for stops and per-file reasons alike (R11-2)."""
    return [f"and {omitted} more"] if omitted else []


def _render_stops(stops: list, others: list) -> list:
    """The reasons as the summary shows them, sized against the shared rule's own summary: the walk stops first, then the per-file reasons.
    The count clause is measured before any item is accepted, the first stop included, so the clause is never cut (R10-1). The first stop
    keeps its whole sentence and the largest label that leaves room for the next stop's short form. Each later stop is shown whole when it
    fits, else as its file and reason kind, else counted. A per-file reason that does not fit is counted in the same clause (R9-2, R10-2)."""
    def short(stop) -> str:
        kind = stop.sentence.split(" under ")[0] if " under " in stop.sentence else stop.sentence.split(" after ")[0]
        return f"{stop.file}: {kind}"

    shown: list = []
    total = len(stops) + len(others)
    for index, stop in enumerate(stops):
        reserve = _count_clause(total - len(shown) - 1)
        next_short = [short(stops[index + 1])] if index + 1 < len(stops) else []
        whole_plain = f"{stop.file}: {stop.sentence}"
        room_for_next = next_short if _joined_length([*shown, whole_plain, *next_short, *reserve]) <= _SUMMARY_MAX else []
        whole = f"{_fit_label(stop.file, stop.sentence, [*shown, *room_for_next, *reserve])}: {stop.sentence}"
        if _joined_length([*shown, whole, *reserve]) <= _SUMMARY_MAX:
            shown.append(whole)
            continue
        if _joined_length([*shown, short(stop), *reserve]) <= _SUMMARY_MAX:
            shown.append(short(stop))
            continue
        break
    for reason in others:
        reserve = _count_clause(total - len(shown) - 1)
        if _joined_length([*shown, reason, *reserve]) <= _SUMMARY_MAX:
            shown.append(reason)
        else:
            break
    return [*shown, *_count_clause(total - len(shown))]


def _stops_first(reasons: list) -> list:
    """The reasons for the shared rule: the walk stops first, then the per-file reasons, sized so the summary keeps the stop sentences whole
    and counts whatever it cannot show (R8-2, R10-2). The status is computed from the full list by the caller, so ordering changes what is
    shown, not whether the scan fails."""
    stops = [reason for reason in reasons if isinstance(reason, _Stop)]
    others = [str(reason) for reason in reasons if not isinstance(reason, _Stop)]
    return _render_stops(stops, others)


def _finding(severity: str, rule: str, summary: str, location: str) -> dict:
    return {"severity": severity, "rule": rule, "summary": summary[:_SUMMARY_MAX], "location": location}


def _from_handler(result: dict, rule: str, location: str, findings: list) -> bool:
    """Turn a handler result into findings; returns True when the handler produced SUCCESS."""
    if result.get("state") != "SUCCESS":
        findings.append(_finding("blocker", rule, f"{result.get('state')}: {result.get('message', result.get('field', ''))}", location))
        return False
    findings.extend(_finding(f["severity"], rule, f["summary"], location) for f in result["findings"])
    return True


def _ce_attributes(event: dict, location: str, findings: list, reasons: list) -> list[dict] | None:
    attributes = []
    if len(event) > 100:
        findings.append(_finding("major", "cloudevents-attribute-count", "more than 100 attributes: not validated", location))
        reasons.append(f"{location}: more than 100 attributes, not validated")
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
            reasons.append(f"{location}: attribute {label} longer than 2048 characters, not validated")
            continue
        attributes.append({"name": name, "value": text})
    return attributes or None


_CE_REQUIRED_NAMES = ("id", "source", "specversion", "type")


def _present_as_scalar(value) -> bool:
    return isinstance(value, (bool, int, float, str))


def _lint_cloudevent(event: dict, location: str, findings: list, reasons: list) -> bool:
    attributes = _ce_attributes(event, location, findings, reasons)
    if attributes is None:
        return False
    result = cloudevents_check({"attributes": attributes, "has_data": "data" in event or "data_base64" in event})
    # a required attribute that is present with a non-scalar value gets its type message only, never a 'missing' message (R7-3)
    present_invalid = {f"required attribute {name!r} is missing" for name in _CE_REQUIRED_NAMES
                       if name in event and not _present_as_scalar(event[name])}
    if present_invalid:
        result = {**result, "findings": [f for f in result.get("findings", []) if f.get("summary") not in present_invalid]}
    _from_handler(result, "cloudevents", location, findings)
    return True


def _scan_document(document, rel: str, stats: dict, findings: list, reasons: list) -> bool:
    """Lint one parsed descriptor; True when it was handed to a handler. A descriptor with the structure of a candidate that
    cannot be analysed adds a reason naming the file. A document with no such structure is out of scope and stays silent."""
    if isinstance(document, dict) and "asyncapi" in document:
        stats["asyncapi"] += 1
        if not isinstance(document["asyncapi"], (str, int, float)) or isinstance(document["asyncapi"], bool):
            reasons.append(f"{rel}: asyncapi value is not a version string, not analysed")
            return False
        text = _bounded_dumps(document)
        if text is None:
            findings.append(_finding("minor", "asyncapi-size", f"document exceeds {MAX_TEXT_CHARS} characters: not linted", rel))
            reasons.append(f"{rel}: document exceeds {MAX_TEXT_CHARS} characters, not linted")
            return False
        result = asyncapi_lint({"document": text})
        _from_handler(result, "asyncapi", rel, findings)
        stopped = [finding["summary"] for finding in result.get("findings", [])
                   if finding["summary"].startswith(ASYNCAPI_WALK_STOPPED)]
        if stopped:  # the stop names the chain or budget it reached: the file is not fully analysed
            reasons.append(_Stop(rel, stopped[0]))
            return False
        return True
    return _scan_cloudevents(document, rel, stats, findings, reasons)


_CE_NODE_BUDGET = 200000  # nodes walked per document when searching for CloudEvents at any depth
_SCHEMA_KEYWORD_MAPS = ("properties", "patternProperties", "definitions", "$defs")  # keyword maps, excluded only when schema maps
_CE_ATTRIBUTE_KEYS = ("specversion", "id", "source", "type")  # the CloudEvents attributes; a string or number value marks event data


def _text_or_number(value) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


_JSON_SCHEMA_TYPE_NAMES = frozenset({"string", "number", "integer", "boolean", "object", "array", "null"})


def _carries_attribute(node: dict) -> bool:
    """A CloudEvents attribute with a string or number value. A `type` value that is a JSON Schema type name is a schema keyword, not an
    event type, so it does not count (R7-1); a reverse-DNS event type does."""
    return any(_text_or_number(node.get(key)) and not (key == "type" and node.get(key) in _JSON_SCHEMA_TYPE_NAMES)
               for key in _CE_ATTRIBUTE_KEYS)


def _is_event_data(entry) -> bool:
    """An object that carries a CloudEvents attribute: event data, not schema (R7-1). A boolean, an object or a list under an attribute
    name is schema-like."""
    return isinstance(entry, dict) and _carries_attribute(entry)


def _is_schema_map(value) -> bool:
    """A JSON Schema keyword map (N-8, R6-2, R7-1): no entry is event data, and the map itself does not carry a CloudEvents attribute, so
    an event held under a key named like a keyword is still a candidate. Entries that are booleans or schema-like objects are a schema
    map, whatever keywords they use."""
    if not isinstance(value, dict) or not value:
        return False
    if _carries_attribute(value):
        return False
    return not any(_is_event_data(entry) for entry in value.values())


def _scalar(node: dict, key: str) -> bool:
    return key in node and not isinstance(node[key], (dict, list))


def _is_cloudevent(node, top: bool) -> bool:
    """A mapping with a specversion key is a CloudEvent at the root. Below the root it is one when its specversion is a string (S-4),
    or a scalar with at least one scalar among id, type and source (N-7, R6-3). A schema whose properties include specversion gives
    mappings or booleans for these, so it is not an event (S-5); a schema keyword map is excluded by _is_schema_map (N-8, R6-2)."""
    if not isinstance(node, dict) or "specversion" not in node:
        return False
    if top or isinstance(node["specversion"], str):
        return True
    return _scalar(node, "specversion") and (_scalar(node, "id") or _scalar(node, "type") or _scalar(node, "source"))


def _where(rel: str, pointer: str) -> str:
    """The finding location: the file, and the RFC 6901 JSON pointer of the event inside it when it is not the root."""
    return rel if pointer == "" else f"{rel}#{pointer}"


def _token(key) -> str:
    """One RFC 6901 reference token: the key with `~` and `/` escaped."""
    return str(key).replace("~", "~0").replace("/", "~1")


def _scan_cloudevents(document, rel: str, stats: dict, findings: list, reasons: list) -> bool:
    """Lint every CloudEvents mapping at any depth, each at its JSON pointer, and every array of them. Nothing below a CloudEvents
    mapping is searched. A child of a JSON Schema keyword map is a schema, never an event, so it is not judged; its own children are
    searched as usual. True when at least one CloudEvent was handed to the CloudEvents check."""
    analysed = False
    budget = _CE_NODE_BUDGET
    stack = [("", document, False)]  # (pointer, node, whether the node is a child of a schema keyword map)
    while stack:
        pointer, node, schema_child = stack.pop()
        budget -= 1
        if budget < 0:
            reasons.append(f"{rel}: more than {_CE_NODE_BUDGET} nodes, the search for CloudEvents stopped, not analysed")
            break
        if isinstance(node, dict):
            if not schema_child and _is_cloudevent(node, top=pointer == ""):
                stats["cloudevents"] += 1
                _lint_cloudevent(node, _where(rel, pointer), findings, reasons)
                analysed = True
            else:
                for key, value in node.items():
                    child = f"{pointer}/{_token(key)}"
                    if key in _SCHEMA_KEYWORD_MAPS and _is_schema_map(value):
                        stack.extend((f"{child}/{_token(name)}", entry, True) for name, entry in value.items())
                    else:
                        stack.append((child, value, False))
        elif isinstance(node, list):
            flags = [_is_cloudevent(entry, top=pointer == "") for entry in node]
            if not any(flags):
                stack.extend((f"{pointer}/{index}", entry, False) for index, entry in enumerate(node))
            elif not all(flags):
                reasons.append(f"{_where(rel, pointer)}: an array mixes CloudEvents with entries that are not CloudEvents, not analysed")
            else:
                if len(node) > MAX_CLOUDEVENTS_PER_ARRAY:
                    findings.append(_finding("major", "cloudevents", f"more than {MAX_CLOUDEVENTS_PER_ARRAY} events: not validated",
                                             _where(rel, pointer)))
                    reasons.append(f"{_where(rel, pointer)}: {len(node)} events in one array, events after the first "
                                   f"{MAX_CLOUDEVENTS_PER_ARRAY} not validated")
                for index, event in enumerate(node[:MAX_CLOUDEVENTS_PER_ARRAY]):
                    stats["cloudevents"] += 1
                    _lint_cloudevent(event, _where(rel, f"{pointer}/{index}"), findings, reasons)
                analysed = True
    return analysed


def scan(root: Path, start: Path, reasons: list) -> tuple[dict, list, list, int]:
    """(stats, findings, skipped, analysed). Each input that was skipped, truncated or not parseable is appended to `reasons`
    as a label and a path only, never as its content."""
    stats = {"files_visited": 0, "candidates": 0, "asyncapi": 0, "cloudevents": 0, "avro": 0, "skipped_large": 0,
             "skipped_binary": 0, "skipped_symlink": 0, "unparsable": 0, "truncated": False}
    findings: list[dict] = []
    skipped: list[dict] = []
    analysed = 0
    yaml_missing = 0
    for path in _walk(root, start, stats, reasons):
        rel = _rel(root, path)
        text, reason = _read_text(path)
        if text is None:
            stats["skipped_large" if reason == "large" else "skipped_binary"] += 1
            reasons.append(f"{rel}: {_UNREAD[reason]}, not analysed")
            continue
        avro = path.suffix.lower() == ".avsc"
        if not avro and "asyncapi" not in text and "specversion" not in text:
            continue
        stats["candidates"] += 1
        document, error = _parse(path, text)
        if error == "tool-missing":
            yaml_missing += 1
            reasons.append(f"{rel}: PyYAML is not installed, not analysed")
            continue
        if error:
            stats["unparsable"] += 1
            reasons.append(f"{rel}: not parseable, not analysed")
            if avro:
                findings.append(_finding("blocker", "avro-invalid", "the .avsc file is not valid JSON", rel))
            continue
        if avro:
            stats["avro"] += 1
            if len(text) > MAX_TEXT_CHARS:
                findings.append(_finding("minor", "avro-size", f"schema exceeds {MAX_TEXT_CHARS} characters: not validated", rel))
                reasons.append(f"{rel}: schema exceeds {MAX_TEXT_CHARS} characters, not validated")
            else:
                result = schema_compat({"format": "avro", "mode": "backward", "base_schema": text, "head_schema": text})
                analysed += 1
                if result.get("state") != "SUCCESS":
                    findings.append(_finding("blocker", "avro-invalid", f"not a valid Avro schema: {result.get('message')}", rel))
        elif _scan_document(document, rel, stats, findings, reasons):
            analysed += 1
    if yaml_missing:
        skipped.append({"check": "yaml-descriptors", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": yaml_missing})
        findings.append(_finding("info", "tool-missing", f"skipped:tool-missing: PyYAML is not installed, {yaml_missing} YAML descriptor "
                                                         "candidate(s) were not checked", "."))
    if stats["truncated"]:
        findings.append(_finding("info", "scan-truncated", f"the scan stopped after {MAX_FILES} files", "."))
    return stats, findings, skipped, analysed


def _inside(root: Path, value: str, flag: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"{flag} {value!r} leaves the repository root")
    if not path.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"{flag} {value!r} does not exist under {root}")
    return path


def _looks_avro(node, depth: int = 0) -> bool:
    """True when only Avro can be this schema. A JSON Schema root is an object or a boolean, so a string or an array root is
    Avro. Inside a schema, an array of JSON Schema items or a `type` list is not Avro; a string under `items`, or a list of
    items holding a string (an Avro union), is, because JSON Schema has no such item. Avro-only primitives, maps, records,
    enums and fixed types are Avro at any depth."""
    if depth == 0 and isinstance(node, (str, list)):
        return True
    if not isinstance(node, dict) or depth > MAX_DETECT_DEPTH:
        return False
    kind = node.get("type")
    if isinstance(kind, str) and kind in AVRO_KINDS | AVRO_ONLY_TYPES | {"map"}:
        return True
    if "fields" in node:
        return True
    return kind == "array" and "items" in node and _avro_items(node["items"], depth + 1)


def _avro_items(items, depth: int) -> bool:
    """The `items` of an array: a string is Avro; a list is a tuple of JSON Schemas or an Avro union, and is Avro when one of
    its entries is a string or an Avro-shaped object."""
    if isinstance(items, str):
        return True
    if isinstance(items, list):
        return any(isinstance(entry, str) or (isinstance(entry, dict) and _looks_avro(entry, depth + 1)) for entry in items)
    return isinstance(items, dict) and _looks_avro(items, depth)


def _detect_format(path: Path, document) -> str:
    if path.suffix.lower() == ".avsc":
        return "avro"
    return "avro" if _looks_avro(document) else "json-schema"


def compare(root: Path, args, findings: list, skipped: list, reasons: list) -> dict:
    base_path, head_path = _inside(root, args.base, "--base"), _inside(root, args.head, "--head")
    rels = (base_path.relative_to(root).as_posix(), head_path.relative_to(root).as_posix())
    location = f"{rels[0]} -> {rels[1]}"
    report = {"performed": False, "mode": args.mode, "format": "", "backward_compatible": False, "forward_compatible": False,
              "compatible": False, "base": rels[0], "head": rels[1]}
    documents = []
    for path, rel in ((base_path, rels[0]), (head_path, rels[1])):
        if path.is_dir():
            raise SwarmError(ErrorCode.E_INPUT, f"{rel} is not a file")
        if not stat.S_ISREG(path.stat().st_mode):  # a FIFO, device or socket is never opened: it cannot be analysed
            reasons.append(f"{rel}: not a regular file, not analysed")
            return report
        text, reason = _read_text(path)
        if text is None:
            raise SwarmError(ErrorCode.E_INPUT, f"{rel} cannot be compared: file is {reason}")
        document, error = _parse(path, text)
        if error == "tool-missing":
            skipped.append({"check": "schema-compat", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": 1})
            findings.append(_finding("info", "tool-missing", f"skipped:tool-missing: PyYAML is needed to read {rel}", rel))
            reasons.append(f"{rel}: PyYAML is needed to read it, schema comparison not performed")
            return report
        if error:
            findings.append(_finding("blocker", "schema-invalid", f"{rel} is not parseable", rel))
            reasons.append(f"{rel}: not parseable, schema comparison not performed")
            return report
        documents.append(document)
    fmt = args.format if args.format != "auto" else _detect_format(base_path, documents[0])
    texts = [_bounded_dumps(d) for d in documents]
    if any(t is None for t in texts):
        findings.append(_finding("major", "schema-too-large", f"a schema exceeds {MAX_TEXT_CHARS} characters: not compared", location))
        reasons.append(f"{location}: a schema exceeds {MAX_TEXT_CHARS} characters, schema comparison not performed")
        return report
    result = schema_compat({"format": fmt, "mode": args.mode, "base_schema": texts[0], "head_schema": texts[1]})
    if _from_handler(result, "schema-compat", location, findings):
        report.update({"performed": True, "format": fmt, "backward_compatible": result["backward_compatible"],
                       "forward_compatible": result["forward_compatible"], "compatible": result["compatible"]})
    else:
        reasons.append(f"{location}: schema comparison not performed")
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
    reasons: list[str] = []
    comparison = compare(root, args, findings, skipped, reasons) if args.base else {"performed": False, "mode": args.mode, "format": "",
                                                                                  "backward_compatible": False, "forward_compatible": False,
                                                                                  "compatible": False, "base": "", "head": ""}
    stats, scan_findings, scan_skipped, analysed = scan(root, start, reasons)
    findings += scan_findings
    skipped += scan_skipped
    analysed += 1 if comparison["performed"] else 0
    if reasons:
        findings += [_finding(f["severity"], f["rule"], f["summary"], ".") for f in incomplete_findings(_stops_first(reasons), analysed)]
    findings.sort(key=lambda f: (SEVERITY_RANK[f["severity"]], f["location"], f["rule"], f["summary"]))
    dropped = max(0, len(findings) - MAX_FINDINGS)
    findings = findings[:MAX_FINDINGS]
    if dropped:
        findings.append(_finding("info", "findings-truncated", f"{dropped} further findings omitted", "."))
    for number, finding in enumerate(findings, 1):
        finding["id"] = f"EV-{number:03d}"
    return {"status": scan_status(findings), "scanned": stats, "comparison": comparison, "skipped": skipped, "findings": findings,
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
