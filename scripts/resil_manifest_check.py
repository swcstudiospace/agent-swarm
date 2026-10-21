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
  * graceful shutdown: resil_shutdown_check with the grace period (default 30 s) and the longest preStop hook (sleep
    action, or `sleep N` commands whose N may carry an s, m, h or d suffix and add up); no-prestop (minor, serving pod
    without a preStop hook); shutdown-invalid.
Bounded: files over 1 MiB are skipped, at most 2000 candidate files are read, and symlinks are never followed. Every skip,
a truncated walk and each YAML file read without PyYAML (`skipped` carries "yaml:skipped:tool-missing (N files)") adds a
major scan-incomplete finding; an empty scan is incomplete too, so `status` is "fail" until every in-scope input is
analysed (swarm/scan_status.py). No external binary is required. Read-only: it writes nothing. `--dry-run` prints
deterministic canned output and touches nothing.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from decimal import Decimal
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
DURATION = r"\d+(?:\.\d+)?[smhd]?"
SLEEP = re.compile(rf"(?:^|[\s;&|(])sleep\s+((?:{DURATION}(?=[\s;&|)]|$)\s*)+)")
SLEEP_TOKEN = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
SLEEP_UNIT_S = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
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


def _candidates(target: Path) -> tuple[list[Path], list[str], bool]:
    """Sorted candidate manifest files below target, the skip reasons of symlinks, and whether the cap cut the walk short.

    Symlinks are never followed: a symlinked file or directory is a reason, not a silent skip.
    """
    if target.is_file():
        if target.suffix not in EXTENSIONS:
            return [], [], False
        if target.is_symlink():
            return [], [f"symlink:{target.name}"], False
        return [target], [], False
    found: list[Path] = []
    reasons: list[str] = []
    for directory, dirnames, filenames in os.walk(target, followlinks=False):
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


def _documents(path: Path, skipped: list[str], yaml_skipped: list[int]) -> list[dict]:
    """Kubernetes objects of one file; problems become `skipped` entries, never exceptions."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            skipped.append(f"too-large:{path.name}")
            return []
        raw = path.read_bytes()
    except OSError as exc:
        skipped.append(f"unreadable:{path.name}:{type(exc).__name__}")
        return []
    if b"\0" in raw[:8192]:
        skipped.append(f"binary:{path.name}")
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        skipped.append(f"binary:{path.name}")
        return []
    if "kind" not in text:
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
    objects: list[dict] = []
    for document in loaded:
        for item in (_l(_d(document).get("items")) if str(_d(document).get("kind", "")).endswith("List") else [document]):
            if isinstance(item, dict) and isinstance(item.get("kind"), str) and isinstance(item.get("apiVersion"), str):
                objects.append(item)
    return objects


def _selector_matches(selector, labels: dict) -> bool:
    """policy/v1 selector semantics: null matches nothing, {} matches every pod of the namespace."""
    if not isinstance(selector, dict):
        return False
    for key, value in _d(selector.get("matchLabels")).items():
        if labels.get(key) != str(value):
            return False
    for expr in _l(selector.get("matchExpressions")):
        expr = _d(expr)
        key, operator, values = expr.get("key"), expr.get("operator"), [str(v) for v in _l(expr.get("values"))]
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


def _probe(raw, name: str) -> dict | None:
    """Container probe in the tool input shape; ValueError for values the API would refuse."""
    if raw is None:
        return None
    raw = _d(raw)
    if "httpGet" in raw:
        http = _d(raw["httpGet"])
        # the target is scheme, host, port and path: the same path on two ports is two targets
        parts = (http.get("scheme", "HTTP"), http.get("host", ""), http.get("port", ""), http.get("path", "/"))
        kind, endpoint = "http", "|".join(str(part) for part in parts)
    elif "tcpSocket" in raw:
        kind, endpoint = "tcp", str(_d(raw["tcpSocket"]).get("port", ""))
    elif "grpc" in raw:
        kind, endpoint = "grpc", str(_d(raw["grpc"]).get("port", ""))
    elif "exec" in raw:
        kind, endpoint = "exec", " ".join(str(part) for part in _l(_d(raw["exec"]).get("command")))
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


def _sleep_seconds(command: str) -> int:
    """Seconds of every `sleep` in a shell command, rounded up. Sequential sleeps add up: never under-count a drain."""
    total = Decimal(0)
    for match in SLEEP.finditer(command):
        for number, unit in SLEEP_TOKEN.findall(match.group(1)):
            total += Decimal(number) * SLEEP_UNIT_S[unit]
    return math.ceil(total)


def _prestop_seconds(containers: list) -> int:
    longest = 0
    for container in containers:
        handler = _d(_d(_d(container).get("lifecycle")).get("preStop"))
        seconds = _int(_d(handler.get("sleep")).get("seconds"), 0) or 0
        command = " ".join(str(part) for part in _l(_d(handler.get("exec")).get("command")))
        longest = max(longest, seconds, _sleep_seconds(command))
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
    keys = [c.get("topologyKey") for c in constraints] + [t.get("topologyKey") for t in required + preferred]
    zone_hard = (any(c.get("topologyKey") in ZONE_KEYS and c.get("whenUnsatisfiable") == "DoNotSchedule" for c in constraints)
                 or any(t.get("topologyKey") in ZONE_KEYS for t in required))
    zone_soft = (any(c.get("topologyKey") in ZONE_KEYS and c.get("whenUnsatisfiable") == "ScheduleAnyway" for c in constraints)
                 or any(t.get("topologyKey") in ZONE_KEYS for t in preferred))
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
    for path in files:
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        before = (len(skipped), yaml_skipped[0])
        objects.extend((rel, obj) for obj in _documents(path, skipped, yaml_skipped))
        if (len(skipped), yaml_skipped[0]) == before:
            analysed += 1
    if yaml_skipped[0]:
        skipped.append(f"yaml:skipped:tool-missing ({yaml_skipped[0]} files)")

    def meta(obj: dict) -> tuple[str, str]:
        metadata = _d(obj.get("metadata"))
        return str(metadata.get("namespace") or "default")[:63], str(metadata.get("name") or "")[:253]

    pdbs = [(rel, obj) for rel, obj in objects if obj["kind"] == "PodDisruptionBudget"]
    hpas = {}
    for _, obj in objects:
        if obj["kind"] == "HorizontalPodAutoscaler":
            ref = _d(_d(obj.get("spec")).get("scaleTargetRef"))
            hpas[(meta(obj)[0], str(ref.get("kind")), str(ref.get("name")))] = _int(_d(obj.get("spec")).get("minReplicas"), 1)
    findings: list[dict] = []
    workloads = 0

    def add(rule: str, severity: str, summary: str, rel: str, workload: str) -> None:
        findings.append({"id": rule, "severity": severity, "summary": summary, "location": rel, "workload": workload})

    for rel, obj in sorted(((r, o) for r, o in objects if o["kind"] in POD_WORKLOADS), key=lambda ro: (ro[0], ro[1]["kind"], meta(ro[1]))):
        namespace, name = meta(obj)
        kind = obj["kind"]
        ref = f"{namespace}/{kind}/{name}"
        spec = _d(obj.get("spec"))
        template = _d(spec.get("template"))
        podspec = _d(template.get("spec"))
        labels = {str(k): str(v) for k, v in _d(_d(template.get("metadata")).get("labels")).items()}
        containers = [c for c in _l(podspec.get("containers")) if isinstance(c, dict)]
        workloads += 1
        replicas = hpas.get((namespace, kind, name), _int(spec.get("replicas"), 1)) if kind in SCALED_WORKLOADS else 1
        # --- PodDisruptionBudget ---
        if kind in SCALED_WORKLOADS:
            matching = [(prel, p) for prel, p in pdbs if meta(p)[0] == namespace
                        and _selector_matches(_d(p.get("spec")).get("selector"), labels)]
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
                entry = {"name": str(c.get("name", ""))[:63] or "container"}
                for key, field in (("startupProbe", "startup"), ("livenessProbe", "liveness"), ("readinessProbe", "readiness")):
                    probe = _probe(c.get(key), key)
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
            prestop = _prestop_seconds(containers)
            result = a25_tools.shutdown_check({
                "workload": name, "serves_traffic": serves, "termination_grace_period_seconds": grace, "prestop_seconds": prestop,
                "app_drain_seconds": 0, "handles_sigterm": True, "keeps_serving_after_sigterm": True, "endpoint_propagation_seconds": 0})
            for f in result["findings"]:
                add(f["id"], f["severity"], f["summary"], rel, ref)
            if serves and not _has_prestop(containers):
                add("no-prestop", "minor", "serving pod without a preStop hook: endpoint removal races SIGTERM; add a short preStop sleep "
                    "or confirm the app keeps serving while it drains", rel, ref)
    for incomplete in incomplete_findings(skipped, analysed):
        findings.append({**incomplete, "location": ".", "workload": ""})
    findings.sort(key=lambda f: (a25_tools.SEVERITIES.index(f["severity"]), f["location"], f["workload"], f["id"], f["summary"]))
    counts = {severity: sum(f["severity"] == severity for f in findings) for severity in a25_tools.SEVERITIES}
    return {"status": scan_status(findings), "findings": findings, "files_scanned": len(files), "workloads_checked": workloads,
            "pdbs_found": len(pdbs), "counts": counts, "skipped": sorted(skipped),
            "summary": f"{len(findings)} finding(s) in {workloads} workload(s) across {len(files)} file(s): "
                       f"{counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info"}


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return dict(DRY_RUN)
    root = ctx.root.resolve()
    target = (root / args.path).resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {root}")
    return _scan(root, target)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A25", "resil_manifest_check", run, description=__doc__, add_args=add_args).main())
