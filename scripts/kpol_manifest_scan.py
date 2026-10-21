#!/usr/bin/env python3
"""A24 - scan Kubernetes manifests for Pod Security Standards violations, wildcard or cluster-admin RBAC and risky service-account settings.

Walks --path under --root for .json, .yaml and .yml files and runs the same rules as the registry tools kpol_pss_check,
kpol_rbac_risk_scan and kpol_sa_token_audit over every Kubernetes object it finds. Bounded: files over 1 MiB, binaries,
symlinks and non-UTF-8 files are skipped, at most 2000 files are examined, symlinks are never followed out of the root.
JSON is read natively; YAML needs PyYAML and is reported as `skipped:tool-missing` when it does not import. No external
binary is required or looked for. Findings use info|minor|major|blocker and `status` is `fail` when any is major or worse, or when any in-scope input was skipped, truncated by a limit or not analysed (a `scan-incomplete` finding lists why).
Read-only: it writes nothing. `--dry-run` prints deterministic canned output and reads nothing.

A mapping is a Kubernetes object when it has a kind key, or an apiVersion shaped like a Kubernetes apiVersion (v1, apps/v1) together
with a body key (metadata, spec, data, ...). A kind-less mapping with an items list is descended into, and each element is judged by
its own identifiers. A mapping with neither a kind nor an apiVersion (a body-only mapping) carries no Kubernetes identifiers and the
API server rejects it, so it is passed over silently.
A generic record with a kind key inside items, or as a single-mapping items value, such as {"kind": "file"}, is a candidate and gets missing-apiVersion: this is the accepted residual of a kind without apiVersion, reached through the items descent.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a24_tools import pss_check, rbac_risk_scan, sa_token_audit  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_SKIPPED_LISTED = 50
MAX_RBAC_OBJECTS = 500
MAX_SERVICE_ACCOUNTS = 200
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".swarm", "vendor", "dist", "build"})
EXTENSIONS = (".json", ".yaml", ".yml")
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
WORKLOAD_KINDS = frozenset({"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController", "Job", "CronJob"})
ROLE_KINDS = frozenset({"Role", "ClusterRole"})
BINDING_KINDS = frozenset({"RoleBinding", "ClusterRoleBinding"})
DRY_RUN = {
    "status": "ok", "dry_run": True, "level": "baseline", "files_scanned": 0, "files_skipped": 0, "manifest_count": 0,
    "tools": {"yaml": "not-checked"}, "skipped": [], "limits_hit": [], "findings": [],
    "summary": "dry-run: kpol_manifest_scan would scan --path under --root for Pod Security, RBAC and service-account findings",
}


def _object_key(manifest: dict) -> str:
    """Kind/namespace/name, or Kind/name when cluster scoped: the location format of the tool findings."""
    meta = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name") else "(unnamed)"
    namespace = meta.get("namespace") if isinstance(meta.get("namespace"), str) else ""
    return f"{manifest['kind']}/{namespace}/{name}" if namespace else f"{manifest['kind']}/{name}"


_KUBERNETES_BODY_KEYS = frozenset({"metadata", "spec", "data", "stringData", "items", "rules", "subjects", "roleRef", "webhooks", "status"})
# a Kubernetes apiVersion: the core group's only version, v1, or a named group (a DNS name, dotted or not) with a version such as
# apps/v1, policy/v1beta1 or autoscaling/v2. A bare v2 is not one: the core group has no version but v1.
_API_VERSION = re.compile(r"v1|[a-z0-9][a-z0-9.-]*/v[0-9]+(?:(?:alpha|beta)[0-9]+)?")


def _is_candidate(doc: dict) -> bool:
    """A mapping with a kind key is a Kubernetes candidate on its own. Without one it is a candidate when its apiVersion is a
    Kubernetes apiVersion (v1, or a named group's version such as apps/v1 or autoscaling/v2) and it has a body key. A Helm
    Chart.yaml (apiVersion v2, no body key), a generic payload (apiVersion "2" beside data) and a bare v2 beside data are not."""
    if "kind" in doc:
        return True
    version = doc.get("apiVersion")
    return isinstance(version, str) and _API_VERSION.fullmatch(version) is not None and not _KUBERNETES_BODY_KEYS.isdisjoint(doc)


def _at(where: str, step: str) -> str:
    """The path of a child: an index joins directly (items[1][0]), a key joins with a dot (items[1].items[0])."""
    if not where:
        return step
    return f"{where}{step}" if step.startswith("[") else f"{where}.{step}"


def _reaches_object(value) -> bool:
    """True when a Kubernetes object sits in value on the paths the scan descends: lists, and the items of a mapping that is not
    itself an object. A depth limit cuts off only what this finds, so generic data never records a limit."""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if _is_candidate(current):
                return True
            current = current.get("items")
            if isinstance(current, dict):
                current = [current]
        if isinstance(current, list):
            stack.extend(current)
    return False


def _reason(rel: str, text: str, where: str) -> str:
    """One skipped reason: its label, and the path of its position unless that is the document root."""
    return f"{rel}: {text} at {where}" if where else f"{rel}: {text}"


def _element_path(where: str, index: int, single: bool) -> str:
    """The path of an element of a mapping's items value. One mapping is the items value itself, so it has no index."""
    return _at(where, "items") if single else _at(where, f"items[{index}]")


def _candidate_item(element, rel: str, skipped: list[str], where: str, depth: int) -> list[dict]:
    """One element of a kind-less candidate's items. A candidate is judged as a document (missing-kind at its path); anything else
    is judged as a List item (a scalar or a kind-less mapping gets its reason at its path)."""
    if isinstance(element, dict) and _is_candidate(element):
        return _flatten(element, rel, skipped, depth, where)
    return _list_item(element, rel, skipped, where, depth)


def _list_item(item, rel: str, skipped: list[str], where: str, depth: int) -> list[dict]:
    """One item of a kind-bearing List, or one element of an array inside one, at the path where. Each item is a Kubernetes object in
    its own right and must carry its own kind: a scalar is a List item that is not an object, and a mapping without a kind is reported
    at its path and never given the List's identity. A kind-less item's items value is descended too, a list element by element or one
    mapping as a single element, each judged by its own kind. A depth cut is reported at the position it cuts, as in _flatten, and
    recorded only when a Kubernetes object is in what was cut off; generic data stays silent."""
    if isinstance(item, list) and depth >= 2:
        if _reaches_object(item):
            skipped.append(_reason(rel, "list-nesting-limit:2", where))
        return []
    if isinstance(item, list):
        out: list[dict] = []
        for index, element in enumerate(item):
            out.extend(_list_item(element, rel, skipped, _at(where, f"[{index}]"), depth + 1))
        return out
    if not isinstance(item, dict):
        skipped.append(_reason(rel, "list-item-not-object", where))
        return []
    if not isinstance(item.get("kind"), str) or not item["kind"]:
        skipped.append(_reason(rel, "list-item-without-kind", where))
        nested = item.get("items")
        single = isinstance(nested, dict)
        if single:
            nested = [nested]  # one mapping is one element, judged by its own identifiers
        if not isinstance(nested, list):
            return []
        if depth >= 2:
            if _reaches_object(nested):
                skipped.append(_reason(rel, "list-nesting-limit:2", _at(where, "items")))
            return []
        found: list[dict] = []
        for index, element in enumerate(nested):
            found.extend(_list_item(element, rel, skipped, _element_path(where, index, single), depth + 1))
        return found
    return _flatten(item, rel, skipped, depth, where)


def _flatten(doc, rel: str, skipped: list[str], depth: int = 0, where: str = "") -> list[dict]:
    """Kubernetes objects of one parsed document, or of the elements of an array, at the path where ('' for the document root).
    Each element is judged by its own identifiers, and the same depth limit applies to arrays and to items. A mapping that is not a
    candidate (see _is_candidate) is passed over silently, except that its items (a list, or one mapping as a single element) is
    descended into; scalar items of such a generic wrapper are generic data and stay silent. A candidate needs a string kind: a
    kind-less candidate records missing-kind at its path, and its items are read as a List's, so a scalar or a kind-less mapping there
    gets its own reason at its path. A missing string apiVersion is reported at its path and the object is still analysed. kind List
    and <Kind>List items are each judged by their own kind (see _list_item). A depth cut is reported at the position it cuts: an
    array's own position, or the items of a mapping (at items, with no index when items is one mapping). The cut is recorded only when
    a Kubernetes object is in what was cut off. Nothing else is dropped silently, so the scan reports it incomplete."""
    if isinstance(doc, list):
        if not doc:
            return []
        if depth >= 2:
            if _reaches_object(doc):
                skipped.append(_reason(rel, "list-nesting-limit:2", where))
            return []
        out: list[dict] = []
        for index, element in enumerate(doc):
            out.extend(_flatten(element, rel, skipped, depth + 1, _at(where, f"[{index}]")))
        return out
    if not isinstance(doc, dict):
        return []
    if not _is_candidate(doc):
        items = doc.get("items")
        single = isinstance(items, dict)
        if single:
            items = [items]  # one mapping is one element, judged by its own identifiers
        if not isinstance(items, list) or not items:
            return []  # no items, or scalar items: generic data, passed over silently
        if depth >= 2:
            if _reaches_object(items):
                skipped.append(_reason(rel, "list-nesting-limit:2", _at(where, "items")))
            return []
        out: list[dict] = []
        for index, element in enumerate(items):
            out.extend(_flatten(element, rel, skipped, depth + 1, _element_path(where, index, single)))
        return out
    kind = doc.get("kind")
    if not isinstance(kind, str) or not kind:
        skipped.append(_reason(rel, "missing-kind", where))
        items = doc.get("items")
        single = isinstance(items, dict)
        if single:
            items = [items]  # one mapping is one element, judged by its own identifiers
        if not isinstance(items, list) or not items:
            return []
        if depth >= 2:
            if _reaches_object(items):
                skipped.append(_reason(rel, "list-nesting-limit:2", _at(where, "items")))
            return []
        found: list[dict] = []
        for index, element in enumerate(items):
            found.extend(_candidate_item(element, rel, skipped, _element_path(where, index, single), depth + 1))
        return found
    version = doc.get("apiVersion")
    if not isinstance(version, str) or not version:
        skipped.append(_reason(rel, "missing-apiVersion", where))
    if kind.endswith("List"):
        if not isinstance(doc.get("items"), list):
            skipped.append(_reason(rel, "list-items-not-a-list", where))
            return []
        if not doc["items"]:
            return []
        if depth >= 2:  # a List is a Kubernetes object itself, so its own cut-off is always reported
            skipped.append(_reason(rel, "list-nesting-limit:2", where))
            return []
        out: list[dict] = []
        for index, item in enumerate(doc["items"]):
            out.extend(_list_item(item, rel, skipped, _at(where, f"items[{index}]"), depth + 1))
        return out
    return [doc]


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _candidates(target: Path, root: Path) -> tuple[list[Path], list[str], list[str]]:
    """Files to read (sorted walk, never through symlinks), what was skipped and any limit that stopped the walk."""
    if target.is_file():
        return ([target] if target.suffix.lower() in EXTENSIONS else []), [], []
    files: list[Path] = []
    skipped: list[str] = []
    limits: list[str] = []

    def walk_error(error: OSError) -> None:
        skipped.append(f"{_rel(Path(error.filename or target), root)}: unreadable-directory")

    for current, dirs, names in os.walk(target, followlinks=False, onerror=walk_error):
        for d in dirs:
            if d in SKIP_DIRS:
                continue
            if (Path(current) / d).is_symlink():
                skipped.append(f"{_rel(Path(current) / d, root)}: symlink-directory")
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(current) / d).is_symlink())
        for name in sorted(names):
            path = Path(current) / name
            if path.suffix.lower() not in EXTENSIONS:
                continue
            if path.is_symlink():
                skipped.append(f"{_rel(path, root)}: symlink")
                continue
            if not path.is_file():  # a FIFO or socket named *.yaml would block the read forever
                skipped.append(f"{_rel(path, root)}: not-a-regular-file")
                continue
            if len(files) >= MAX_FILES:
                limits.append(f"max-files:{MAX_FILES}")
                return files, skipped, limits
            files.append(path)
    return files, skipped, limits


def _read(path: Path) -> str:
    """File text; raises ValueError('too-large'), ('binary') or ('not-utf8') for files that are skipped."""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("too-large")
    with open(path, "rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if b"\x00" in data[:8192]:
        raise ValueError("binary")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("not-utf8") from exc


def _yaml_module():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed per run
    except ImportError:
        return None
    return yaml


MAX_YAML_NODES = 200_000  # expanded node count of one YAML document, aliases counted every time they are used


def _children(node) -> list:
    value = getattr(node, "value", None)
    if not isinstance(value, list):
        return []
    return [part for item in value for part in (item if isinstance(item, tuple) else (item,))]


def _expanded_nodes(root) -> int:
    """Node count of a composed YAML document with every alias expanded (a billion-laughs file is small on disk and huge
    once built). Counted on the node graph, so nothing is built; a recursive anchor raises ValueError."""
    memo: dict[int, int] = {}
    active: set[int] = set()
    stack = [(root, False)]
    while stack:
        node, done = stack.pop()
        key = id(node)
        if done:
            active.discard(key)
            memo[key] = min(1 + sum(memo[id(child)] for child in _children(node)), MAX_YAML_NODES + 1)
            continue
        if key in memo:
            continue
        if key in active:
            raise ValueError("recursive-anchor")
        active.add(key)
        stack.append((node, True))
        stack.extend((child, False) for child in _children(node) if id(child) not in memo)
    return memo[id(root)]


def _plain(value):
    """JSON-shaped copy of a parsed YAML value: keys become strings (the tools index them as text) and dates ISO strings."""
    if isinstance(value, dict):
        return {(key if isinstance(key, str) else str(key)): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _yaml_documents(text: str, yaml) -> list:
    loader = yaml.SafeLoader(text)
    try:
        documents = []
        while loader.check_node():
            node = loader.get_node()
            if _expanded_nodes(node) > MAX_YAML_NODES:
                raise ValueError("too-many-nodes")
            document = loader.construct_document(node)
            if document is not None:
                documents.append(_plain(document))
        return documents
    finally:
        loader.dispose()


def _documents(path: Path, text: str, yaml) -> list:
    """Parsed documents of one file; raises ValueError('parse-error') for unparseable content."""
    errors = (ValueError, RecursionError) + ((yaml.YAMLError,) if yaml is not None else ())
    try:
        if path.suffix.lower() == ".json":
            return [_plain(json.loads(text))]
        return _yaml_documents(text, yaml)
    except errors as exc:
        raise ValueError("parse-error") from exc


def _add(findings: list[dict], result: dict, location: str, key_to_file: dict[str, set[str]] | None = None) -> None:
    """Append a tool result's findings. With key_to_file the file is found through the object key in the finding id; an object
    key defined in several files lists all of them."""
    for finding in result.get("findings", []):
        place = location
        if key_to_file is not None:
            place = ", ".join(sorted(key_to_file.get(finding["id"].partition(":")[2].partition("#")[0], ())))
        findings.append({**finding, "location": place})


def _call(handler, payload: dict, label: str, skipped: list[str]) -> dict:
    """Run a tool handler in-process; a rejected input is recorded as skipped, never raised. The reason carries the state and field only: a message can echo input."""
    try:
        result = handler(payload)
    except (TypeError, AttributeError, ValueError, KeyError, RecursionError) as exc:  # one odd manifest must not end the whole scan
        skipped.append(f"{label}: handler-error {type(exc).__name__}")
        return {}
    if result.get("state") != "SUCCESS":
        skipped.append(f"{label}: {result.get('state')} {result.get('field', '')}".strip())
        return {}
    return result


def scan(root: Path, target: Path, level: str) -> dict:
    files, skipped, limits = _candidates(target, root)
    yaml = _yaml_module()
    findings: list[dict] = []
    workloads: list[tuple[str, dict]] = []
    roles: list[dict] = []
    bindings: list[dict] = []
    accounts: list[dict] = []
    token_secrets: list[dict] = []
    key_to_file: dict[str, set[str]] = {}
    scanned = skipped_yaml = 0
    for path in files:
        rel = _rel(path, root)
        if path.suffix.lower() != ".json" and yaml is None:
            skipped_yaml += 1
            skipped.append(f"{rel}: tool-missing")
            continue
        try:
            text = _read(path)
            documents = _documents(path, text, yaml)
        except (OSError, ValueError) as exc:
            skipped.append(f"{rel}: {exc}")
            continue
        scanned += 1
        for document in documents:
            for manifest in _flatten(document, rel, skipped):
                kind = manifest["kind"]
                key_to_file.setdefault(_object_key(manifest), set()).add(rel)
                if kind in WORKLOAD_KINDS:
                    workloads.append((rel, manifest))
                elif kind in ROLE_KINDS:
                    roles.append(manifest)
                elif kind in BINDING_KINDS:
                    bindings.append(manifest)
                elif kind == "ServiceAccount":
                    accounts.append(manifest)
                elif kind == "Secret" and manifest.get("type") == "kubernetes.io/service-account-token":
                    token_secrets.append(manifest)
    if skipped_yaml:
        findings.append({"id": "skipped:tool-missing", "severity": "info", "location": "",
                         "summary": f"PyYAML is not importable: {skipped_yaml} YAML file(s) were not scanned; JSON manifests were"})
    problems: list[str] = []
    shared_accounts = accounts[:MAX_SERVICE_ACCOUNTS]
    if len(accounts) > MAX_SERVICE_ACCOUNTS:
        limits.append(f"max-service-accounts:{MAX_SERVICE_ACCOUNTS}")
    for rel, manifest in workloads:
        _add(findings, _call(pss_check, {"workloads": [manifest], "level": level}, f"{rel} pss", problems), rel)
        _add(findings, _call(sa_token_audit, {"workloads": [manifest], "service_accounts": shared_accounts}, f"{rel} sa", problems), rel)
    if len(token_secrets) > MAX_SERVICE_ACCOUNTS:
        limits.append(f"max-token-secrets:{MAX_SERVICE_ACCOUNTS}")
    if token_secrets:
        _add(findings, _call(sa_token_audit, {"workloads": [], "service_accounts": token_secrets[:MAX_SERVICE_ACCOUNTS]}, "secrets sa", problems), "", key_to_file)
    if roles or bindings:
        if len(roles) > MAX_RBAC_OBJECTS or len(bindings) > MAX_RBAC_OBJECTS:
            limits.append(f"max-rbac-objects:{MAX_RBAC_OBJECTS}")
        payload = {"roles": roles[:MAX_RBAC_OBJECTS], "bindings": bindings[:MAX_RBAC_OBJECTS]}
        _add(findings, _call(rbac_risk_scan, payload, "rbac", problems), "", key_to_file)
    skipped = sorted(set(skipped + problems))
    incomplete = [{**finding, "location": ""} for finding in incomplete_findings(skipped + limits, scanned)]
    unique = {(f["id"], f["severity"], f["summary"], f["location"]): f for f in findings + incomplete}
    ordered = sorted(unique.values(), key=lambda f: (-SEVERITY_RANK[f["severity"]], f["location"], f["id"], f["summary"]))
    failing = sum(1 for f in ordered if SEVERITY_RANK[f["severity"]] >= SEVERITY_RANK["major"])
    return {
        "status": scan_status(ordered), "level": level, "files_scanned": scanned, "files_skipped": len(skipped),
        "manifest_count": len(workloads) + len(roles) + len(bindings) + len(accounts) + len(token_secrets),
        "tools": {"yaml": "available" if yaml is not None else "skipped:tool-missing"},
        "skipped": skipped[:MAX_SKIPPED_LISTED], "limits_hit": sorted(set(limits)), "findings": ordered,
        "summary": f"{scanned} file(s) scanned, {len(ordered)} finding(s), {failing} major or worse",
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return dict(DRY_RUN)
    target = (ctx.root / args.path).resolve()
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {ctx.root}")
    return scan(ctx.root, target, args.level)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")
    p.add_argument("--level", choices=("baseline", "restricted"), default="baseline", help="Pod Security Standard to enforce (default baseline)")


if __name__ == "__main__":
    sys.exit(AgentScript("A24", "kpol_manifest_scan", run, description=__doc__, add_args=add_args).main())
