#!/usr/bin/env python3
"""A16 - scan a repository for cloud-native readiness signals (env-based config, Dockerfile and health probes, graceful shutdown, multi-AZ/region declarations in IaC, tagging) and score them per Well-Architected pillar.

Walks the files under --root (optionally narrowed by --path) and looks for: environment-based configuration and SIGTERM
handling in application source, Dockerfile health checks and shell-form entrypoints (SIGTERM is not forwarded through
`sh -c`), Kubernetes workloads (probes, resource requests, replicas, zone spread, termination handling, autoscalers),
Terraform and CloudFormation declarations (multi-AZ, backups, encryption, tags, arm64/lifecycle signals), a committed
`.env` file and a CI pipeline definition. Every rule is a Well-Architected question; the answers are scored by the same
`cloud_wellarch_scorecard` logic the A16 tool uses (swarm/tools/a16_tools.py), so the scan and the tool never disagree.

The scan is a heuristic over text: it can miss signals set through modules, Helm values or generated code, and it reads
IaC with regular expressions, not an HCL parser. Read-only and bounded: files over 1 MiB, binaries, unreadable entries, symlinks and
symlinked directories are not analysed, and the walk stops after 2000 files. Each such in-scope input, and each Kubernetes manifest
that is not parsed (PyYAML missing, not valid YAML/JSON, or a malformed workload), is a reason in one major `scan-incomplete` finding,
so `status` is `fail` until every in-scope input is analysed (swarm/scan_status.py). Kubernetes manifests are parsed structurally when
PyYAML imports (JSON manifests are always parsed natively). No external
binary (kubectl, helm, terraform, ...) is looked up or required. Findings carry severities info|minor|major|blocker and
`status` is `fail` when any major or worse finding exists. The scan reads the working tree, not git: run it on a clean checkout, where a
`.env` file can only be there because it is tracked. Strings from the scanned files (values, secrets) are never copied into the output;
findings carry rule text, relative paths and Kubernetes workload and container names only (a credential-shaped name becomes a label). `--dry-run` prints canned deterministic output and reads nothing.
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
from swarm.script_base import AgentScript  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.tools import a16_tools  # noqa: E402

try:  # optional: structural Kubernetes parsing
    import yaml
except ImportError:  # pragma: no cover - exercised by tests through the module attribute
    yaml = None

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", "vendor", ".terraform", ".tox"})
TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "specs", "testdata", "fixtures"})
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
SOURCE_SUFFIXES = frozenset({".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".java", ".kt", ".rb", ".rs", ".cs", ".php"})
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")
SCALED_KINDS = ("HorizontalPodAutoscaler", "ScaledObject")  # ScaledObject: KEDA
CI_NAMES = frozenset({".gitlab-ci.yml", "jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".travis.yml", "cloudbuild.yaml"})
ENV_SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tpl")

ENV_READ = re.compile(r"os\.environ|os\.getenv|\bgetenv\(|process\.env|os\.Getenv|os\.LookupEnv|System\.getenv|\bENV\[|\bENV\.fetch|"
                      r"env::var|Environment\.GetEnvironmentVariable|\$_ENV")
SIGTERM = re.compile(r"SIGTERM|signal\.Notify|addShutdownHook|ShutdownHook|GracefulShutdown|graceful[_ ]?shutdown|\.Shutdown\(|"
                     r"tokio::signal|SmartLifecycle|server\.close\(")
COMPUTE_OR_DATA = re.compile(
    r"aws_(?:instance|db_instance|rds_cluster|autoscaling_group|eks_cluster|ecs_service|elasticache_\w+|lb|alb)\b|"
    r"google_(?:compute_instance|sql_database_instance|container_cluster)\b|"
    r"azurerm_(?:linux_virtual_machine|windows_virtual_machine|postgresql\w*|mysql\w*|mssql\w*|kubernetes_cluster)\b|"
    r"AWS::(?:EC2::Instance|RDS::DBInstance|RDS::DBCluster|AutoScaling::AutoScalingGroup|ECS::Service|EKS::Cluster|"
    r"ElasticLoadBalancingV2::LoadBalancer)\b")
DATABASE = re.compile(r"aws_db_instance\b|aws_rds_cluster\b|google_sql_database_instance\b|azurerm_(?:postgresql|mysql|mssql)\w*|"
                      r"AWS::RDS::DB(?:Instance|Cluster)\b")
MULTI_AZ = re.compile(
    r"multi_az\"?\s*[=:]\s*true|MultiAZ:\s*true|zone_redundant\"?\s*[=:]\s*true|ZoneRedundant:\s*true|"
    r"availability_zone_count\"?\s*[=:]\s*(?:[2-9]|\d\d)|data\s+\"aws_availability_zones\"|Fn::GetAZs|!GetAZs|"
    r"(?:availability_zones|zones|azs|subnets|subnet_ids|SubnetIds|AvailabilityZones)\"?\s*[=:]\s*\[[^\]\n]*,[^\]\n]*\]")
MULTI_AZ_OFF = re.compile(r"multi_az\"?\s*[=:]\s*false|MultiAZ:\s*false")
BACKUP = re.compile(r"backup_retention_period\"?\s*[=:]\s*[1-9]|BackupRetentionPeriod:\s*[1-9]|point_in_time_recovery|"
                    r"backup_configuration\s*\{|backup_retention_days|\bbackup\s*\{")
ENCRYPTION = re.compile(r"(?:storage_)?encrypted\"?\s*[=:]\s*true|StorageEncrypted:\s*true|Encrypted:\s*true|kms_key_(?:id|arn|name)|"
                        r"KmsKeyId|SSEAlgorithm|server_side_encryption|disk_encryption|customer_managed_key")
AUTOSCALING = re.compile(r"aws_appautoscaling_target|aws_autoscaling_group|google_compute_(?:region_)?autoscaler|"
                         r"azurerm_monitor_autoscale_setting|enable_autoscaling|AWS::AutoScaling::|AWS::ApplicationAutoScaling::|"
                         r"min_capacity|max_capacity|autoscaling_group")
TAGS_PRESENT = re.compile(r"\b(?:tags|default_tags|labels|common_tags)\"?\s*(?:=|:|\{)|^\s*Tags:", re.M)
EFFICIENCY = re.compile(r"\barm64\b|aarch64|\b[a-z]{1,3}\d+g[a-z]{0,2}\.[a-z0-9]+\b|lifecycle_rule|lifecycle_configuration|"
                        r"LifecycleConfiguration|expiration\b|retention_in_days|RetentionInDays|min_capacity\"?\s*[=:]\s*0\b|minReplicas:\s*0\b")
HELM_PROBE = re.compile(r"(?:readiness|liveness|startup)Probe:")
HELM_REQUESTS = re.compile(r"\brequests:")
HELM_SPREAD = re.compile(r"topologySpreadConstraints:|podAntiAffinity:")
HELM_GRACE = re.compile(r"terminationGracePeriodSeconds:|preStop:")
HELM_KIND = re.compile(r"^kind:\s*(Deployment|StatefulSet|DaemonSet)\s*$", re.M)
HELM_REPLICAS = re.compile(r"^\s*replicas:\s*(\d+)\s*$", re.M)
HELM_DOCUMENT = re.compile(r"^---\s*$", re.M)
REQUIRED_TAGS = {
    "owner": ("owner", "team", "service-owner", "service_owner"),
    "environment": ("environment", "env", "stage"),
    "cost-center": ("cost-center", "cost_center", "costcenter", "cost-centre", "costcentre"),
}

# rule key -> (pillar, weight). One Well-Architected answer per rule; its risk follows the worst finding of the rule.
RULES = {
    "ci-pipeline": ("operational_excellence", 3),
    "env-config": ("operational_excellence", 2),
    "env-file": ("security", 5),
    "encryption": ("security", 3),
    "health-probes": ("reliability", 4),
    "multi-az": ("reliability", 5),
    "backups": ("reliability", 3),
    "graceful-shutdown": ("reliability", 3),
    "replicas": ("reliability", 2),
    "autoscaling": ("performance_efficiency", 3),
    "resource-requests": ("performance_efficiency", 2),
    "tagging": ("cost_optimization", 3),
    "efficiency": ("sustainability", 2),
}
PILLAR_ORDER = ("operational_excellence", "security", "reliability", "performance_efficiency", "cost_optimization", "sustainability")


def _finding(rule: str, fid: str, severity: str, summary: str, location: str = "") -> dict:
    return {"rule": rule, "id": f"cloud.{fid}", "severity": severity, "summary": summary, "location": a16_tools._safe_label(location)}


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "(outside the root)"


def _reason(kind: str, rel: str, where: str = "") -> str:
    """One reason an in-scope input was not analysed: the skip kind, its path (through the label rule) and, when given, where in it."""
    return f"skipped:{kind}: {a16_tools._safe_label(rel)}" + (f" ({where})" if where else "")


def _read_text(path: Path) -> tuple[str | None, str]:
    """(text, ''): the text of a regular, non-binary file within the size bound. Otherwise (None, why), why being the skip kind."""
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        return None, "unreadable"
    if len(data) > MAX_FILE_BYTES:
        return None, "too-large"
    if b"\0" in data[:4096]:
        return None, "binary"
    return data.decode("utf-8", errors="replace"), ""


def _instructions(text: str) -> list[tuple[str, str]]:
    """Dockerfile instructions as (KEYWORD, argument) with line continuations joined and comments dropped."""
    joined, pending = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        joined.append((pending + line).strip())
        pending = ""
    if pending.strip():
        joined.append(pending.strip())
    parsed = []
    for line in joined:
        keyword, *argument = re.split(r"\s+", line, maxsplit=1)  # any whitespace separates the keyword, as Docker does
        parsed.append((keyword.upper(), argument[0].strip() if argument else ""))
    return parsed


def _dockerfile_facts(text: str) -> dict:
    """Facts of the final build stage: HEALTHCHECK present, and whether the launching instruction is shell form."""
    final: list[tuple[str, str]] = []
    for keyword, argument in _instructions(text):
        if keyword == "FROM":
            final = []
        final.append((keyword, argument))
    healthcheck = any(k == "HEALTHCHECK" and not a.upper().startswith("NONE") for k, a in final)
    launcher = next((entry for kind in ("ENTRYPOINT", "CMD") for entry in reversed(final) if entry[0] == kind), None)
    shell_form = bool(launcher) and not launcher[1].startswith("[") and not launcher[1].startswith("exec ")
    return {"healthcheck": healthcheck, "shell_form": shell_form, "launcher": launcher[0] if launcher else ""}


def _tag_keys(text: str) -> set[str]:
    lowered = text.lower()
    found = set()
    for canonical, aliases in REQUIRED_TAGS.items():
        for alias in aliases:
            name = re.escape(alias)
            if re.search(r"[\"'\s]" + name + r"[\"']?\s*[:=]", lowered) or re.search(r"key\"?\s*[:=]\s*[\"']?" + name + r"[\"']?\s*(?:[,}\n]|$)", lowered):
                found.add(canonical)
                break
    return found


class _Malformed(Exception):
    """A workload with a non-mapping nested field: the scan counts it and skips it, the rest of the file is still scanned."""


def _mapping(value) -> dict:
    """A nested field as a mapping: a missing or empty value reads as {}; any other non-mapping value makes the workload malformed."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    raise _Malformed


def _workload(doc: dict, path: str) -> dict | None:
    """Facts of a Deployment, StatefulSet or DaemonSet; None for other kinds. Raises _Malformed when the workload has no spec mapping
    (missing, null or another type) or a nested field is not a mapping: the scan records a reason, never a silent skip."""
    if doc.get("kind") not in WORKLOAD_KINDS:
        return None
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        raise _Malformed
    pod = _mapping(_mapping(spec.get("template")).get("spec"))
    metadata = _mapping(doc.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    raw_containers = pod.get("containers") or []
    if not isinstance(raw_containers, list):
        raise _Malformed
    containers = [c for c in raw_containers if isinstance(c, dict)]
    resources = [_mapping(c.get("resources")) for c in containers]
    lifecycles = [_mapping(c.get("lifecycle")) for c in containers]
    affinity = _mapping(pod.get("affinity"))
    replicas = spec.get("replicas")
    if replicas is None and doc["kind"] != "DaemonSet":
        replicas = 1  # apps/v1 defaults an unset or null spec.replicas to 1
    return {
        "path": path, "kind": doc["kind"], "name": a16_tools._safe_label(str(metadata.get("name", "?"))),
        "replicas": replicas if isinstance(replicas, int) and not isinstance(replicas, bool) else None,
        "has_probe": any(c.get(k) for c in containers for k in ("readinessProbe", "livenessProbe", "startupProbe")),
        "no_requests": sorted(a16_tools._safe_label(str(c.get("name", "?"))) for c, res in zip(containers, resources) if not res.get("requests")),
        "spread": bool(pod.get("topologySpreadConstraints") or affinity.get("podAntiAffinity")),
        "graceful": "terminationGracePeriodSeconds" in pod or any(lc.get("preStop") for lc in lifecycles),
        "labels": sorted(str(key) for key in labels),
    }


def _helm_workloads(text: str, path: str) -> list[dict]:
    """Template files cannot be parsed as YAML: use text signals, one workload per kind line. Each `---` document is read on its own
    text, so a kind's replicas, probes, requests and spread come from its own document, never from another kind's."""
    workloads = []
    for document in HELM_DOCUMENT.split(text):
        replicas = HELM_REPLICAS.search(document)
        for kind in HELM_KIND.findall(document):
            workloads.append({
                "path": path, "kind": kind, "name": "(helm template)", "replicas": int(replicas.group(1)) if replicas else None,
                "has_probe": bool(HELM_PROBE.search(document)), "no_requests": [] if HELM_REQUESTS.search(document) else ["(helm template)"],
                "spread": bool(HELM_SPREAD.search(document)), "graceful": bool(HELM_GRACE.search(document)),
                "labels": [],
            })
    return workloads


# the Kubernetes List kinds: the exact kind List, and <WorkloadKind>List for each workload kind the scanner judges
STRICT_LIST_KINDS = frozenset({"List"} | {f"{kind}List" for kind in WORKLOAD_KINDS})
# a List may hold a List of workloads, and so on: this many List levels are judged, counting the top-level List; deeper is a reason
MAX_LIST_DEPTH = 3


def _is_list(item: dict) -> bool:
    """Whether a mapping is judged as a List of items, by its kind. A strict List kind (STRICT_LIST_KINDS) always is: its items must be a
    list, and a missing items value is a reason. Any other kind ending in List (a custom resource such as TodoList) is one only when an
    items key is present, so a custom kind with a spec and no items is judged as its own kind. A kind-less mapping is a List when its
    items is a list. Any other kind is judged as that kind, whatever its items value, so a stray items field cannot hide a workload."""
    kind = item.get("kind")
    if kind is None:
        return isinstance(item.get("items"), list)
    if not isinstance(kind, str):
        return False
    if kind in STRICT_LIST_KINDS:
        return True
    return kind.endswith("List") and "items" in item


def _collect_list(mapping: dict, where: str, depth: int, docs: list[dict], odd: list[tuple[str, str]]) -> None:
    """Judge the items of a List. Depth 1 is the top-level List. An entry that is itself a list kind is judged the same way one level
    deeper, up to MAX_LIST_DEPTH levels; past that it is a reason naming its position, never a silent drop. Every other entry is a
    document, a kind-less mapping is an unkinded-entry reason, and a non-mapping entry is a non-mapping-document reason."""
    entries = mapping.get("items")
    if not isinstance(entries, list):
        odd.append(("non-list-items", f"{where}, List items"))
        return
    for position, entry in enumerate(entries):
        here = f"{where}, List items[{position}]"
        if isinstance(entry, dict) and isinstance(entry.get("kind"), str):
            if not _is_list(entry):
                docs.append(entry)
            elif depth >= MAX_LIST_DEPTH:
                odd.append(("list-too-deep", here))
            else:
                _collect_list(entry, here, depth + 1, docs, odd)
        elif isinstance(entry, dict):  # a List item with no kind is invalid Kubernetes: reported, never silently dropped
            odd.append(("unkinded-entry", here))
        elif entry is not None and entry != []:
            odd.append(("non-mapping-document", here))


def _k8s_docs(text: str, suffix: str) -> tuple[list[dict], list[tuple[str, str]]] | None:
    """(documents, reasons), or None when the file is unparseable. Documents are the mappings: each document and each List entry. A
    reason is a (kind, where) pair: a non-mapping document or List entry, or a List whose items is not a list (its items are then not
    analysed). A top-level sequence of objects is unparseable (None), because its content cannot be attributed to a document here.
    Reasons are kept only when the file also holds a Kubernetes document (a mapping with kind, or a List): a file that is not
    Kubernetes at all is out of scope. Empty documents hold nothing and are skipped. YAML needs PyYAML (the caller checks)."""
    try:
        if suffix == ".json":
            loaded = [json.loads(text)]
        else:
            loaded = list(yaml.safe_load_all(text))
    except (ValueError, RecursionError, yaml.YAMLError if yaml else ValueError):  # a document nested past the parser's limit is unparseable, not a scan abort
        return None
    docs: list[dict] = []
    odd: list[tuple[str, str]] = []
    kubernetes = False
    for index, item in enumerate(loaded):
        if item is None or item == []:
            continue
        if isinstance(item, list):
            return None
        if not isinstance(item, dict):
            odd.append(("non-mapping-document", f"document {index}"))
            continue
        if _is_list(item):
            kubernetes = True
            _collect_list(item, f"document {index}", 1, docs, odd)
        else:
            kubernetes = kubernetes or "kind" in item
            docs.append(item)
    return docs, (odd if kubernetes else [])


def scan_tree(root: Path, target: Path) -> dict:
    """Collect facts from the files under `target` (bounded); every fact keeps the relative path it came from."""
    facts: dict = {
        "files_scanned": 0, "files_skipped": 0, "truncated": False, "ci": [], "env_files": [], "sources": [], "env_reads": [], "sigterm": [],
        "incomplete": [],
        "dockerfiles": [], "compose_health": [], "workloads": [], "scalers": [], "k8s_labels": [], "yaml_skipped": [], "unparseable": [],
        "iac": [], "iac_compute": [], "iac_database": [], "multi_az": [], "multi_az_off": [], "backup": [], "encryption": [], "autoscaling": [],
        "tags": [], "tag_keys": set(), "efficiency": [], "helm": [], "malformed": [],
    }
    seen = 0
    reasons: list[str] = facts["incomplete"]  # one reason per in-scope input that was not analysed: a label and a path, never content

    def candidates():
        if target.is_file():
            yield target.parent, target.name
            return

        def walk_error(error: OSError) -> None:  # an unreadable directory: its entries are not analysed
            reasons.append(_reason("unreadable-directory", _relative(root, Path(error.filename or target))))

        for current, dirs, names in os.walk(target, followlinks=False, onerror=walk_error):
            kept = []
            for d in sorted(dirs):
                if d in SKIP_DIRS:
                    continue
                child = Path(current) / d
                if child.is_symlink():  # listed as a directory but never followed, so its tree is not analysed
                    reasons.append(_reason("symlink-directory", _relative(root, child)))
                    continue
                kept.append(d)
            dirs[:] = kept
            for name in sorted(names):
                yield Path(current), name

    for directory, name in candidates():
        seen += 1
        if seen > MAX_FILES:
            facts["truncated"] = True
            reasons.append(f"skipped:file-limit: the scan stopped after {MAX_FILES} files, the rest of the tree was not analysed")
            break
        path = directory / name
        rel = path.relative_to(root).as_posix()
        lowered, suffix = name.lower(), path.suffix.lower()
        parts = {part.lower() for part in path.relative_to(root).parts[:-1]}
        is_dockerfile = lowered in ("dockerfile", "containerfile") or lowered.startswith("dockerfile.") or lowered.endswith(".dockerfile")
        is_env = lowered == ".env" or (lowered.startswith(".env.") and not lowered.endswith(ENV_SAFE_SUFFIXES))
        is_source = suffix in SOURCE_SUFFIXES and not (parts & TEST_DIRS) and not re.search(r"(?:^test_|_test\.|\.test\.|\.spec\.)", lowered)
        is_tf = suffix == ".tf" or lowered.endswith(".tf.json")
        is_data = suffix in (".yaml", ".yml", ".json", ".template")
        is_ci = (lowered in CI_NAMES or rel.startswith(".github/workflows/") and suffix in (".yml", ".yaml")
                 or rel.startswith(".circleci/") and suffix in (".yml", ".yaml"))
        in_scope = is_dockerfile or is_env or is_source or is_tf or is_data or is_ci
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            facts["files_skipped"] += 1
            if in_scope:
                reasons.append(_reason("unreadable", rel))
            continue
        if not stat.S_ISREG(mode):  # symlinks, sockets, devices
            facts["files_skipped"] += 1
            if in_scope:
                reasons.append(_reason("symlink" if stat.S_ISLNK(mode) else "not-regular", rel))
            continue
        if not in_scope:
            continue
        text, why = _read_text(path)
        if text is None:
            facts["files_skipped"] += 1
            reasons.append(_reason(why, rel))
            continue
        facts["files_scanned"] += 1
        if is_ci:
            facts["ci"].append(rel)
        if is_env:
            if any(re.match(r"\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*[^\s#]", line) for line in text.splitlines()):
                facts["env_files"].append(rel)
        if is_source:
            facts["sources"].append(rel)
            if ENV_READ.search(text):
                facts["env_reads"].append(rel)
            if SIGTERM.search(text):
                facts["sigterm"].append(rel)
        if is_dockerfile:
            facts["dockerfiles"].append({"path": rel, **_dockerfile_facts(text)})
        iac = is_tf or (is_data and "AWS::" in text and ("Resources:" in text or '"Resources"' in text))
        if iac:
            facts["iac"].append(rel)
            for key, regex in (("iac_compute", COMPUTE_OR_DATA), ("iac_database", DATABASE), ("multi_az", MULTI_AZ), ("multi_az_off", MULTI_AZ_OFF),
                               ("backup", BACKUP), ("encryption", ENCRYPTION), ("autoscaling", AUTOSCALING), ("efficiency", EFFICIENCY)):
                if regex.search(text):
                    facts[key].append(rel)
            if TAGS_PRESENT.search(text):
                facts["tags"].append(rel)
                facts["tag_keys"] |= _tag_keys(text)
            continue
        if is_data and re.match(r"(?:docker-)?compose[\w.-]*\.ya?ml$", lowered) and "healthcheck:" in text:
            facts["compose_health"].append(rel)
        if is_data and "apiVersion" in text and "kind" in text:
            if "{{" in text and "templates" in parts:
                facts["workloads"].extend(_helm_workloads(text, rel))
                if "labels:" in text:
                    facts["k8s_labels"].append("(helm)")
                if EFFICIENCY.search(text):
                    facts["efficiency"].append(rel)
                if re.search(r"^kind:\s*(?:HorizontalPodAutoscaler|ScaledObject)", text, re.M):
                    facts["scalers"].append(rel)
                continue
            if suffix != ".json" and yaml is None:
                facts["yaml_skipped"].append(rel)
                continue
            parsed = _k8s_docs(text, suffix)
            if parsed is None:
                facts["unparseable"].append(rel)
                continue
            docs, odd = parsed
            facts["incomplete"].extend(_reason(kind, rel, where) for kind, where in odd)
            for doc in docs:
                try:
                    workload = _workload(doc, rel)
                except _Malformed:
                    facts["malformed"].append((rel, doc.get("kind")))
                    continue
                if workload:
                    facts["workloads"].append(workload)
                    facts["k8s_labels"].extend(workload["labels"])
                if doc.get("kind") in SCALED_KINDS:
                    facts["scalers"].append(rel)
            if EFFICIENCY.search(text):
                facts["efficiency"].append(rel)
    return facts


def _absence(facts: dict, rule: str, fid: str, summary: str, location: str = "") -> dict:
    """A minor absence finding. A Kubernetes manifest that PyYAML could not parse may hold the very signal being looked for, so while
    one is unparsed the answer is unknown: a finding that says so, never a claim that the content is missing."""
    if facts["yaml_skipped"]:
        unknown_id = fid.removesuffix("-missing") + "-unknown"  # health-probe-missing -> health-probe-unknown
        return _finding(rule, unknown_id, "minor",
                        f"unknown, not missing: {summary}; Kubernetes YAML was not parsed (PyYAML missing)", location)
    return _finding(rule, fid, "minor", summary, location)


def evaluate(facts: dict) -> list[dict]:
    """Rules over the facts. Each finding is tagged with its rule; a rule that does not apply yields no finding and no answer."""
    out: list[dict] = []
    workloads = facts["workloads"]
    out.extend(_finding("env-file", "env-file-committed", "major",
                        "a .env file with values is in the scanned tree (committed unless version control ignores it); secrets belong in a secret store", p)
               for p in facts["env_files"])
    if not facts["ci"]:
        out.append(_finding("ci-pipeline", "ci-pipeline-missing", "minor", "no CI pipeline definition found (GitHub Actions, GitLab CI, Jenkins, ...)"))
    if facts["sources"] and not facts["env_reads"]:
        out.append(_finding("env-config", "config-not-from-environment", "minor",
                            f"{len(facts['sources'])} source file(s) and none reads configuration from the environment (twelve-factor III)"))
    # health probes
    for w in workloads:
        if not w["has_probe"]:
            out.append(_finding("health-probes", "k8s-probes-missing", "major", f"{w['kind']} {w['name']} has no readiness, liveness or startup probe", w["path"]))
    if not workloads and not facts["compose_health"] and not any(d["healthcheck"] for d in facts["dockerfiles"]):
        out.append(_absence(facts, "health-probes", "health-probe-missing",
                            "no health check signal found (Dockerfile HEALTHCHECK, compose healthcheck, Kubernetes probes)"))
    # graceful shutdown
    for d in facts["dockerfiles"]:
        if d["shell_form"]:
            out.append(_finding("graceful-shutdown", "docker-shell-form-entrypoint", "minor",
                                f"{d['launcher']} runs through `sh -c`, so SIGTERM never reaches the app; use the exec form or `exec`", d["path"]))
    if (facts["sources"] or facts["dockerfiles"] or workloads) and not facts["sigterm"] and not any(w["graceful"] for w in workloads):
        out.append(_absence(facts, "graceful-shutdown", "graceful-shutdown-missing",
                            "no SIGTERM handling, shutdown hook, preStop hook or termination grace period found"))
    # multi-AZ / region
    out.extend(_finding("multi-az", "multi-az-disabled", "major", "multi-AZ is explicitly disabled", p) for p in facts["multi_az_off"])
    if facts["iac_compute"] and not facts["multi_az"]:
        out.append(_finding("multi-az", "multi-az-missing", "major",
                            "IaC declares compute or data resources but no multi-AZ/zone-redundant declaration was found", facts["iac_compute"][0]))
    if facts["iac_database"] and not facts["backup"]:
        out.append(_finding("backups", "backup-not-declared", "minor", "database resources are declared without a backup retention or point-in-time recovery setting",
                            facts["iac_database"][0]))
    # replicas and zone spread
    for w in workloads:
        if w["kind"] != "DaemonSet" and w["replicas"] == 1:
            out.append(_finding("replicas", "k8s-single-replica", "minor", f"{w['kind']} {w['name']} runs a single replica", w["path"]))
        elif w["kind"] != "DaemonSet" and (w["replicas"] or 0) >= 2 and not w["spread"]:
            out.append(_finding("replicas", "k8s-no-zone-spread", "minor",
                                f"{w['kind']} {w['name']} has {w['replicas']} replicas but no topology spread or pod anti-affinity", w["path"]))
    # performance and efficiency
    for w in workloads:
        if w["no_requests"]:
            out.append(_finding("resource-requests", "k8s-no-resource-requests", "minor",
                                f"{w['kind']} {w['name']} container(s) without resource requests: {', '.join(w['no_requests'])}", w["path"]))
    if not facts["scalers"] and not facts["autoscaling"]:
        out.append(_absence(facts, "autoscaling", "autoscaling-missing", "no autoscaler (HPA, KEDA, cloud autoscaling group/target) declared"))
    if facts["iac"] and not facts["encryption"]:
        out.append(_finding("encryption", "encryption-not-declared", "minor", "IaC is present but no encryption-at-rest setting was found", facts["iac"][0]))
    # tagging
    if facts["iac"]:
        if not facts["tags"]:
            out.append(_finding("tagging", "tags-missing", "minor", "IaC declares no resource tags", facts["iac"][0]))
        elif sorted(set(REQUIRED_TAGS) - facts["tag_keys"]):
            out.append(_finding("tagging", "tag-keys-missing", "minor",
                                f"allocation tag keys not found in IaC: {', '.join(sorted(set(REQUIRED_TAGS) - facts['tag_keys']))}", facts["tags"][0]))
    elif not facts["k8s_labels"]:
        out.append(_absence(facts, "tagging", "tags-missing", "no resource tags or labels found in IaC or Kubernetes manifests"))
    if not facts["efficiency"]:
        out.append(_finding("efficiency", "efficiency-signals-missing", "minor", "no arm64/Graviton, scale-to-zero or data lifecycle/retention signal found"))
    return out


def _applicable(rule: str, facts: dict) -> bool:
    """Rules without findings still need an answer when the repository has something to judge."""
    workloads = facts["workloads"]
    return {
        "ci-pipeline": True, "env-file": True, "health-probes": True, "autoscaling": True, "tagging": True, "efficiency": True,
        "env-config": bool(facts["sources"]),
        "encryption": bool(facts["iac"]),
        "multi-az": bool(facts["iac_compute"] or facts["multi_az_off"]),
        "backups": bool(facts["iac_database"]),
        "graceful-shutdown": bool(facts["sources"] or facts["dockerfiles"] or workloads),
        "replicas": bool(workloads),
        "resource-requests": bool(workloads),
    }[rule]


def score(findings: list[dict], facts: dict) -> tuple[list[dict], float]:
    """Per-pillar scores through the A16 Well-Architected scorecard; risk follows the worst finding of each rule."""
    answers = []
    for rule, (pillar, weight) in RULES.items():
        if not _applicable(rule, facts):
            continue
        worst = min((SEVERITY_RANK[f["severity"]] for f in findings if f["rule"] == rule), default=SEVERITY_RANK["info"])
        risk = "high" if worst <= SEVERITY_RANK["major"] else "medium" if worst == SEVERITY_RANK["minor"] else "none"
        answers.append({"pillar": pillar, "question": rule, "weight": weight, "risk": risk})
    result = a16_tools.wellarch_scorecard({"answers": answers})
    if result.get("state") != "SUCCESS":  # cannot happen: every pillar has an always-applicable rule
        raise SwarmError(ErrorCode.E_INPUT, f"scorecard refused the scan answers: {result}")
    return [{"pillar": p["pillar"], "score_pct": p["score_pct"], "answered": p["answered"], "high_risk": p["high_risk"], "medium_risk": p["medium_risk"]}
            for p in result["pillars"]], result["overall_score_pct"]


def _sorted(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (SEVERITY_RANK[f["severity"]], f["id"], f["location"], f["summary"]))


def dry_run_output() -> dict:
    return {
        "status": "ok", "dry_run": True, "summary": "dry-run: cloud_readiness_scan would scan --path under --root and score the signals per Well-Architected pillar",
        "files_scanned": 0, "files_skipped": 0, "truncated": False,
        "signals": {"ci_files": 0, "dockerfiles": 0, "kubernetes_workloads": 0, "iac_files": 0, "source_files": 0},
        "pillars": [], "overall_score_pct": None, "skipped": [], "findings": [],
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return dry_run_output()
    root = ctx.root.resolve()
    target = (root / args.path).resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, "--path leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, "--path does not exist under the repository root")
    facts = scan_tree(root, target)
    findings = evaluate(facts)
    skipped = []
    reasons = list(facts["incomplete"])
    if facts["yaml_skipped"]:
        skipped.append("skipped:tool-missing: PyYAML (Kubernetes YAML manifests not analysed)")
        reasons.extend(_reason("tool-missing: PyYAML not importable, Kubernetes manifest not analysed", p) for p in facts["yaml_skipped"])
    reasons.extend(_reason("unparseable-manifest", p) for p in facts["unparseable"])
    reasons.extend(_reason("malformed-workload", rel, kind) for rel, kind in facts["malformed"])
    findings.extend({**item, "location": ""} for item in incomplete_findings(sorted(set(reasons)), facts["files_scanned"]))
    pillars, overall = score(findings, facts)
    findings = _sorted(findings)
    status = scan_status(findings)
    return {
        "status": status,
        "summary": f"{len(findings)} finding(s); overall readiness {overall}%",
        "files_scanned": facts["files_scanned"], "files_skipped": facts["files_skipped"], "truncated": facts["truncated"],
        "signals": {"ci_files": len(facts["ci"]), "dockerfiles": len(facts["dockerfiles"]), "kubernetes_workloads": len(facts["workloads"]),
                    "iac_files": len(facts["iac"]), "source_files": len(facts["sources"])},
        "pillars": pillars, "overall_score_pct": overall, "skipped": skipped,
        "findings": [{k: v for k, v in f.items() if k != "rule" and (k != "location" or v)} for f in findings],
    }


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A16", "cloud_readiness_scan", run, description=__doc__, add_args=add_args).main())
