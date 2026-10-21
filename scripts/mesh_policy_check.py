#!/usr/bin/env python3
"""A23 - scan NetworkPolicy, mesh authorization, PeerAuthentication and Gateway API manifests for missing default-deny, permissive mTLS and cross-namespace references.

Reads JSON and (when PyYAML imports) YAML manifests under --root/--path and runs the A23 tool analyses over them:
NetworkPolicy default-deny per namespace (mesh_netpol_coverage), Istio AuthorizationPolicy default-deny and wildcard
rules (mesh_authz_lint), effective PeerAuthentication mTLS mode per workload (mesh_mtls_coverage) and Gateway API
attachment plus cross-namespace ReferenceGrants (mesh_gateway_api_check). The mesh checks (authz, mTLS) run only when the
tree contains an Istio security resource, because a plain cluster has no mesh to configure. Without PyYAML the YAML
files are not read and every check that depends on them reports `skipped:tool-missing`; JSON is always handled natively.

Read-only and bounded: files over 1 MiB and binary files are skipped, at most 2000 manifest files are read. Symlinks are never
followed or read, and a directory that cannot be listed is skipped: each is a reason naming its input. Findings use
info|minor|major|blocker. `status` is `fail` when any major or blocker finding exists, and also when no in-scope input was analysed
(none found, or none parsed) or one was not (skipped, unparsable, over a bound, or rejected by a check): the shared rule in
swarm/scan_status.py. `--dry-run` prints deterministic canned output and reads nothing. Stdlib only (PyYAML optional).
"""
from __future__ import annotations

import itertools
import json
import os
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a23_tools  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.tools.a30_tools import _path_label as path_label  # noqa: E402  (one definition of "secret-shaped path", shared with A30)
from swarm.tools.a30_tools import _secret_kind  # noqa: E402  (the strict rule an identifier is shown by)

try:  # optional: only YAML-dependent checks need it
    import yaml as _YAML
except ImportError:  # pragma: no cover - exercised by passing yaml_module=None
    _YAML = None

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_VISITED = 50_000  # directory entries looked at, so a huge tree without manifests still ends
MAX_DOCUMENTS = 5000
MAX_FINDINGS = 500
MAX_EXPANSIONS = 200_000  # list items visited while expanding one parsed file, aliases included: a shared list costs per path it is reached by
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".tox"}
SUFFIXES = (".yaml", ".yml", ".json")
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}
ROOT_NAMESPACE_RE = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?")
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")
CHECKS = (
    ("netpol-default-deny", ("NetworkPolicy", "Deployment", "StatefulSet", "DaemonSet")),
    ("authz-default-deny", ("AuthorizationPolicy",)),
    ("mtls-coverage", ("PeerAuthentication",)),
    ("gateway-references", ("Gateway", "HTTPRoute", "ReferenceGrant")),
)
COUNT_KEYS = ("files_scanned", "documents", "NetworkPolicy", "AuthorizationPolicy", "PeerAuthentication", "Gateway", "HTTPRoute",
              "ReferenceGrant", "workloads")


def _rel(root: Path, path: Path) -> str:
    """The path relative to the root for a finding or a skip reason; the bare name when the path is not under the root."""
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name


def _dir_label(root: Path, path: str, ordinal: int) -> str:
    """A directory as a reason shows it: repository-relative, then the same redaction as every other path in a reason or finding
    (a30 _path_label), so a directory whose name is secret-shaped shows as its <redacted-path-N> label."""
    p = Path(path)
    rel = p.relative_to(root).as_posix() if p.is_relative_to(root) else "<outside-root>"
    return path_label(rel, ordinal)


def _name_label(text: str, ordinal: int) -> str:
    """A workload namespace/name as a reason shows it. A name is echoed as an identifier, not a file path, so the strict rule applies: a
    bare 64-digit hex value is refused (a30 strict), and so is anything the handler scrubs (its credential shapes, the 40-plus hex-run
    pattern included). Otherwise the name is shown unchanged."""
    if _secret_kind(text, strict=True) or a23_tools._scrub(text) != text:
        return f"<redacted-path-{ordinal}>"
    return text


def _walk(root: Path, start: Path, labels) -> tuple[list[Path], dict]:
    """Candidate manifest files in deterministic order, what the bounds skipped, and a reason for each input left unread.
    A symlink is never followed and never read, wherever its target is: a symlinked directory is not descended into and a
    symlinked file is not opened. A directory that cannot be listed is skipped and recorded the same way. Each one is a reason
    naming the input, so the scan is incomplete."""
    stats = {"symlinks_not_followed": 0, "unlistable_directories": 0, "truncated": False, "reasons": []}
    if start.is_file():
        return [start], stats
    files: list[Path] = []
    visited = 0

    def unlistable_directory(error: OSError) -> None:
        stats["unlistable_directories"] += 1
        reason = _dir_label(root, error.filename or str(start), next(labels))
        stats["reasons"].append(f"{reason} directory not listed: {type(error).__name__}")

    for dirpath, dirnames, filenames in os.walk(start, followlinks=False, onerror=unlistable_directory):
        kept: list[str] = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS:
                continue
            child = Path(dirpath) / d
            if child.is_symlink():
                stats["symlinks_not_followed"] += 1
                shown = path_label(_rel(root, child), next(labels))
                stats["reasons"].append(f"{shown}/: symlinked directory not followed")
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            visited += 1
            if visited > MAX_VISITED:
                stats["truncated"] = True
                stats["reasons"].append(f"directory entry limit of {MAX_VISITED} reached; the rest of the tree was not read")
                return files, stats
            if not name.lower().endswith(SUFFIXES):
                continue
            path = Path(dirpath) / name
            if path.is_symlink():
                stats["symlinks_not_followed"] += 1
                shown = path_label(_rel(root, path), next(labels))
                stats["reasons"].append(f"{shown}: symlinked file not read")
                continue
            if len(files) >= MAX_FILES:
                stats["truncated"] = True
                stats["reasons"].append(f"file limit of {MAX_FILES} reached; the rest of the tree was not read")
                return files, stats
            files.append(path)
    return files, stats


class _ExpansionLimit(Exception):
    """One parsed file expands to more list items than MAX_EXPANSIONS (aliases shared through many lists)."""


def _expand(value, budget: list[int], ancestors: set[int] | None = None) -> list[dict]:
    """Documents of one parsed value; a `kind: List` wrapper contributes its items. Lists are cycle-checked on the current path only,
    so a self-referencing list (`&x [*x]`) contributes nothing past the point where it closes its own path. Every list item visited,
    aliases included, is charged to budget; _ExpansionLimit is raised when it runs out."""
    ancestors = set() if ancestors is None else ancestors
    if isinstance(value, list):
        if id(value) in ancestors:
            return []
        ancestors.add(id(value))
        try:
            out: list[dict] = []
            for item in value:
                budget[0] -= 1
                if budget[0] < 0:
                    raise _ExpansionLimit()
                out.extend(_expand(item, budget, ancestors))
            return out
        finally:
            ancestors.discard(id(value))
    if not isinstance(value, dict):
        return []
    if value.get("kind") == "List" and isinstance(value.get("items"), list):
        return _expand(value["items"], budget, ancestors)
    return [value]


def _expand_file(parsed: list) -> list[dict]:
    """Documents of every parsed value of one file, sharing one expansion budget: an alias reached through many lists is expanded per
    path, so a few kilobytes of YAML can otherwise expand exponentially."""
    budget = [MAX_EXPANSIONS]
    return [doc for value in parsed for doc in _expand(value, budget)]


def _load(root: Path, files: list[Path], yaml_module, labels) -> tuple[list[tuple[str, dict]], dict, list[dict]]:
    """(relative path, document) pairs, skip counters, info findings for unparsable files, and a reason per input not analysed.
    A path is shown through path_label (redacted when secret-shaped, as A30 does); a reason names the cause only, never the content."""
    stats = {"too_large": 0, "binary": 0, "yaml_skipped": 0, "unparsable": 0, "parsed": 0, "reasons": []}
    docs: list[tuple[str, dict]] = []
    findings: list[dict] = []
    for path in files:
        rel = path_label(_rel(root, path), next(labels))
        try:
            # the type is checked on the open descriptor, not on the path: a FIFO swapped in after the walk is refused before a read.
            # O_NONBLOCK makes the open of a FIFO with no writer return at once. A symlink is followed here as it was before.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):  # named pipes, sockets and devices are never read: a read could block the scan
                    stats["unparsable"] += 1
                    stats["reasons"].append(f"{rel}: not a regular file, not opened")
                    continue
                if info.st_size > MAX_FILE_BYTES:
                    stats["too_large"] += 1
                    stats["reasons"].append(f"{rel}: larger than {MAX_FILE_BYTES} bytes")
                    continue
                raw = handle.read()
        except OSError:
            stats["unparsable"] += 1
            # a path that is not a regular file (directory, socket, missing) keeps its not-regular reason; any other failure is unreadable
            reason = "unreadable" if path.is_file() else "not a regular file, not opened"
            stats["reasons"].append(f"{rel}: {reason}")
            continue
        if b"\x00" in raw[:8192]:
            stats["binary"] += 1
            stats["reasons"].append(f"{rel}: binary")
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            stats["binary"] += 1
            stats["reasons"].append(f"{rel}: not UTF-8")
            continue
        is_json = path.suffix.lower() == ".json"
        if not is_json and yaml_module is None:
            stats["yaml_skipped"] += 1
            stats["reasons"].append(f"{rel}: YAML not read, PyYAML is not importable")
            continue
        try:
            parsed = [json.loads(text)] if is_json else list(yaml_module.safe_load_all(text))
        except Exception as exc:  # noqa: BLE001 - YAMLError and JSONDecodeError: report and go on
            stats["unparsable"] += 1
            stats["reasons"].append(f"{rel}: not parsed ({type(exc).__name__})")
            findings.append({"id": "MESH-SCAN-UNPARSABLE", "severity": "info", "summary": f"{rel}: not parsed ({type(exc).__name__})",
                             "location": rel})
            continue
        try:
            expanded = _expand_file(parsed)
        except (_ExpansionLimit, RecursionError):  # too many list items, or nested deeper than the interpreter's stack: not analysed
            stats["unparsable"] += 1
            stats["reasons"].append(f"{rel}: list expansion exceeds {MAX_EXPANSIONS} items or its nesting; not analysed")
            continue
        stats["parsed"] += 1
        for doc in expanded:
            if len(docs) >= MAX_DOCUMENTS:
                stats["truncated_documents"] = True
                stats["reasons"].append(f"document limit of {MAX_DOCUMENTS} reached; the rest of the documents were not read")
                return docs, stats, findings
            docs.append((rel, doc))
    return docs, stats, findings


def _kind(doc: dict) -> str | None:
    kind, version = doc.get("kind"), str(doc.get("apiVersion", ""))
    if kind in ("NetworkPolicy",) and version.startswith("networking.k8s.io/"):
        return kind
    if kind in ("AuthorizationPolicy", "PeerAuthentication") and version.startswith("security.istio.io/"):
        return kind
    if kind in ("Gateway", "HTTPRoute", "ReferenceGrant") and version.startswith("gateway.networking.k8s.io/"):
        return kind
    if kind in WORKLOAD_KINDS and version.startswith("apps/"):
        return kind
    if kind == "Namespace" and version == "v1":
        return kind
    return None


def _coords(doc: dict) -> tuple[str, str] | None:
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str) or not metadata["name"]:
        return None
    namespace = metadata.get("namespace")
    return (namespace if isinstance(namespace, str) and namespace else "default"), metadata["name"]


def _pod_of(doc: dict) -> tuple[dict | None, str]:
    """(workload, ""): the namespace, name and pod-template labels; or (None, the path that is not a mapping). A missing or null spec or
    template is not analysed; a null template metadata or labels reads as no labels."""
    coords = _coords(doc)
    if coords is None:
        return None, "metadata.name is missing"
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        return None, "spec is missing, null or not a mapping"
    template = spec.get("template")
    if not isinstance(template, dict):
        return None, "spec.template is missing, null or not a mapping"
    metadata = {} if template.get("metadata") is None else template["metadata"]
    if not isinstance(metadata, dict):
        return None, "spec.template.metadata is not a mapping"
    labels = {} if metadata.get("labels") is None else metadata["labels"]
    if not isinstance(labels, dict):
        return None, "spec.template.metadata.labels is not a mapping"
    return {"namespace": coords[0], "name": coords[1], "labels": {str(k): str(v) for k, v in labels.items()}}, ""


def _sorted(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (-SEVERITY_RANK[f["severity"]], f["id"], f["summary"]))


def scan(root: Path, start: Path, *, yaml_module=_YAML, min_strict_pct: float = 100, root_namespace: str = a23_tools.DEFAULT_ROOT_NAMESPACE) -> dict:
    """Scan `start` (inside `root`) and return the result document. An input that was not analysed, or a check that could not be
    evaluated, makes the scan incomplete: its status is `fail` (swarm/scan_status.py)."""
    labels = itertools.count(1)  # one numbering for every redacted path, by position in the scan, so no two labels collide
    files, walk_stats = _walk(root, start, labels)
    docs, load_stats, findings = _load(root, files, yaml_module, labels)
    reasons = [*walk_stats["reasons"], *load_stats["reasons"]]
    by_kind: dict[str, list[dict]] = {}
    locations: dict[str, str] = {}
    workloads: list[dict] = []
    for rel, doc in docs:
        kind = _kind(doc)
        if kind is None:
            continue
        by_kind.setdefault(kind, []).append(doc)
        coords = _coords(doc)
        if coords:
            locations.setdefault(f"{coords[0]}/{coords[1]}", rel)
        if kind in WORKLOAD_KINDS:
            pod, problem = _pod_of(doc)
            if pod is None:  # not analysed: a reason, never a phantom pod and never silently dropped
                named = _name_label(f"{coords[0]}/{coords[1]}", next(labels)) if coords else "unnamed document"
                reasons.append(f"{rel}: {kind} {named}: {problem}; workload not analysed")
            else:
                workloads.append(pod)
    namespaces = sorted({p["namespace"] for p in workloads} | {c[0] for k in ("NetworkPolicy", "AuthorizationPolicy", "PeerAuthentication")
                                                              for d in by_kind.get(k, []) if (c := _coords(d))}
                        | {c[1] for d in by_kind.get("Namespace", []) if (c := _coords(d))})
    mesh_in_use = bool(by_kind.get("AuthorizationPolicy") or by_kind.get("PeerAuthentication"))
    yaml_missing = load_stats["yaml_skipped"] > 0
    plans = {
        "netpol-default-deny": (bool(by_kind.get("NetworkPolicy") or workloads or by_kind.get("Namespace")), a23_tools.netpol_coverage,
                                {"network_policies": by_kind.get("NetworkPolicy", []), "pods": workloads, "namespaces": namespaces}),
        "authz-default-deny": (mesh_in_use, a23_tools.authz_lint,
                               {"policies": by_kind.get("AuthorizationPolicy", []), "namespaces": namespaces, "root_namespace": root_namespace}),
        "mtls-coverage": (mesh_in_use and bool(workloads), a23_tools.mtls_coverage,
                          {"peer_authentications": by_kind.get("PeerAuthentication", []), "workloads": workloads,
                           "min_strict_pct": min_strict_pct, "root_namespace": root_namespace}),
        "gateway-references": (bool(by_kind.get("Gateway") or by_kind.get("HTTPRoute")), a23_tools.gateway_api_check,
                               {"gateways": by_kind.get("Gateway", []), "routes": by_kind.get("HTTPRoute", []),
                                "reference_grants": by_kind.get("ReferenceGrant", []),
                                "namespaces": [{"name": d["metadata"]["name"], "labels": (d["metadata"].get("labels") or {})}
                                               for d in by_kind.get("Namespace", []) if _coords(d)]}),
    }
    checks = []
    for name, _kinds in CHECKS:
        applicable, handler, payload = plans[name]
        if not applicable:
            checks.append({"check": name, "status": "skipped:tool-missing" if yaml_missing else "skipped:no-input", "findings": 0})
            continue
        try:
            result = handler(payload)
        except Exception as exc:  # noqa: BLE001 - one malformed manifest must not hide the other checks
            findings.append({"id": "MESH-SCAN-ERROR", "severity": "minor", "summary": f"{name}: analysis failed ({type(exc).__name__})"})
            reasons.append(f"{name}: analysis failed ({type(exc).__name__}), check not evaluated")
            checks.append({"check": name, "status": "error", "findings": 1})
            continue
        if result.get("state") != "SUCCESS":
            findings.append({"id": "MESH-SCAN-INVALID-INPUT", "severity": "minor",
                             "summary": f"{name}: input rejected at {result.get('field')}; the check was not evaluated"})
            reasons.append(f"{name}: input rejected at {result.get('field')}, check not evaluated")
            checks.append({"check": name, "status": "error", "findings": 1})
            continue
        got = []
        for item in result["findings"]:
            item = dict(item)
            if item["id"] == "ROUTE-PARENT-MISSING":  # the Gateway may live outside the scanned files
                item["severity"] = "minor"
                item["summary"] += " (scanned files only)"
            subject = item["summary"].split(": ", 1)[0]
            if subject in locations:
                item["location"] = locations[subject]
            got.append(item)
        findings.extend(got)
        checks.append({"check": name, "status": "ran", "findings": result["finding_count"]})
    counts = {"files_scanned": len(files), "documents": len(docs), **{k: len(by_kind.get(k, [])) for k in
              ("NetworkPolicy", "AuthorizationPolicy", "PeerAuthentication", "Gateway", "HTTPRoute", "ReferenceGrant")},
              "workloads": len(workloads)}
    skipped = []
    if yaml_missing:
        skipped.append({"what": "yaml-manifests", "status": "skipped:tool-missing", "files": load_stats["yaml_skipped"]})
    for key, what in (("too_large", "files-over-1MiB"), ("binary", "binary-files")):
        if load_stats[key]:
            skipped.append({"what": what, "status": "skipped:bounded", "files": load_stats[key]})
    if walk_stats["symlinks_not_followed"]:
        skipped.append({"what": "symlinks-not-followed", "status": "skipped:bounded", "files": walk_stats["symlinks_not_followed"]})
    if walk_stats["unlistable_directories"]:
        skipped.append({"what": "directories-not-listed", "status": "skipped:bounded",
                        "directories": walk_stats["unlistable_directories"]})
    truncated = bool(walk_stats["truncated"] or load_stats.get("truncated_documents"))
    findings.extend({**item, "location": "."} for item in incomplete_findings(reasons, load_stats["parsed"]))
    ordered = _sorted(findings)
    status = scan_status(findings)
    return {
        "status": status,
        "findings": ordered[:MAX_FINDINGS],
        "finding_count": len(findings),
        "counts": counts,
        "checks": checks,
        "skipped": skipped,
        "truncated": truncated,
        "summary": f"{len(findings)} finding(s) in {len(files)} file(s); "
                   + ", ".join(f"{c['check']}={c['status']}" for c in checks) + ("; truncated" if truncated else ""),
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok",
            "dry_run": True,
            "findings": [],
            "finding_count": 0,
            "counts": {key: 0 for key in COUNT_KEYS},
            "checks": [{"check": name, "status": "dry-run", "findings": 0} for name, _ in CHECKS],
            "skipped": [],
            "truncated": False,
            "summary": "dry-run: mesh_policy_check would scan manifests under --root for default-deny, mTLS and cross-namespace gaps",
        }
    if not 0 <= args.min_strict_pct <= 100:
        raise SwarmError(ErrorCode.E_INPUT, "--min-strict-pct must be between 0 and 100")
    if not ROOT_NAMESPACE_RE.fullmatch(args.root_namespace):
        raise SwarmError(ErrorCode.E_INPUT, "--root-namespace must be a Kubernetes namespace name")
    target = (ctx.root / args.path).resolve()
    labels = itertools.count(1)  # one numbering for the redacted paths of this call, as scan numbers its own
    if not target.is_relative_to(ctx.root):
        shown = path_label(args.path, next(labels))  # a --path can carry a credential or key shape: the same redaction as paths
        raise SwarmError(ErrorCode.E_INPUT, f"--path {shown!r} leaves the repository root")
    if not target.exists():
        shown = path_label(args.path, next(labels))
        root = path_label(str(ctx.root), next(labels))  # the root is a path in this envelope too
        raise SwarmError(ErrorCode.E_INPUT, f"--path {shown!r} does not exist under {root}")
    return scan(ctx.root, target, min_strict_pct=args.min_strict_pct, root_namespace=args.root_namespace)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")
    p.add_argument("--min-strict-pct", type=float, default=100.0, help="required share of workloads under strict mTLS (default 100)")
    p.add_argument("--root-namespace", default=a23_tools.DEFAULT_ROOT_NAMESPACE,
                   help="Istio rootNamespace whose policies apply mesh-wide (default istio-system; set it to the mesh's rootNamespace)")


if __name__ == "__main__":
    sys.exit(AgentScript("A23", "mesh_policy_check", run, description=__doc__, add_args=add_args).main())
