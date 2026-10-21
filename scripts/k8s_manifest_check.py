#!/usr/bin/env python3
"""A21 - scan Kubernetes manifests in a repository for APIs removed in a target version and for missing resource requests and limits.

Walks --path under --root for .yaml/.yml/.json files and extensionless Kustomization files (bounded: files over 1 MiB are skipped, at most 2000 files are
read, symlinks are never followed, binary files are skipped) and reports, per manifest document:
- removed-api: the apiVersion/kind is not served by --target-version (blocker) or is removed in the next minor (minor);
  the removal table and its rules are those of swarm.tools.a21_tools (the k8s_deprecated_api_scan tool);
- missing-request: a container or init container of a Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet,
  ReplicationController, Job or CronJob has no cpu or memory request (major; Kubernetes copies an omitted request from its limit);
- missing-limit: no cpu or memory limit (minor).
--target-version defaults to the newest removal in the table, so every API the table knows counts as removed.
JSON is parsed natively. YAML needs PyYAML; without it YAML files are not read and the result carries
`skipped:tool-missing`. Read-only: nothing is written. Optional binaries (kubectl, helm) are never required.
Status is `fail` when any major or worse finding exists, or when any in-scope input was not analysed (skipped, unreadable, too large, binary, unparsable, symlinked including a symlinked --path, past the file limit, a non-mapping document, a malformed Kubernetes object whose apiVersion or kind is missing, empty or not a string, a List nested deeper than the bound), or when no Kubernetes object was inspected at all. An object is inspected when it is a candidate that was parsed and judged; a Chart.yaml or kustomize file that is silent by design, an empty document and a values file do not count. A tree whose files were read but held no inspected object fails with a reason, listed first so the summary never cuts it off, that names the files read and says none held a Kubernetes manifest, or none held an inspectable Kubernetes manifest when a candidate was rejected as malformed; a tree with no file read at all fails with "no in-scope input was analysed". A mapping is a Kubernetes object candidate with a kind key, or with an apiVersion key and any other key; a mapping with neither apiVersion nor kind (values files) is not an object and is not read as one. Files named Chart.yaml, Chart.yml, kustomization.yaml, kustomization.yml or Kustomization are identified by file name and are objects only with a kind key: a kind-bearing mapping in them is analysed as usual, and a mapping without kind (chart or kustomize metadata) is silent. In a kustomize file a mapping whose kind is one of KUSTOMIZE_CONFIG_KINDS (Kustomization, Component) is Kustomize's own configuration and is silent whatever its apiVersion. Residual: a non-manifest mapping with a top-level apiVersion key and another key (for example a values file that sets apiVersion) is reported as malformed by design, because it cannot be told apart from a kind-less object; the remedy is to rename the key or move the file out of the scanned path. Behaviour change with the same rule: a non-chart JSON or YAML mapping with a top-level apiVersion and a name or version key, such as lib.json, is reported as malformed and fails closed. `--dry-run` prints deterministic canned output and reads nothing.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a21_tools  # noqa: E402
from swarm.scan_status import MAX_REASONS_SHOWN, incomplete_findings, scan_status  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
SUFFIXES = (".yaml", ".yml", ".json")
WALK_NAMES = ("Kustomization",)  # extensionless manifest names the walk reads; the --path route accepts exactly the same names
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
MAX_LIST_DEPTH = 8  # nested kind: List levels expanded; deeper nesting is a reason, never silently dropped
SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "blocker": 3}


def _load_yaml():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, looked up per run
    except ImportError:
        return None
    return yaml


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _first_symlink(root: Path, requested: Path) -> str | None:
    """The first component of the requested path, from the root down, that is a symlink; such a path is never followed."""
    try:
        parts = requested.relative_to(root).parts
    except ValueError:  # outside the root; the containment check reports it
        return None
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            return _relative(current, root)
    return None


def _skip(counters: dict, reasons: list[str], key: str, reason: str):
    """Count one in-scope input that was not analysed and record why; reasons carry labels and paths, never parsed values."""
    counters[key] += 1
    reasons.append(reason)
    return None


def _none_held_reason(paths: list[str], rejected: bool) -> str:
    """Names the files that were read when no Kubernetes object was inspected; names and paths only, never parsed values. rejected: a candidate was seen and rejected as malformed, so the files held a Kubernetes object that was not inspectable."""
    shown = paths[:MAX_REASONS_SHOWN]
    more = len(paths) - len(shown)
    listed = ", ".join(shown) + (f" and {more} more" if more > 0 else "")
    noun = "file" if len(paths) == 1 else "files"
    what = "an inspectable Kubernetes manifest" if rejected else "a Kubernetes manifest"
    return f"read {len(paths)} {noun} ({listed}); none held {what}"


def _manifest_name(name: str) -> bool:
    """Whether a file name is a manifest candidate: a YAML or JSON suffix, or an extensionless name in WALK_NAMES."""
    return name.lower().endswith(SUFFIXES) or name in WALK_NAMES


def _candidates(base: Path, root: Path, counters: dict, reasons: list[str]) -> list[Path]:
    """Manifest candidates in sorted order; symlinks are never followed or read, and each one is a skipped input. A file named by --path is a candidate only under the same name rule as the walk."""
    if base.is_file():
        return [base] if _manifest_name(base.name) else []
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
            if not _manifest_name(name):
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
    try:  # the type is checked on the open descriptor: a FIFO or device named like a manifest is refused before a read, and O_NONBLOCK
        # makes the open of a FIFO with no writer return at once. A symlink is followed here as it was before.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                return _skip(counters, reasons, "unreadable", f"{label}: not a readable regular file")
            if info.st_size > MAX_FILE_BYTES:
                return _skip(counters, reasons, "too_large", f"{label}: larger than {MAX_FILE_BYTES} bytes")
            data = handle.read()
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


def _expand(document, where: str, reasons: list[str], depth: int = 0) -> list[dict]:
    """The manifest objects of one document: a kind: List contributes its items, recursively up to MAX_LIST_DEPTH. Anything else that cannot be analysed is a reason."""
    if document is None:  # an empty YAML document carries no object
        return []
    if not isinstance(document, dict):
        reasons.append(f"{where}: document is not a mapping; not analysed")
        return []
    kind = document.get("kind")
    if not (isinstance(kind, str) and kind.endswith("List") and isinstance(document.get("items"), list)):
        return [document]
    if depth >= MAX_LIST_DEPTH:
        reasons.append(f"{where}: List nested deeper than {MAX_LIST_DEPTH} levels; its items are not analysed")
        return []
    out: list[dict] = []
    for item in document["items"]:
        if isinstance(item, dict):
            out.extend(_expand(item, where, reasons, depth + 1))
        else:
            reasons.append(f"{where}: List item is not a mapping; not analysed")
    return out


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


def _non_empty_string(value) -> bool:
    return isinstance(value, str) and value.strip() != ""


# Helm chart metadata and Kustomize configuration are identified by file name; their mappings are objects only with a kind key.
KUSTOMIZE_FILE_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
KIND_ONLY_FILE_NAMES = ("Chart.yaml", "Chart.yml") + KUSTOMIZE_FILE_NAMES
# Kustomize's own configuration kinds: not workloads, so a mapping of either kind in a kustomize file is silent whatever its apiVersion.
# A tuple, not a set, so that an unhashable kind value is compared safely.
KUSTOMIZE_CONFIG_KINDS = ("Kustomization", "Component")


def _kubernetes_object(obj: dict, where: str, reasons: list[str], kind_only: bool, kustomize: bool) -> bool:
    """True when obj is a Kubernetes object to analyse. A candidate is a mapping with a kind key; outside KIND_ONLY_FILE_NAMES it is also a mapping with an apiVersion key and any other key. In a kustomize file a mapping whose kind is one of KUSTOMIZE_CONFIG_KINDS is Kustomize's own configuration: silent whatever its apiVersion. A mapping that is not a candidate (values, chart or kustomize metadata without kind) stays silent. A candidate needs a non-empty string apiVersion and kind, else it is malformed: a reason, never a silent drop or a counted manifest. Reasons carry field labels and the location only, never the values."""
    if kustomize and obj.get("kind") in KUSTOMIZE_CONFIG_KINDS:
        return False  # not a workload, so there is no removed-API or resource check to run
    candidate = "kind" in obj or (not kind_only and "apiVersion" in obj and any(key != "apiVersion" for key in obj))
    if not candidate:
        return False
    bad = [key for key in ("apiVersion", "kind") if not _non_empty_string(obj.get(key))]
    if bad:
        reasons.append(f"{where}: malformed Kubernetes object, {' and '.join(bad)} not a non-empty string; not analysed")
        return False
    return True


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
    counters = {"symlink": 0, "too_large": 0, "binary": 0, "unparsable": 0, "unreadable": 0, "yaml_files": 0, "parsed": 0, "truncated": False}
    reasons: list[str] = []
    link = _first_symlink(ctx.root, ctx.root / args.path)
    if link is not None:  # a --path through a symlink is never followed or read, as in a directory walk
        _skip(counters, reasons, "symlink", f"{link}: symlink not followed")
        files, location = [], link
    else:
        target = (ctx.root / args.path).resolve()
        if not target.is_relative_to(ctx.root):
            raise SwarmError(ErrorCode.E_INPUT, "--path leaves the repository root")
        if not target.exists():
            raise SwarmError(ErrorCode.E_INPUT, "--path does not exist under the repository root")
        files = _candidates(target, ctx.root, counters, reasons)
        location = target.relative_to(ctx.root).as_posix() or "."
    probe = a21_tools.deprecated_api_scan({"target_version": args.target_version or _default_target(), "resources": []})
    if probe["state"] != "SUCCESS":
        raise SwarmError(ErrorCode.E_INPUT, "--target-version is not a Kubernetes 1.x version")
    target_label = probe["target_version"]
    yaml_module = _load_yaml()
    findings: list[dict] = []
    manifests = 0
    parsed_paths: list[str] = []  # every file that was read and parsed, whatever it held: named when no Kubernetes object is inspected
    rejected_candidate = False  # a candidate was rejected as malformed: the files held a Kubernetes object, just not an inspectable one
    for path in files:
        relative = _relative(path, ctx.root)
        documents = _documents(path, ctx.root, yaml_module, counters, reasons)
        if documents is not None:
            counters["parsed"] += 1
            parsed_paths.append(relative)
        kind_only = path.name in KIND_ONLY_FILE_NAMES  # chart and kustomize files are objects only when they carry a kind
        kustomize = path.name in KUSTOMIZE_FILE_NAMES
        doc_list = documents or []
        for index, document in enumerate(doc_list):
            where = f"{relative}#{index}" if len(doc_list) > 1 else relative
            for obj in _expand(document, where, reasons):
                before = len(reasons)
                if not _kubernetes_object(obj, where, reasons, kind_only, kustomize):
                    if len(reasons) > before:  # a malformed candidate records exactly one reason
                        rejected_candidate = True
                    continue
                manifests += 1
                findings.extend(_scan_object(obj, target_label, where))
    if manifests == 0 and parsed_paths:
        # inserted first, so the MAX_REASONS_SHOWN cap in incomplete_findings can never hide it
        reasons.insert(0, _none_held_reason(parsed_paths, rejected_candidate))
    skipped = []
    if counters["yaml_files"]:
        skipped.append({"check": "yaml-manifests", "reason": "skipped:tool-missing", "tool": "PyYAML", "files": counters["yaml_files"]})
    findings.extend({**finding, "location": location} for finding in incomplete_findings(reasons, manifests))
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
