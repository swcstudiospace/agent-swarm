#!/usr/bin/env python3
"""A21 - scan Kubernetes manifests in a repository for APIs removed in a target version and for missing resource requests and limits.

Walks --path under --root for .yaml/.yml/.json files (bounded: files over 1 MiB are skipped, at most 2000 files are
read, symlinks are never followed, binary files are skipped) and reports, per manifest document:
- removed-api: the apiVersion/kind is not served by --target-version (blocker) or is removed in the next minor (minor);
  the removal table and its rules are those of swarm.tools.a21_tools (the k8s_deprecated_api_scan tool);
- missing-request: a container or init container of a Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet,
  ReplicationController, Job or CronJob has no cpu or memory request (major; Kubernetes copies an omitted request from its limit);
- missing-limit: no cpu or memory limit (minor).
--target-version defaults to the newest removal in the table, so every API the table knows counts as removed.
JSON is parsed natively. YAML needs PyYAML; without it YAML files are not read and the result carries
`skipped:tool-missing`. Read-only: nothing is written. Optional binaries (kubectl, helm) are never required.
Status is `fail` when any major or worse finding exists, or when any in-scope input was not analysed (skipped, unreadable, too large, binary, unparsable, symlinked, past the file limit, or none analysed): a major `scan-incomplete` finding then lists the reasons. `--dry-run` prints deterministic canned output and reads nothing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a21_tools  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
SUFFIXES = (".yaml", ".yml", ".json")
SKIP_DIRS = {".git", "node_modules", ".swarm"}
WORKLOAD_PODS = {  # kind -> path of the pod spec
    "Pod": ("spec",),
    "Deployment": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"),
    "DaemonSet": ("spec", "template", "spec"),
    "ReplicaSet": ("spec", "template", "spec"),
    "ReplicationController": ("spec", "template", "spec"),
    "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
}
MAX_ALIASES = 20
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}


def _load_yaml():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, looked up per run
    except ImportError:
        return None
    return yaml


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _skip(counters: dict, reasons: list[str], key: str, reason: str):
    """Count one in-scope input that was not analysed and record why; reasons carry labels and paths, never parsed values."""
    counters[key] += 1
    reasons.append(reason)
    return None


def _candidates(base: Path, root: Path, counters: dict, reasons: list[str]) -> list[Path]:
    """Manifest candidates in sorted order; symlinks are never followed or read, and each one is a skipped input."""
    if base.is_file():
        return [base]
    found: list[Path] = []
    for current, dirs, files in os.walk(base, followlinks=False):
        kept = []
        for d in sorted(dirs):
            if d in SKIP_DIRS:
                continue
            if (Path(current) / d).is_symlink():
                _skip(counters, reasons, "symlink", f"{_relative(Path(current) / d, root)}: symlink directory not followed")
            else:
                kept.append(d)
        dirs[:] = kept
        for name in sorted(files):
            path = Path(current) / name
            if not name.lower().endswith(SUFFIXES):
                continue
            if path.is_symlink():
                _skip(counters, reasons, "symlink", f"{_relative(path, root)}: symlink not followed")
            elif len(found) >= MAX_FILES:
                counters["truncated"] = True
                reasons.append(f"file limit: stopped after {MAX_FILES} files; narrow --path to scan the rest")
                return found
            else:
                found.append(path)
    return found


def _alias_count(text: str, yaml_module) -> int:
    """Alias events of the YAML stream: a comment or plain scalar that merely contains '*name' is not an alias."""
    return sum(isinstance(event, yaml_module.AliasEvent) for event in yaml_module.parse(text, Loader=yaml_module.SafeLoader))


def _documents(path: Path, root: Path, yaml_module, counters: dict, reasons: list[str]):
    """Parsed documents of one file, or None when it was not analysed (the reason is recorded)."""
    label = _relative(path, root)
    if not path.is_file():  # a FIFO or device named like a manifest would block the read
        return _skip(counters, reasons, "unreadable", f"{label}: not a readable regular file")
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return _skip(counters, reasons, "too_large", f"{label}: larger than {MAX_FILE_BYTES} bytes")
        data = path.read_bytes()
    except OSError:
        return _skip(counters, reasons, "unreadable", f"{label}: not a readable regular file")
    if b"\x00" in data[:8192]:
        return _skip(counters, reasons, "binary", f"{label}: binary file")
    try:
        text = data.decode("utf-8-sig")  # a byte-order mark is encoding, not content
    except UnicodeDecodeError:
        return _skip(counters, reasons, "binary", f"{label}: not UTF-8 text")
    if path.suffix.lower() == ".json":
        try:
            return [json.loads(text)]
        except (ValueError, RecursionError):
            return _skip(counters, reasons, "unparsable", f"{label}: not valid JSON")
    if yaml_module is None:
        return _skip(counters, reasons, "yaml_files", f"{label}: PyYAML is not installed, so YAML is not read (skipped:tool-missing)")
    try:
        if _alias_count(text, yaml_module) > MAX_ALIASES:
            return _skip(counters, reasons, "unparsable", f"{label}: more than {MAX_ALIASES} YAML aliases")
        return list(yaml_module.safe_load_all(text))
    except (yaml_module.YAMLError, ValueError, RecursionError):
        return _skip(counters, reasons, "unparsable", f"{label}: not valid YAML")


def _expand(document) -> list[dict]:
    """The manifest objects of one document: a kind: List contributes its items."""
    if not isinstance(document, dict):
        return []
    kind = document.get("kind")
    if isinstance(kind, str) and kind.endswith("List") and isinstance(document.get("items"), list):
        return [item for item in document["items"] if isinstance(item, dict)]
    return [document]


def _dig(node, path: tuple[str, ...]):
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _present(value) -> bool:
    return value is not None and str(value).strip() != ""


def _resource_findings(kind: str, name: str, spec) -> list[tuple[str, str, str, str]]:
    """(id, rule, severity, summary) for every container without requests or limits."""
    out: list[tuple[str, str, str, str]] = []
    if not isinstance(spec, dict):
        return out
    for group in ("initContainers", "containers"):
        containers = spec.get(group)
        for container in containers if isinstance(containers, list) else []:
            if not isinstance(container, dict):
                continue
            cname = str(container.get("name", "?"))
            raw_resources = container.get("resources")
            resources = raw_resources if isinstance(raw_resources, dict) else {}
            raw_limits = resources.get("limits")
            limits = raw_limits if isinstance(raw_limits, dict) else {}
            for bucket, rule, severity in (("requests", "missing-request", "major"), ("limits", "missing-limit", "minor")):
                raw_values = resources.get(bucket)
                values = raw_values if isinstance(raw_values, dict) else {}
                for resource in ("cpu", "memory"):
                    # Kubernetes copies an omitted (or null) request from the limit of the same resource; an empty quantity is still a gap
                    copied = bucket == "requests" and values.get(resource) is None and _present(limits.get(resource))
                    if not _present(values.get(resource)) and not copied:
                        what = "container" if group == "containers" else "init container"
                        out.append((f"{rule}/{kind}/{name}/{cname}/{resource}", rule, severity, f"{kind} {name}: {what} {cname} has no {resource} {bucket[:-1]}"))
    return out


def _scan_object(obj: dict, target_label: str, location: str) -> list[dict]:
    api_version, kind = obj["apiVersion"], obj["kind"]
    raw_metadata = obj.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    name = str(metadata.get("name", "?"))[:63] or "?"
    findings: list[dict] = []
    removed = a21_tools.deprecated_api_scan({
        "target_version": target_label,
        "resources": [{"api_version": api_version[:63], "kind": kind[:63], "name": name}],
    })
    for item in removed.get("findings", []):
        findings.append({"id": f"removed-api/{api_version}/{kind}/{name}", "rule": "removed-api", "severity": item["severity"],
                         "summary": item["summary"], "location": location})
    path = WORKLOAD_PODS.get(kind)
    if path is not None and (api_version == "v1" or api_version.startswith(("apps/", "batch/", "extensions/"))):
        for fid, rule, severity, summary in _resource_findings(kind, name, _dig(obj, path)):
            findings.append({"id": fid, "rule": rule, "severity": severity, "summary": summary, "location": location})
    return findings


def _default_target() -> str:
    return f"1.{max(minor for minor, _ in a21_tools.REMOVED_APIS.values())}"


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "target_version": args.target_version or _default_target(), "files_scanned": 0,
            "manifests_scanned": 0, "findings": [], "skipped": [], "truncated": False,
            "summary": "dry-run: k8s_manifest_check would scan --path under --root for removed APIs and missing requests and limits",
        }
    target = (ctx.root / args.path).resolve()
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, "--path leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, "--path does not exist under the repository root")
    probe = a21_tools.deprecated_api_scan({"target_version": args.target_version or _default_target(), "resources": []})
    if probe["state"] != "SUCCESS":
        raise SwarmError(ErrorCode.E_INPUT, "--target-version is not a Kubernetes 1.x version")
    target_label = probe["target_version"]
    counters = {"symlink": 0, "too_large": 0, "binary": 0, "unparsable": 0, "unreadable": 0, "yaml_files": 0, "parsed": 0, "truncated": False}
    reasons: list[str] = []
    yaml_module = _load_yaml()
    findings: list[dict] = []
    files = _candidates(target, ctx.root, counters, reasons)
    manifests = 0
    for path in files:
        relative = _relative(path, ctx.root)
        documents = _documents(path, ctx.root, yaml_module, counters, reasons)
        if documents is not None:
            counters["parsed"] += 1
        doc_list = documents or []
        for index, document in enumerate(doc_list):
            for obj in _expand(document):
                if not (isinstance(obj.get("apiVersion"), str) and isinstance(obj.get("kind"), str)):
                    continue
                manifests += 1
                findings.extend(_scan_object(obj, target_label, f"{relative}#{index}" if len(doc_list) > 1 else relative))
    skipped = []
    if counters["yaml_files"]:
        skipped.append({"check": "yaml-manifests", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": counters["yaml_files"]})
    location = target.relative_to(ctx.root).as_posix() or "."
    findings.extend({**finding, "location": location} for finding in incomplete_findings(reasons, counters["parsed"]))
    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["location"], f["id"]))
    return {
        "status": scan_status(findings),
        "target_version": target_label,
        "files_scanned": counters["parsed"],
        "manifests_scanned": manifests,
        "files_skipped": {k: counters[k] for k in ("symlink", "too_large", "binary", "unparsable", "unreadable")},
        "truncated": counters["truncated"],
        "skipped": skipped,
        "findings": findings,
        "summary": f"{len(findings)} finding(s) in {manifests} manifest(s) against Kubernetes {target_label}",
    }


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")
    p.add_argument("--target-version", default=None, help="Kubernetes version to check removed APIs against (default: newest removal in the table)")


if __name__ == "__main__":
    sys.exit(AgentScript("A21", "k8s_manifest_check", run, description=__doc__ or "", add_args=add_args).main())
