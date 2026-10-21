#!/usr/bin/env python3
"""A25 - scan Kubernetes workload manifests for missing PodDisruptionBudgets, topology spread, probes and graceful-shutdown settings.

Reads Deployment, StatefulSet and DaemonSet manifests (YAML when PyYAML imports, JSON always) under --root/--path plus
the PodDisruptionBudgets and HorizontalPodAutoscalers that belong to them, and reports findings (id, severity
info|minor|major|blocker, summary, location, workload). `status` is "fail" when any major or worse finding exists.

Rules, shared with the A25 tools where one exists (swarm/tools/a25_tools.py):
  * PodDisruptionBudget (Deployment, StatefulSet): resil_pdb_check rules on the effective replica count (the HPA
    minReplicas when an HPA targets the workload, else spec.replicas, default 1), plus pdb-multiple (blocker: the
    eviction API refuses pods covered by more than one PDB) and pdb-invalid.
  * topology spread (replicas >= 2): spread-missing (major, no spread or anti-affinity at all), zone-spread-missing
    (major, none keyed on the zone label), zone-spread-soft-only (minor), deprecated-zone-label (minor).
  * probes: resil_probe_check per container (liveness-missing is not reported, liveness is optional); a container with
    ports counts as serving traffic; probe-invalid (major) for values the API would refuse.
  * graceful shutdown: resil_shutdown_check with the grace period (default 30 s) and the longest preStop hook: a sleep
    action with a positive integer of seconds, or an exec argv `sleep N` or `sh|bash -c "[path/]sleep N"`, where N is a
    non-negative integer of at most 9 digits with an optional suffix s, m, h or d, at most 86400 s; the optional path is
    plain (letters, digits, _, ., - and /). No-prestop (minor, serving pod without a preStop hook); shutdown-invalid. Any
    other hook kind, command form or empty hook, and any hook longer than 86400 s, is not analysed: a skip reason naming
    the workload and the hook kind, the command text never echoed, and never counted as zero.
Bounded: files over 1 MiB are skipped; a document whose expanded tree has more than 200000 nodes (a YAML alias counts at
every use), or a file whose documents together have more than 1000000, is skipped as unparseable. A scan has three allowances
shared by all its files: 8 MiB of file bytes (each file is charged its size before it is read), 10000000 nodes, and 5000000
PDB selector work units (see MAX_SCAN_SELECTOR_COMPARISONS). Every node a walk visits is charged to the node allowance, also
for a file that is then rejected, up to the node at which the walk stops; so the walking of one scan is at most 10000000 + 1
nodes (about 1.2 s on this machine), and the YAML parsing of the bytes is bounded by the 8 MiB allowance. A file that does not fit the byte or node allowance is not read
or not analysed, and the scan fails closed: that file gives scan-byte-budget-spent or scan-node-budget-spent, and every later
file gives not-read-after-scan-byte-budget or not-read-after-scan-node-budget with the count of unread files. A workload whose
PDB comparison needs a selector comparison after that allowance is spent gives scan-selector-budget-spent:<file> (its PDB
verdict is not analysed) and every later one not-compared-after-scan-selector-budget:<n>-workloads, so the scan fails closed.
At most 2000 candidate files are considered; symlinks, a
symlinked --path included, are never followed. `files_scanned` counts the files whose bytes were read within these budgets.
Every skip, unlistable directory, truncated
walk and each YAML file read without PyYAML (`skipped` carries "yaml:skipped:tool-missing (N files)") adds a major
scan-incomplete finding; an empty scan is incomplete too, so `status` is "fail" until every in-scope input is analysed
(swarm/scan_status.py). An invalid replicas or HPA minReplicas is a major replicas-invalid finding. A Deployment,
StatefulSet, DaemonSet, PodDisruptionBudget or HPA whose shape is not the one read is not analysed:
`malformed-workload:<file>:<path>` (the path names the field, never its value; an HPA reason is prefixed with its kind).
That covers a name neither set nor generated, a non-string name or namespace, a non-string label, container or selector
value, a container sequence of non-mappings, a non-mapping selector, an HPA whose scaleTargetRef is not a mapping with a
kind and a name, and a missing apiVersion. A top-level JSON or YAML sequence, or a scalar document, in a file that holds
both apiVersion and kind is an unparseable file, as in A16. A List item without a string kind, a document with apiVersion
and no string kind, a List whose items is not a sequence, or a non-mapping entry each gives a reason. A malformed
budget of the workloads in its namespace unanalysed. No external binary is required. Read-only: it writes nothing.
`--dry-run` prints deterministic canned output and touches nothing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a25_tools  # noqa: E402

try:
    import yaml
except ImportError:  # YAML-dependent checks are reported as skipped, JSON keeps working
    yaml = None

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", "vendor", ".terraform"}
EXTENSIONS = {".yaml", ".yml", ".json"}
POD_WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet")
SCALED_WORKLOADS = ("Deployment", "StatefulSet")
ZONE_KEYS = {a25_tools.ZONE_KEY, a25_tools.LEGACY_ZONE_KEY}
MAX_NODES = 200_000  # per document, counting every use of a YAML alias: an alias bomb is a small file with a huge expanded tree
MAX_FILE_NODES = 1_000_000  # per file, all documents together: many documents that each fit MAX_NODES still cost a walk each
MAX_SCAN_NODES = 10_000_000  # across all files of one scan: every node walked is charged, rejected files included (at most this + 1); about 1.2 s on this machine
MAX_SCAN_BYTES = 8 * 1024 * 1024  # across all files of one scan, charged before each file is read. It bounds parsing (about 2.8 s per 1 MiB file on this machine); the PDB matching is bounded by MAX_SCAN_SELECTOR_COMPARISONS
MAX_SCAN_SELECTOR_COMPARISONS = 5_000_000  # selector work for the whole scan, in units: one per budget candidate taken from the index, per label pair or expression evaluated, and per value of an expression (see _PdbIndex)
SHELLS = ("sh", "bash")
MAX_DURATION_DIGITS = 9  # a duration is read only up to this many digits (about 31 years); a longer digit run is not analysed
PRESTOP_MAX_SECONDS = 86400  # the bound of resil_shutdown_check prestop_seconds (agents.json): a longer hook cannot be checked
NUMBER = rf"[0-9]{{1,{MAX_DURATION_DIGITS}}}"
DURATION = re.compile(rf"({NUMBER})([smhd]?)")  # one integer with at most one suffix: s, m, h or d
PROGRAM_PREFIX = r"(?:[A-Za-z0-9_./-]*/)?"  # an optional plain path: letters, digits, _, ., - and / only, so no shell syntax
SHELL_SLEEP = re.compile(rf"{PROGRAM_PREFIX}sleep ({NUMBER}[smhd]?)")  # the whole `-c` string of a shell form
UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
ANALYSED_KINDS = (*POD_WORKLOADS, "PodDisruptionBudget", "HorizontalPodAutoscaler")
# Helm chart metadata and kustomize configuration: their mappings are objects only with a kind key, so a mapping with apiVersion
# and no kind is silent in these files, as in A21 (k8s_manifest_check.KIND_ONLY_FILE_NAMES).
KIND_ONLY_FILE_NAMES = ("Chart.yaml", "Chart.yml", "kustomization.yaml", "kustomization.yml", "Kustomization")
PROBE_FIELDS = {"initialDelaySeconds": "initial_delay_seconds", "periodSeconds": "period_seconds", "timeoutSeconds": "timeout_seconds",
                "successThreshold": "success_threshold", "failureThreshold": "failure_threshold"}
PROBE_LIMITS = {"initial_delay_seconds": (0, 86400), "period_seconds": (1, 86400), "timeout_seconds": (1, 86400),
                "success_threshold": (1, 100), "failure_threshold": (1, 1000)}
DRY_RUN = {"status": "ok", "dry_run": True, "findings": [], "files_scanned": 0, "workloads_checked": 0, "pdbs_found": 0,
           "counts": {"blocker": 0, "major": 0, "minor": 0, "info": 0}, "skipped": [],
           "summary": "dry-run: resil_manifest_check would scan --path under --root"}


def _d(value) -> dict:
    return value if isinstance(value, dict) else {}


def _l(value) -> list:
    return value if isinstance(value, list) else []


def _int(value, default: int | None = None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _text(value) -> str | None:
    """Scalar as text; None for a mapping, a list or null, which are never converted (str() would expand a shared alias)."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return str(value)
    return None


def _replica_count(value) -> int | None:
    """replicas or minReplicas: absent or null is the API default of 1; a non-negative integer is kept, anything else is None."""
    if value is None:
        return 1
    count = _int(value)
    return count if count is not None and count >= 0 else None


def _node_budget_exceeded(documents: list, remaining: int) -> tuple[str | None, int]:
    """(reason, nodes): the first allowance the walk exceeds, or None, and the nodes walked. Every node walked is charged, so a
    rejected file costs what it walked up to the point of rejection: the scan allowance `remaining` (checked per node, so a walk
    never goes past it), MAX_NODES per document and MAX_FILE_NODES per file."""
    total = 0
    for document in documents:
        count, stack = 0, [document]
        while stack:
            node = stack.pop()
            count += 1
            if total + count > remaining:
                return "scan-node-budget-spent", total + count
            if count > MAX_NODES:
                return f"more-than-{MAX_NODES}-nodes", total + count
            if isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        total += count
        if total > MAX_FILE_NODES:
            return f"more-than-{MAX_FILE_NODES}-nodes-in-file", total
    return None, total


def _budget_namespace(item: dict) -> str:
    """Namespace a malformed PodDisruptionBudget belongs to; "*" when that is unreadable, so it may select any workload."""
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return "*"
    namespace = metadata.get("namespace")
    if namespace is not None and _text(namespace) is None:
        return "*"
    return _text(namespace) or "default"


def _exec_parts(handler: dict, where: str, skipped: list[str]) -> list[str]:
    """argv of a handler's exec action; a scalar command string or a non-scalar argument is a skip reason, not analysed."""
    command = _d(handler.get("exec")).get("command")
    if command is None:
        return []
    if not isinstance(command, list):
        skipped.append(f"{where}: exec.command is a string, not an argument list: not analysed")
        return []
    parts = [_text(part) for part in command]
    if any(part is None for part in parts):
        skipped.append(f"{where}: exec.command has an argument that is not a string or number: not analysed")
        return []
    return parts


def _candidates(target: Path) -> tuple[list[Path], list[str], bool]:
    """Sorted candidate manifest files below target, the skip reasons, and whether the cap cut the walk short.

    Symlinks are never followed: a symlinked file or directory is a reason, not a silent skip. A directory that cannot be
    listed is a reason too, so the manifests inside it are never read without a word.
    """
    if target.is_symlink():
        return [], [f"symlink:{target.name}"], False
    if target.is_file():
        return ([target] if target.suffix in EXTENSIONS else []), [], False
    found: list[Path] = []
    reasons: list[str] = []

    def unlistable(error: OSError) -> None:
        where = Path(error.filename or target)
        try:
            label = where.relative_to(target).as_posix() or "."
        except ValueError:
            label = where.name
        reasons.append(f"unreadable-directory:{label}:{type(error).__name__}")

    for directory, dirnames, filenames in os.walk(target, followlinks=False, onerror=unlistable):
        kept = []
        for name in sorted(dirnames):
            if name in SKIP_DIRS:
                continue
            if (Path(directory) / name).is_symlink():
                reasons.append(f"symlink:{name}")
                continue
            kept.append(name)
        dirnames[:] = kept
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix not in EXTENSIONS:
                continue
            if path.is_symlink():
                reasons.append(f"symlink:{filename}")
                continue
            if len(found) >= MAX_FILES:
                return found, reasons, True
            found.append(path)
    return found, reasons, False


def _kind_of(value) -> str:
    """JSON type of a value, for a skip reason: the value itself is never named."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "sequence"
    if isinstance(value, dict):
        return "mapping"
    return "other"


def _malformed(item: dict) -> list[str]:
    """Paths of the fields this scanner reads that do not have the shape it reads; a container entry also names its type.

    The value is never returned, and a shape that is not the expected one is never coerced to empty or to a default: the
    object is not analysed. A missing name is accepted when metadata.generateName is a non-empty string (create input).
    """
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        return ["metadata"]
    bad: list[str] = []
    generated = metadata.get("generateName")
    name = metadata.get("name")
    if name is not None and _text(name) is None:  # a mapping or list name is malformed even with a generateName (ND5)
        bad.append("metadata.name")
    elif _text(name) in (None, "") and not (isinstance(generated, str) and generated):
        bad.append("metadata.name")
    if metadata.get("namespace") is not None and _text(metadata.get("namespace")) is None:
        bad.append("metadata.namespace")
    kind = item["kind"]
    if kind == "HorizontalPodAutoscaler":
        hpa_spec = item.get("spec")
        ref = hpa_spec.get("scaleTargetRef") if isinstance(hpa_spec, dict) else None
        if not isinstance(hpa_spec, dict):
            bad.append("spec")
        elif not isinstance(ref, dict):
            bad.append("spec.scaleTargetRef")
        else:
            if _text(ref.get("kind")) in (None, ""):
                bad.append("spec.scaleTargetRef.kind")
            if _text(ref.get("name")) in (None, ""):
                bad.append("spec.scaleTargetRef.name")
        return bad
    spec = item.get("spec")
    if not isinstance(spec, dict):
        return [*bad, "spec"]
    if kind in POD_WORKLOADS:
        template = spec.get("template")
        if not isinstance(template, dict):
            bad.append("spec.template")
        else:
            pod_meta = template.get("metadata")
            if pod_meta is not None and not isinstance(pod_meta, dict):
                bad.append("spec.template.metadata")
            labels = _d(pod_meta).get("labels")
            if labels is not None and (not isinstance(labels, dict) or any(_text(value) is None for value in labels.values())):
                bad.append("spec.template.metadata.labels")
            pod_spec = template.get("spec")
            if not isinstance(pod_spec, dict):
                bad.append("spec.template.spec")
            else:
                containers = pod_spec.get("containers")
                if not isinstance(containers, list):
                    bad.append(f"spec.template.spec.containers:{_kind_of(containers)}")
                else:
                    for index, container in enumerate(containers):
                        if not isinstance(container, dict):
                            bad.append(f"spec.template.spec.containers[{index}]:{_kind_of(container)}")
                        elif _text(container.get("name")) in (None, ""):
                            bad.append("spec.template.spec.containers.name")
    if kind == "PodDisruptionBudget":
        selector = spec.get("selector")
        if selector is not None and not isinstance(selector, dict):
            bad.append("spec.selector")
        selector = _d(selector)
        match_labels = selector.get("matchLabels")
        if match_labels is not None and (not isinstance(match_labels, dict)
                                         or any(_text(key) is None or _text(value) is None for key, value in match_labels.items())):
            bad.append("spec.selector.matchLabels")
        expressions = selector.get("matchExpressions")
        if expressions is not None and not isinstance(expressions, list):
            bad.append("spec.selector.matchExpressions")
        for expr in _l(expressions):
            if not isinstance(expr, dict):
                bad.append("spec.selector.matchExpressions")
                continue
            if not isinstance(expr.get("key"), str):
                bad.append("spec.selector.matchExpressions.key")
            values = expr.get("values")
            if values is not None and (not isinstance(values, list) or not all(isinstance(value, str) for value in values)):
                bad.append("spec.selector.matchExpressions.values")
    return bad



def _documents(path: Path, skipped: list[str], yaml_skipped: list[int], unanalysed_budgets: list[str], budget: dict) -> list[dict]:
    """Kubernetes objects of one file; problems become `skipped` entries, never exceptions."""
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            skipped.append(f"too-large:{path.name}")
            return []
        if size > budget["bytes_left"]:
            budget["spent"] = "byte"
            skipped.append(f"scan-byte-budget-spent:{path.name}")
            return []
        budget["bytes_left"] -= size  # charged before the file is read or parsed
        raw = path.read_bytes()
    except OSError as exc:
        skipped.append(f"unreadable:{path.name}:{type(exc).__name__}")
        return []
    budget["read"] += 1
    if b"\0" in raw[:8192]:
        skipped.append(f"binary:{path.name}")
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        skipped.append(f"binary:{path.name}")
        return []
    if "apiVersion" not in text and "kind" not in text.lower():  # neither key: no Kubernetes object, out of scope
        return []
    try:
        if path.suffix == ".json":
            loaded = [json.loads(text)]
        elif yaml is None:
            yaml_skipped[0] += 1
            return []
        else:
            loaded = list(yaml.safe_load_all(text))
    except Exception as exc:  # noqa: BLE001 - template syntax (Helm) or broken documents are skipped, not fatal
        skipped.append(f"unparseable:{path.name}:{type(exc).__name__}")
        return []
    reason, used = _node_budget_exceeded(loaded, budget["nodes_left"])
    budget["nodes_left"] -= used  # every node walked is charged, a rejected file included
    if reason == "scan-node-budget-spent":
        budget["spent"] = "node"
        skipped.append(f"scan-node-budget-spent:{path.name}")
        return []
    if reason:
        skipped.append(f"unparseable:{path.name}:{reason}")
        return []
    shaped = "apiVersion" in text and "kind" in text.lower()  # A16's scope gate, with the same case-folded kind test as the parse gate
    objects: list[dict] = []

    def take(item: dict) -> None:
        kind = item["kind"]
        if kind in ANALYSED_KINDS:
            if not isinstance(item.get("apiVersion"), str):
                skipped.append(f"malformed-workload:{path.name}:{kind}:apiVersion")
                return
            malformed = dict.fromkeys(_malformed(item))
            if malformed:
                prefix = f"{kind}:" if kind == "HorizontalPodAutoscaler" else ""
                skipped.extend(f"malformed-workload:{path.name}:{prefix}{where}" for where in malformed)
                if kind == "PodDisruptionBudget":
                    unanalysed_budgets.append(_budget_namespace(item))
                return
        objects.append(item)

    for document in loaded:
        if document is None:  # an empty YAML document holds nothing
            continue
        if isinstance(document, list):  # as in A16: a top-level sequence is unparseable, its content is not attributed
            if shaped:
                skipped.append(f"unparseable:{path.name}:top-level-sequence")
            return []
        if not isinstance(document, dict):
            if shaped:
                skipped.append(f"non-mapping-document:{path.name}")
            continue
        if str(document.get("kind", "")).endswith("List"):
            items = document.get("items")
            if not isinstance(items, list):
                skipped.append(f"list-items-not-a-sequence:{path.name}")
                continue
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    skipped.append(f"list-item-not-a-mapping:{path.name}:items[{index}]")
                elif not isinstance(item.get("kind"), str):
                    skipped.append(f"list-item-without-kind:{path.name}:items[{index}]")
                else:
                    take(item)
        elif not isinstance(document.get("kind"), str):
            if "apiVersion" in document and path.name not in KIND_ONLY_FILE_NAMES:
                skipped.append(f"document-without-kind:{path.name}")
        else:
            take(document)
    return objects


class _SelectorBudgetSpent(Exception):
    """Raised inside a selector comparison when the scan's selector allowance cannot pay for the next unit of work."""


def _selector_matches(selector, labels: dict, charge=None) -> bool:
    """policy/v1 selector semantics: null matches nothing, {} matches every pod of the namespace. A non-scalar key or value
    is not a label, so it matches nothing. `charge(units)`, when given, is called before each label pair and each expression
    is evaluated, with the work it pays for (one unit, plus one per value of an expression); it raises _SelectorBudgetSpent
    when the allowance is spent, so a comparison never does more work than the allowance pays for."""
    spend = charge if charge is not None else (lambda units=1: None)
    if not isinstance(selector, dict):
        return False
    for key, value in _d(selector.get("matchLabels")).items():
        spend()
        text_key, want = _text(key), _text(value)
        if text_key is None or want is None or labels.get(text_key) != want:
            return False
    for expr in _l(selector.get("matchExpressions")):
        expr = _d(expr)
        raw_values = _l(expr.get("values"))
        spend(1 + len(raw_values))
        key, operator = _text(expr.get("key")), expr.get("operator")
        values = [_text(v) for v in raw_values]
        if key is None:
            return False
        present = key in labels
        if operator == "In":
            ok = present and labels[key] in values
        elif operator == "NotIn":
            ok = not present or labels[key] not in values
        elif operator == "Exists":
            ok = present
        elif operator == "DoesNotExist":
            ok = not present
        else:
            return False
        if not ok:
            return False
    return True


class _PdbIndex:
    """The PodDisruptionBudgets of one scan, for the selector comparison of each workload (see `matching`).

    A budget with matchLabels is filed under its first matchLabels pair in sorted order. It can select only a workload whose
    labels contain every pair of its matchLabels, so it is never compared with a workload that lacks the filed pair. A workload
    is compared with the budgets filed under its own label pairs and with every budget of its namespace that has no matchLabels
    (matchExpressions only, an empty selector, or none). The verdicts equal a comparison with every budget of the namespace. Each
    unit of selector work made counts against MAX_SCAN_SELECTOR_COMPARISONS for the whole scan (see _selector_matches).
    """

    def __init__(self, budgets: list[tuple[str, dict, str]]):
        self.budgets = budgets  # (file, budget, namespace) in scan order: the position breaks every tie
        self.limit = MAX_SCAN_SELECTOR_COMPARISONS
        self.comparisons = 0
        self.filed: dict[tuple[str, tuple], list[int]] = {}
        self.unlabelled: dict[str, list[int]] = {}
        for position, (_, obj, namespace) in enumerate(budgets):
            selector = _d(_d(obj.get("spec")).get("selector"))
            pairs = sorted(((_text(key), _text(value)) for key, value in _d(selector.get("matchLabels")).items()),
                           key=lambda pair: (str(pair[0]), str(pair[1])))
            if pairs:
                self.filed.setdefault((namespace, pairs[0]), []).append(position)
            else:
                self.unlabelled.setdefault(namespace, []).append(position)

    def _charge(self, units: int = 1) -> None:
        """Spend `units` of the scan's selector allowance before the work they pay for; raises _SelectorBudgetSpent if they do not fit."""
        if self.comparisons + units > self.limit:
            raise _SelectorBudgetSpent()
        self.comparisons += units

    def matching(self, namespace: str, labels: dict) -> list[tuple[str, dict]] | None:
        """The (file, budget) pairs of `namespace` that select a workload with `labels`, in scan order; None when the allowance
        runs out first. The units spent before that stay counted. The unlabelled budgets and the budgets filed under each label
        pair are paid for before they are used; a label pair with no filed budget costs nothing. The comparisons then pay for
        their own work (see _selector_matches), so a workload with no candidate keeps its exact verdict after the allowance is spent."""
        try:
            unlabelled = self.unlabelled.get(namespace, [])
            self._charge(len(unlabelled))
            candidates = list(unlabelled)
            for pair in labels.items():
                filed = self.filed.get((namespace, pair), [])
                if filed:  # a label pair with no filed budget costs nothing: a dictionary miss compares nothing
                    self._charge(len(filed))
                    candidates.extend(filed)
            hits: list[tuple[str, dict]] = []
            for position in sorted(candidates):
                rel, obj, _ = self.budgets[position]
                if _selector_matches(_d(obj.get("spec")).get("selector"), labels, self._charge):
                    hits.append((rel, obj))
            return hits
        except _SelectorBudgetSpent:
            return None



def _probe(raw, name: str, where: str, skipped: list[str]) -> dict | None:
    """Container probe in the tool input shape; ValueError for values the API would refuse."""
    if raw is None:
        return None
    raw = _d(raw)
    if "httpGet" in raw:
        http = _d(raw["httpGet"])
        # the target is scheme, host, port and path: the same path on two ports is two targets
        parts = (_text(http.get("scheme", "HTTP")), _text(http.get("host", "")), _text(http.get("port", "")), _text(http.get("path", "/")))
        kind, endpoint = "http", "|".join(part or "" for part in parts)
    elif "tcpSocket" in raw:
        kind, endpoint = "tcp", _text(_d(raw["tcpSocket"]).get("port", "")) or ""
    elif "grpc" in raw:
        kind, endpoint = "grpc", _text(_d(raw["grpc"]).get("port", "")) or ""
    elif "exec" in raw:
        kind, endpoint = "exec", " ".join(_exec_parts(raw, where, skipped))
    else:
        raise ValueError(f"{name} probe has no httpGet, tcpSocket, grpc or exec handler")
    probe = {"kind": kind, "endpoint": endpoint[:200]}
    for source, target in PROBE_FIELDS.items():
        if source in raw:
            low, high = PROBE_LIMITS[target]
            value = _int(raw[source])
            if value is None or not low <= value <= high:
                raise ValueError(f"{name}.{source} must be an integer in {low}..{high}")
            probe[target] = value
    return probe


def _duration_seconds(value) -> int | None:
    """Seconds of one duration: a non-negative integer, or one integer of at most MAX_DURATION_DIGITS digits with an optional
    suffix s, m, h or d. Anything else (a float, two tokens, a longer digit run) is None and is never converted."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < 10**MAX_DURATION_DIGITS else None
    if isinstance(value, str):
        match = DURATION.fullmatch(value)
        if match:
            return int(match.group(1)) * UNIT_SECONDS[match.group(2)]
    return None


def _program(value, name: str) -> bool:
    """True when value is the program `name`, or a plain path ending in it: a shell metacharacter never matches."""
    return isinstance(value, str) and re.fullmatch(PROGRAM_PREFIX + name, value) is not None


def _sleep_argv_seconds(argv: list) -> int | None:
    """Seconds of `sleep N[suffix]` (an argv of two) or of `sh|bash -c "[path/]sleep N[suffix]"` (an argv of three); None for
    any other command."""
    if not argv:
        return None
    first = argv[0]
    if len(argv) == 2 and _program(first, "sleep"):
        return _duration_seconds(argv[1])
    if len(argv) == 3 and any(_program(first, shell) for shell in SHELLS) and argv[1] == "-c" and isinstance(argv[2], str):
        match = SHELL_SLEEP.fullmatch(argv[2])
        return _duration_seconds(match.group(1)) if match else None
    return None


def _exec_seconds(exec_handler, here: str, skipped: list[str]) -> int:
    """Seconds of a preStop exec hook, or 0 with a skip reason. The command text is never echoed."""
    if not isinstance(exec_handler, dict):
        skipped.append(f"{here}: preStop exec is not a mapping: not analysed")
        return 0
    command = exec_handler.get("command")
    if isinstance(command, str):
        skipped.append(f"{here}: preStop exec.command is a string, not an argument list: not analysed")
        return 0
    if not isinstance(command, list):
        skipped.append(f"{here}: preStop exec.command is not an argument list: not analysed")
        return 0
    if any(part is None or isinstance(part, (bool, dict, list)) for part in command):
        skipped.append(f"{here}: preStop exec.command has an argument that is not a string or number: not analysed")
        return 0
    seconds = _sleep_argv_seconds(command)
    if seconds is None:
        skipped.append(f"{here}: preStop exec command is not a recognised sleep form: not analysed")
        return 0
    return seconds


def _admit(value: int, here: str, skipped: list[str], current: int) -> int:
    """The longer of current and one hook's seconds. A hook above the shutdown check's bound is a skip reason, never counted."""
    if value > PRESTOP_MAX_SECONDS:
        skipped.append(f"{here}: preStop hook is longer than {PRESTOP_MAX_SECONDS} s, the bound of the shutdown check: not analysed")
        return current
    return max(current, value)


def _hook_seconds(handler: dict, here: str, skipped: list[str]) -> int:
    """Seconds one preStop handler holds the container for. A hook with no kind, or a kind without a duration, is a skip
    reason, never zero."""
    if not handler:
        skipped.append(f"{here}: preStop hook names no hook kind (exec, sleep, httpGet or tcpSocket): not analysed")
        return 0
    seconds = 0
    for kind in handler:
        if kind == "sleep":
            value = _int(_d(handler.get("sleep")).get("seconds"))
            if value is None:
                skipped.append(f"{here}: preStop sleep.seconds is not an integer: not analysed")
            elif not 0 < value < 10**MAX_DURATION_DIGITS:
                skipped.append(f"{here}: preStop sleep.seconds is not a positive integer of at most {MAX_DURATION_DIGITS} digits: "
                               "not analysed")
            else:
                seconds = _admit(value, here, skipped, seconds)
        elif kind == "exec":
            seconds = _admit(_exec_seconds(handler.get("exec"), here, skipped), here, skipped, seconds)
        else:
            label = kind if kind in ("httpGet", "tcpSocket") else "unrecognised"
            skipped.append(f"{here}: preStop {label} hook has no computable duration: not analysed")
    return seconds


def _prestop_seconds(containers: list, where: str, skipped: list[str]) -> int:
    """Longest preStop hook of the containers, in seconds. Every hook without a readable duration is a skip reason."""
    longest = 0
    for container in containers:
        here = f"{where} container {_text(container.get('name'))[:63]}"
        lifecycle = container.get("lifecycle")
        if lifecycle is None:
            continue
        if not isinstance(lifecycle, dict):
            skipped.append(f"{here}: lifecycle is not a mapping: preStop not analysed")
            continue
        handler = lifecycle.get("preStop")
        if handler is None:
            continue
        if not isinstance(handler, dict):
            skipped.append(f"{here}: preStop hook is not a mapping: not analysed")
            continue
        longest = max(longest, _hook_seconds(handler, here, skipped))
    return longest


def _has_prestop(containers: list) -> bool:
    return any(_d(_d(_d(c).get("lifecycle")).get("preStop")) for c in containers)


def _spread(podspec: dict, replicas: int) -> list[tuple[str, str, str]]:
    constraints = [_d(c) for c in _l(podspec.get("topologySpreadConstraints"))]
    anti = _d(_d(podspec.get("affinity")).get("podAntiAffinity"))
    required = [_d(t) for t in _l(anti.get("requiredDuringSchedulingIgnoredDuringExecution"))]
    preferred = [_d(_d(t).get("podAffinityTerm")) for t in _l(anti.get("preferredDuringSchedulingIgnoredDuringExecution"))]
    if replicas < 2:
        return []
    out: list[tuple[str, str, str]] = []
    keys = [_text(c.get("topologyKey")) for c in constraints] + [_text(t.get("topologyKey")) for t in required + preferred]
    zone_hard = (any(_text(c.get("topologyKey")) in ZONE_KEYS and c.get("whenUnsatisfiable") == "DoNotSchedule" for c in constraints)
                 or any(_text(t.get("topologyKey")) in ZONE_KEYS for t in required))
    zone_soft = (any(_text(c.get("topologyKey")) in ZONE_KEYS and c.get("whenUnsatisfiable") == "ScheduleAnyway" for c in constraints)
                 or any(_text(t.get("topologyKey")) in ZONE_KEYS for t in preferred))
    if not keys:
        out.append(("spread-missing", "major", f"{replicas} replicas with no topologySpreadConstraints or podAntiAffinity: they may share one node or zone"))
    elif not zone_hard and not zone_soft:
        out.append(("zone-spread-missing", "major", f"spread rules exist but none use {a25_tools.ZONE_KEY}: a zone outage can take every replica"))
    elif not zone_hard:
        out.append(("zone-spread-soft-only", "minor", "zone spread is only preferred (ScheduleAnyway or preferred anti-affinity)"))
    if a25_tools.LEGACY_ZONE_KEY in keys:
        out.append(("deprecated-zone-label", "minor", f"{a25_tools.LEGACY_ZONE_KEY} is deprecated; use {a25_tools.ZONE_KEY}"))
    return out


def _scan(root: Path, target: Path) -> dict:
    skipped: list[str] = []
    yaml_skipped = [0]
    files, reasons, truncated = _candidates(target)
    skipped.extend(reasons)
    if truncated:
        skipped.append(f"limit:{MAX_FILES}-files")
    analysed = 0
    objects: list[tuple[str, dict]] = []
    unanalysed_budgets: list[str] = []
    budget = {"nodes_left": MAX_SCAN_NODES, "bytes_left": MAX_SCAN_BYTES, "spent": None, "read": 0}
    for index, path in enumerate(files):
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        before = (len(skipped), yaml_skipped[0])
        objects.extend((rel, obj) for obj in _documents(path, skipped, yaml_skipped, unanalysed_budgets, budget))
        if budget["spent"]:
            unread = len(files) - index - 1
            if unread:
                skipped.append(f"not-read-after-scan-{budget['spent']}-budget:{unread}-files")
            break
        if (len(skipped), yaml_skipped[0]) == before:
            analysed += 1
    if yaml_skipped[0]:
        skipped.append(f"yaml:skipped:tool-missing ({yaml_skipped[0]} files)")

    def meta(obj: dict) -> tuple[str, str]:
        metadata = _d(obj.get("metadata"))
        return (_text(metadata.get("namespace")) or "default")[:63], (_text(metadata.get("name")) or _text(metadata.get("generateName")) or "")[:253]

    pdbs = [(rel, obj) for rel, obj in objects if obj["kind"] == "PodDisruptionBudget"]
    budgets = _PdbIndex([(rel, obj, meta(obj)[0]) for rel, obj in pdbs])
    hpas: dict[tuple[str, str, str], int | None] = {}
    for _, obj in objects:
        if obj["kind"] == "HorizontalPodAutoscaler":
            spec = _d(obj.get("spec"))
            ref = _d(spec.get("scaleTargetRef"))
            hpas[(meta(obj)[0], _text(ref.get("kind")) or "", _text(ref.get("name")) or "")] = _replica_count(spec.get("minReplicas"))
    findings: list[dict] = []
    workloads = 0
    selector_state = {"spent_in": None, "later": 0}

    def add(rule: str, severity: str, summary: str, rel: str, workload: str) -> None:
        findings.append({"id": rule, "severity": severity, "summary": summary, "location": rel, "workload": workload})

    for rel, obj in sorted(((r, o) for r, o in objects if o["kind"] in POD_WORKLOADS), key=lambda ro: (ro[0], ro[1]["kind"], meta(ro[1]))):
        namespace, name = meta(obj)
        kind = obj["kind"]
        ref = f"{namespace}/{kind}/{name}"
        spec = _d(obj.get("spec"))
        template = _d(spec.get("template"))
        podspec = _d(template.get("spec"))
        labels: dict[str, str] = {}
        for raw_key, raw_value in _d(_d(template.get("metadata")).get("labels")).items():
            text_key, text_value = _text(raw_key), _text(raw_value)
            if text_key is not None and text_value is not None:
                labels[text_key] = text_value
        containers = [c for c in _l(podspec.get("containers")) if isinstance(c, dict)]
        workloads += 1
        replicas: int | None = 1
        if kind in SCALED_WORKLOADS:
            key = (namespace, kind, name)
            if key in hpas:
                replicas, source = hpas[key], "HorizontalPodAutoscaler minReplicas"
            else:
                replicas, source = _replica_count(spec.get("replicas")), "replicas"
            if replicas is None:
                add("replicas-invalid", "major", f"{source} must be a non-negative integer", rel, ref)
        # --- PodDisruptionBudget ---
        unknown_budget = kind in SCALED_WORKLOADS and any(budget in (namespace, "*") for budget in unanalysed_budgets)
        if unknown_budget:
            skipped.append(f"{rel}:{ref}: a malformed PodDisruptionBudget may select this workload: its budget is not analysed")
        matching = None
        if kind in SCALED_WORKLOADS and replicas is not None and not unknown_budget:
            matching = budgets.matching(namespace, labels)
            if matching is None:  # the selector allowance ran out before this workload was compared: its PDB verdict is not analysed
                if selector_state["spent_in"] is None:
                    selector_state["spent_in"] = rel
                    skipped.append(f"scan-selector-budget-spent:{rel}")
                else:
                    selector_state["later"] += 1
        if matching is not None:
            if len(matching) > 1:
                add("pdb-multiple", "blocker", f"{len(matching)} PodDisruptionBudgets select these pods ({', '.join(meta(p)[1] for _, p in matching)}): "
                    "the eviction API refuses pods covered by more than one budget", rel, ref)
            payload = {"workloads": [{"name": name, "kind": kind, "replicas": replicas}]}
            pspec = _d(matching[0][1].get("spec")) if matching else {}
            if matching and not any(key in pspec for key in ("minAvailable", "maxUnavailable")):
                # the budget is reported once, as invalid: an empty budget is not also reported as a missing one
                add("pdb-invalid", "major", f"PodDisruptionBudget {meta(matching[0][1])[1]} sets neither minAvailable nor maxUnavailable",
                    matching[0][0], ref)
            else:
                entry = payload["workloads"][0]
                for source, target_key in (("minAvailable", "min_available"), ("maxUnavailable", "max_unavailable")):
                    if source in pspec:
                        entry[target_key] = "" if isinstance(pspec[source], bool) else str(pspec[source])
                policy = pspec.get("unhealthyPodEvictionPolicy")
                if policy in ("IfHealthyBudget", "AlwaysAllow"):
                    entry["unhealthy_pod_eviction_policy"] = policy
                result = a25_tools.pdb_check(payload)
                if result["state"] == "INVALID_INPUT":
                    add("pdb-invalid", "major", result["message"], matching[0][0] if matching else rel, ref)
                else:
                    for f in result["findings"]:
                        add(f["id"], f["severity"], f["summary"], matching[0][0] if matching and f["id"] != "pdb-missing" else rel, ref)
        if kind in SCALED_WORKLOADS and replicas is not None:
            # --- topology spread ---
            for rule, severity, summary in _spread(podspec, replicas):
                add(rule, severity, summary, rel, ref)
        # --- probes ---
        serves = any(_l(_d(c).get("ports")) for c in containers)
        # Pod readiness is the AND of its containers' readiness: only a container that exposes ports takes traffic, so a
        # portless sidecar (log shipper, agent) is checked as a worker and is not asked for a readiness probe.
        probe_groups: dict[bool, list[dict]] = {True: [], False: []}
        try:
            for c in containers:
                entry = {"name": _text(c.get("name"))[:63]}
                for key, field in (("startupProbe", "startup"), ("livenessProbe", "liveness"), ("readinessProbe", "readiness")):
                    probe = _probe(c.get(key), key, f"{rel}:{entry['name']}", skipped)
                    if probe is not None:
                        entry[field] = probe
                probe_groups[bool(_l(c.get("ports")))].append(entry)
            for group_serves, group in probe_groups.items():
                if not group:
                    continue
                result = a25_tools.probe_check({"workload": name, "serves_traffic": group_serves, "containers": group})
                if result["state"] == "INVALID_INPUT":
                    add("probe-invalid", "major", result["message"], rel, ref)
                else:
                    for f in result["findings"]:
                        if f["id"] != "liveness-missing":
                            add(f["id"], f["severity"], f"{f['subject'].split('/', 1)[-1]}: {f['summary']}", rel, ref)
        except ValueError as exc:
            add("probe-invalid", "major", str(exc), rel, ref)
        # --- graceful shutdown ---
        grace = podspec.get("terminationGracePeriodSeconds", 30)
        if _int(grace) is None or not 0 <= grace <= 86400:
            add("shutdown-invalid", "major", "terminationGracePeriodSeconds must be an integer in 0..86400", rel, ref)
        else:
            prestop = _prestop_seconds(containers, f"{rel}:{ref}", skipped)
            result = a25_tools.shutdown_check({
                "workload": name, "serves_traffic": serves, "termination_grace_period_seconds": grace, "prestop_seconds": prestop,
                "app_drain_seconds": 0, "handles_sigterm": True, "keeps_serving_after_sigterm": True, "endpoint_propagation_seconds": 0})
            for f in result["findings"]:
                add(f["id"], f["severity"], f["summary"], rel, ref)
            if serves and not _has_prestop(containers):
                add("no-prestop", "minor", "serving pod without a preStop hook: endpoint removal races SIGTERM; add a short preStop sleep "
                    "or confirm the app keeps serving while it drains", rel, ref)
    if selector_state["later"]:
        skipped.append(f"not-compared-after-scan-selector-budget:{selector_state['later']}-workloads")
    for incomplete in incomplete_findings(skipped, analysed):
        findings.append({**incomplete, "location": ".", "workload": ""})
    findings.sort(key=lambda f: (a25_tools.SEVERITIES.index(f["severity"]), f["location"], f["workload"], f["id"], f["summary"]))
    counts = {severity: sum(f["severity"] == severity for f in findings) for severity in a25_tools.SEVERITIES}
    return {"status": scan_status(findings), "findings": findings, "files_scanned": budget["read"], "workloads_checked": workloads,
            "pdbs_found": len(pdbs), "counts": counts, "skipped": sorted(skipped),
            "summary": f"{len(findings)} finding(s) in {workloads} workload(s) across {budget['read']} file(s): "
                       f"{counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info"}


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return dict(DRY_RUN)
    root = ctx.root.resolve()
    requested = root / args.path
    target = requested.resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {root}")
    # a symlinked --path is reported as the walk reports a symlink: never followed, so the scan is incomplete
    return _scan(root, requested if requested.is_symlink() else target)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A25", "resil_manifest_check", run, description=__doc__, add_args=add_args).main())
