#!/usr/bin/env python3
"""A21 - scan Kubernetes manifests in a repository for APIs removed in a target version and for missing resource requests and limits.

Walks --path under --root for .yaml/.yml/.json files (bounded: files over 1 MiB are skipped, at most 2000 files are
read, symlinks are never followed, binary files are skipped) and reports, per manifest document:
- removed-api: the apiVersion/kind is not served by --target-version (blocker) or is removed in the next minor (minor);
  the removal table and its rules are those of swarm.tools.a21_tools (the k8s_deprecated_api_scan tool);
- missing-request: a container or init container of a Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet,
  ReplicationController, Job or CronJob has no cpu or memory request (major);
- missing-limit: no cpu or memory limit (minor).
--target-version defaults to the newest removal in the table, so every API the table knows counts as removed.
JSON is parsed natively. YAML needs PyYAML; without it YAML files are not read and the result carries
`skipped:tool-missing`. Read-only: nothing is written. Optional binaries (kubectl, helm) are never required.
Status is `fail` when any major or worse finding exists. `--dry-run` prints deterministic canned output and reads nothing.
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
from swarm.tools import a21_tools  # noqa: E402

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
_ALIAS = re.compile(r"(?:^|[\s\[,{:-])\*[A-Za-z0-9_.-]+")
MAX_ALIASES = 20
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}


def _load_yaml():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, looked up per run
    except ImportError:
        return None
    return yaml


def _candidates(base: Path, counters: dict) -> list[Path]:
    """Manifest candidates in sorted order; symlinks are never followed or read."""
    if base.is_file():
        return [base]
    found: list[Path] = []
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(current) / d).is_symlink())
        for name in sorted(files):
            path = Path(current) / name
            if not name.lower().endswith(SUFFIXES):
                continue
            if path.is_symlink():
                counters["symlink"] += 1
            elif len(found) >= MAX_FILES:
                counters["truncated"] = True
                return found
            else:
                found.append(path)
    return found


def _documents(path: Path, yaml_module, counters: dict, skipped: dict):
    """Parsed documents of one file, or None when it was skipped or unreadable."""
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            counters["too_large"] += 1
            return None
        data = path.read_bytes()
    except OSError:
        counters["unreadable"] += 1
        return None
    if b"\x00" in data[:8192]:
        counters["binary"] += 1
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        counters["binary"] += 1
        return None
    if path.suffix.lower() == ".json":
        try:
            return [json.loads(text)]
        except (ValueError, RecursionError):
            counters["unparsable"] += 1
            return None
    if yaml_module is None:
        skipped["yaml_files"] += 1
        return None
    if len(_ALIAS.findall(text)) > MAX_ALIASES:
        counters["unparsable"] += 1
        return None
    try:
        return list(yaml_module.safe_load_all(text))
    except (yaml_module.YAMLError, RecursionError):
        counters["unparsable"] += 1
        return None


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
            resources = container.get("resources") if isinstance(container.get("resources"), dict) else {}
            for bucket, rule, severity in (("requests", "missing-request", "major"), ("limits", "missing-limit", "minor")):
                values = resources.get(bucket) if isinstance(resources.get(bucket), dict) else {}
                for resource in ("cpu", "memory"):
                    if not _present(values.get(resource)):
                        what = "container" if group == "containers" else "init container"
                        out.append((f"{rule}/{kind}/{name}/{cname}/{resource}", rule, severity, f"{kind} {name}: {what} {cname} has no {resource} {bucket[:-1]}"))
    return out


def _scan_object(obj: dict, target_label: str, location: str) -> list[dict]:
    api_version, kind = obj["apiVersion"], obj["kind"]
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
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
    counters = {"symlink": 0, "too_large": 0, "binary": 0, "unparsable": 0, "unreadable": 0, "parsed": 0, "truncated": False}
    skipped_yaml = {"yaml_files": 0}
    yaml_module = _load_yaml()
    findings: list[dict] = []
    files = _candidates(target, counters)
    manifests = 0
    for path in files:
        relative = path.relative_to(ctx.root).as_posix()
        documents = _documents(path, yaml_module, counters, skipped_yaml)
        counters["parsed"] += documents is not None
        for index, document in enumerate(documents or []):
            for obj in _expand(document):
                if not (isinstance(obj.get("apiVersion"), str) and isinstance(obj.get("kind"), str)):
                    continue
                manifests += 1
                findings.extend(_scan_object(obj, target_label, f"{relative}#{index}" if len(documents) > 1 else relative))
    skipped = []
    if skipped_yaml["yaml_files"]:
        skipped.append({"check": "yaml-manifests", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": skipped_yaml["yaml_files"]})
    if counters["truncated"]:
        findings.append({"id": "scan/file-limit", "rule": "scan-bound", "severity": "info", "location": target.relative_to(ctx.root).as_posix() or ".",
                         "summary": f"stopped after {MAX_FILES} files; narrow --path to scan the rest"})
    findings.sort(key=lambda f: (-SEVERITY_RANK[f["severity"]], f["location"], f["id"]))
    failing = any(SEVERITY_RANK[f["severity"]] >= SEVERITY_RANK["major"] for f in findings)
    return {
        "status": "fail" if failing else "ok",
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
    sys.exit(AgentScript("A21", "k8s_manifest_check", run, description=__doc__, add_args=add_args).main())
