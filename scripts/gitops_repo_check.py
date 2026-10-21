#!/usr/bin/env python3
"""A22 - lint a GitOps repository: kustomization references, Argo CD/Flux application sync policy, sync-wave annotations, image pinning and plaintext secrets.

Scans the YAML and JSON files under --root (or --path below it) and reports findings {id, severity, summary, file, location,
check} with severity info|minor|major|blocker. Checks:
  kustomize  every resources/components/patch/generator reference exists, directory bases hold a kustomization, file references
             stay inside the kustomization directory (Kustomize's default load restrictor), no reference cycle; then the
             overlay is checked with gitops_kustomize_check (duplicate ids, namespace collisions, patch targets)
  apps       Argo CD Application / ApplicationSet and Flux Kustomization / HelmRelease: gitops_app_sync_policy and
             gitops_appset_check; a path with a --prod-marker segment (prod, production, prd) is production
  waves      Argo CD sync waves and hooks of every Kustomize tree or directory that uses them: gitops_sync_wave_check
  images     container images and kustomize `images:` entries: floating tags and, with --require-digest, tag-only references
             (gitops_image_policy)
  secrets    plaintext Secrets, secret-looking env and ConfigMap values, credential patterns and secretGenerator literals
             (gitops_secret_scan)
`status` is "fail" when any major or blocker finding exists. The rules are the handlers of swarm/tools/a22_tools.py, so the
script and the registry tools never disagree. Read-only and bounded: files over 1 MiB, binaries and symlinks are skipped and
at most 2000 files are read. YAML needs PyYAML; without it YAML files are not read, so the scan reports `scan/incomplete`
(major, status "fail") while JSON is still read. No external binary is used. `--dry-run` prints deterministic canned output and reads nothing.
"""
from __future__ import annotations

import importlib
import json
import os
import posixpath
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools import a22_tools as rules  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 2000
MAX_FINDINGS = 500
MAX_DEPTH = 60
MAX_NODES = 200_000
UNIT_LIMIT = 300  # gitops_sync_wave_check bound
SKIP_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".swarm", ".terraform"})
# Kept on purpose: VCS metadata (.git, .hg, .svn) and the package or tool caches of a checkout (node_modules, .venv, venv, __pycache__,
# .terraform, .swarm), which hold no repository manifests. build/ and dist/ are NOT skipped: build tools write rendered manifests there,
# and a directory this scan skips is a file it never reads.
CONFIG_SUFFIXES = (".yaml", ".yml", ".json")
KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
DIRECTORY_FIELDS = ("resources", "bases", "components")
FILE_FIELDS = ("crds", "generators", "transformers", "validators")
DEFAULT_PROD_MARKERS = ("prod", "production", "prd")
PREFIX_EXEMPT_KINDS = frozenset({"Namespace", "CustomResourceDefinition", "APIService"})  # kustomize's prefix/suffix transformer skips these
SEVERITY_RANK = {"blocker": 0, "major": 1, "minor": 2, "info": 3}
CHECKS = ("parse", "kustomize", "apps", "waves", "images", "secrets")
SKIP_REASONS = ("symlink", "too-large", "binary", "unreadable", "too-complex", "templated")


def _normalise(value, budget: list[int], depth: int = 0):
    """JSON-shaped copy with string keys; ValueError when the document is too deep or too large (YAML alias bombs included)."""
    budget[0] += 1
    if budget[0] > MAX_NODES or depth > MAX_DEPTH:
        raise ValueError("document too complex")
    if isinstance(value, dict):
        return {str(key): _normalise(child, budget, depth + 1) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(child, budget, depth + 1) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)  # YAML timestamps and the like


def _load_yaml():
    try:
        return importlib.import_module("yaml")
    except ImportError:
        return None


class Repo:
    """What was read: parsed documents per file plus the findings collected so far."""

    def __init__(self, root: Path, prod_markers: list[str], require_digest: bool):
        self.root, self.real_root = root, os.path.realpath(root)
        self.prod_markers, self.require_digest = {m.lower() for m in prod_markers}, require_digest
        self.raw: dict[str, list] = {}  # every parsed document of a file
        self.docs: dict[str, list[dict]] = {}  # the object documents
        self.skipped = {reason: 0 for reason in SKIP_REASONS}
        self.yaml_missing_files = 0
        self.truncated = False
        self.examined = 0
        self.findings: list[dict] = []
        self.reasons: list[str] = []  # one label and path per in-scope input or check that was not analysed; never a value

    def add(self, check: str, file: str, fid: str, severity: str, summary: str) -> None:
        scrub = rules._scrub  # findings never quote a credential-shaped value
        self.findings.append({"id": scrub(f"{check}:{fid}"), "severity": severity, "summary": scrub(summary), "file": file, "location": file, "check": check})

    def skip(self, kind: str, path: str) -> None:
        """Count an input that was not read and name its path as a reason the status cannot be ok."""
        self.skipped[kind] += 1
        self.reasons.append(f"{Path(path).relative_to(self.root).as_posix()}: {kind}, not analysed")

    def from_tool(self, check: str, file: str, result: dict) -> None:
        """Take a handler's findings (info dropped as noise). A check that did not run is a reason, never a silent pass."""
        if result.get("state") == "SUCCESS":
            for item in result["findings"]:
                if item["severity"] != "info":
                    self.add(check, file, item["id"], item["severity"], item["summary"])
        else:
            self.reasons.append(f"{check} check did not run on {file} ({result.get('state', 'no result')})")

    def production(self, rel: str) -> bool:
        return any(token in self.prod_markers for token in re.split(r"[^a-z0-9]+", rel.lower()))

    def exists(self, rel: str, directory: bool | None = None) -> bool:
        """Whether rel exists inside the root; a symlink that leaves the root counts as absent."""
        path = os.path.join(self.real_root, rel) if rel else self.real_root
        real = os.path.realpath(path)
        if real != self.real_root and not real.startswith(self.real_root + os.sep):
            return False
        if directory is None:
            return os.path.exists(real)
        return os.path.isdir(real) if directory else os.path.isfile(real)


def _parse(text: str, name: str, yaml_mod):
    """(documents, problem) of a file; problem is None, "yaml-missing", "templated", "error" or "too-complex"."""
    budget = [0]  # one node budget per file: an alias bomb is bounded across all its documents, not per document
    try:
        if name.endswith(".json"):
            loaded = json.loads(text)
            loaded = loaded if isinstance(loaded, list) else [loaded]
        elif yaml_mod is None:
            return [], "yaml-missing"
        else:
            loaded = list(yaml_mod.safe_load_all(text))
        return [_normalise(item, budget) for item in loaded if item is not None], None
    except ValueError as exc:
        if "too complex" in str(exc):
            return [], "too-complex"
        return [], "templated" if "{{" in text else "error"
    except RecursionError:  # valid but nested beyond the interpreter's stack: too complex, not invalid
        return [], "too-complex"
    except getattr(yaml_mod, "YAMLError", ValueError):
        return [], "templated" if "{{" in text else "error"


def _scan(repo: Repo, base: Path, yaml_mod) -> None:
    done = False
    for current, dirnames, filenames in os.walk(base, followlinks=False):
        kept = []
        for name in sorted(dirnames):
            if name in SKIP_DIRS:
                continue
            if os.path.islink(os.path.join(current, name)):
                repo.skip("symlink", os.path.join(current, name))
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if not (name.endswith(CONFIG_SUFFIXES) or name == "Kustomization"):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path):
                repo.skip("symlink", path)
                continue
            repo.examined += 1
            if repo.examined > MAX_FILES:
                repo.truncated = done = True
                repo.reasons.append(f"scan stopped after {MAX_FILES} files; the rest of the tree was not scanned")
                break
            rel = Path(path).relative_to(repo.root).as_posix()
            try:
                info = os.stat(path)
                if not stat.S_ISREG(info.st_mode):  # FIFOs, sockets and devices are never opened: open() on a FIFO blocks forever
                    repo.skip("unreadable", path)
                    continue
                if info.st_size > MAX_FILE_BYTES:
                    repo.skip("too-large", path)
                    continue
                # O_NONBLOCK stops a FIFO swapped in after the stat from blocking the open; the fstat below rejects it
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as handle:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        repo.skip("unreadable", path)
                        continue
                    data = handle.read(MAX_FILE_BYTES + 1)
            except OSError:
                repo.skip("unreadable", path)
                continue
            if b"\0" in data[:8192]:
                repo.skip("binary", path)
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                repo.skip("binary", path)
                continue
            documents, problem = _parse(text, name, yaml_mod)
            if problem == "yaml-missing":
                repo.yaml_missing_files += 1
                repo.reasons.append(f"{rel}: YAML not read, PyYAML is not installed")
            elif problem in ("templated", "too-complex"):
                repo.skip(problem, path)
            elif problem == "error":
                repo.add("parse", rel, rel, "major", f"{rel} is not valid YAML/JSON; GitOps controllers cannot render it")
            else:
                repo.raw[rel] = documents
                repo.docs[rel] = [d for d in rules._expand_lists(documents) if isinstance(d, dict)]  # a kind: List stands for its items: the objects it holds
        if done:
            break


def _is_object(doc) -> bool:
    return (isinstance(doc, dict) and isinstance(doc.get("apiVersion"), str) and isinstance(doc.get("kind"), str)
            and isinstance(doc.get("metadata"), dict) and bool(rules._name_of(doc)))


def _join(directory: str, entry: str) -> str | None:
    """Repo-relative path of entry under directory; None when it leaves the repository; "" is the root."""
    joined = posixpath.normpath(posixpath.join(directory or ".", entry))
    if joined == ".":
        return ""
    return None if joined == ".." or joined.startswith("../") or posixpath.isabs(joined) else joined


def _remote(entry: str) -> bool:
    return "://" in entry or entry.startswith(("git@", "git::", "github.com/", "gitlab.com/", "bitbucket.org/"))


def _under(rel: str, directory: str) -> bool:
    return not directory or rel == directory or rel.startswith(directory + "/")


def _strings(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _annotated(doc: dict, key: str) -> bool:
    annotations = doc["metadata"].get("annotations")
    return isinstance(annotations, dict) and key in annotations


def _defines_a_kind_here(manifests: list[dict]) -> bool:
    """Whether a CustomResourceDefinition of the unit defines the kind of another object in it: their order matters without annotations too."""
    crds = set()
    for manifest in manifests:
        spec = manifest.get("spec")
        names = spec.get("names") if isinstance(spec, dict) else None
        if manifest["kind"] == "CustomResourceDefinition" and isinstance(spec, dict) and isinstance(names, dict):
            if isinstance(spec.get("group"), str) and isinstance(names.get("kind"), str):
                crds.add((spec["group"], names["kind"]))
    return any(m["kind"] != "CustomResourceDefinition" and (rules._group(m["apiVersion"]), m["kind"]) in crds for m in manifests)


def _file_references(kust: dict) -> list[tuple[str, str]]:
    """(field, path) of the files a kustomization loads besides its resources, components and crds: patches, files and envs of generators, openapi."""
    refs: list[tuple[str, str]] = []
    for entry in kust.get("patches") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            refs.append(("patches", entry["path"]))
    for entry in _strings(kust.get("patchesStrategicMerge")):
        if "\n" not in entry and not entry.lstrip().startswith(("{", "apiVersion", "kind")):
            refs.append(("patchesStrategicMerge", entry))
    for entry in kust.get("patchesJson6902") or []:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            refs.append(("patchesJson6902", entry["path"]))
    for field in ("configMapGenerator", "secretGenerator"):
        for generator in kust.get(field) or []:
            if isinstance(generator, dict):
                refs += [(field, item.split("=", 1)[-1]) for item in _strings(generator.get("files")) + _strings(generator.get("envs"))]
                if isinstance(generator.get("env"), str):
                    refs.append((field, generator["env"]))
    openapi = kust.get("openapi")
    if isinstance(openapi, dict) and isinstance(openapi.get("path"), str):
        refs.append(("openapi", openapi["path"]))
    return refs


def _kustomizations(repo: Repo) -> dict[str, tuple[str, dict]]:
    found = {}
    for rel in sorted(repo.raw):
        documents = repo.raw[rel]
        if posixpath.basename(rel) in KUSTOMIZATION_NAMES and documents and isinstance(documents[0], dict):
            found[posixpath.dirname(rel)] = (rel, documents[0])
    return found


def _check_references(repo: Repo, kusts: dict) -> dict[str, list[str]]:
    """Reference existence, load restriction and cycles; returns the directory edges of the kustomization graph."""
    edges: dict[str, list[str]] = {}
    for directory, (rel, kust) in sorted(kusts.items()):
        edges[directory] = []
        for field in DIRECTORY_FIELDS + FILE_FIELDS:
            for entry in _strings(kust.get(field)):
                if _remote(entry):
                    continue
                target = _join(directory, entry)
                if target is None:
                    repo.add("kustomize", rel, f"outside-root:{field}:{entry}", "minor", f"{rel}: {field} entry {entry} leaves the scanned root, so it cannot be checked")
                elif not repo.exists(target):
                    repo.add("kustomize", rel, f"missing-reference:{field}:{entry}", "blocker", f"{rel}: {field} entry {entry} does not exist; kustomize build fails")
                elif repo.exists(target, True):
                    if field in DIRECTORY_FIELDS:
                        if target in kusts:
                            edges[directory].append(target)
                        else:
                            repo.add("kustomize", rel, f"base-without-kustomization:{entry}", "blocker",
                                     f"{rel}: {field} entry {entry} is a directory without a kustomization file")
                    else:
                        repo.add("kustomize", rel, f"directory-not-file:{field}:{entry}", "major",
                                 f"{rel}: {field} entry {entry} is a directory, but this field takes a file; kustomize's behaviour for it is not verified by this scan")
                        if not _under(target, directory):
                            repo.add("kustomize", rel, f"load-restrictor:{field}:{entry}", "major",
                                     f"{rel}: {field} entry {entry} is a directory outside the kustomization directory; kustomize refuses it unless run with --load-restrictor LoadRestrictionsNone")
                elif not _under(target, directory):
                    repo.add("kustomize", rel, f"load-restrictor:{field}:{entry}", "major",
                             f"{rel}: file {entry} is outside the kustomization directory; kustomize refuses it unless run with --load-restrictor LoadRestrictionsNone")
                elif field == "components":
                    repo.add("kustomize", rel, f"component-not-directory:{entry}", "blocker", f"{rel}: components entry {entry} is a file, not a directory")
        for field, entry in _file_references(kust):
            if _remote(entry):
                continue
            target = _join(directory, entry)
            if target is None:
                repo.add("kustomize", rel, f"outside-root:{field}:{entry}", "minor", f"{rel}: {field} file {entry} leaves the scanned root, so it cannot be checked")
            elif not repo.exists(target, False):
                repo.add("kustomize", rel, f"missing-file:{field}:{entry}", "blocker", f"{rel}: {field} file {entry} does not exist; kustomize build fails")
            elif not _under(target, directory):
                repo.add("kustomize", rel, f"load-restrictor:{field}:{entry}", "major",
                         f"{rel}: file {entry} is outside the kustomization directory; kustomize refuses it unless run with --load-restrictor LoadRestrictionsNone")
    state: dict[str, int] = {}

    def visit(node: str, trail: list[str]) -> None:
        state[node] = 1
        for child in edges.get(node, []):
            if state.get(child) == 1:
                cycle = " -> ".join(trail[trail.index(child):] + [child])
                repo.add("kustomize", kusts[node][0], f"cycle:{child}", "blocker", f"kustomization cycle: {cycle}")
            elif child not in state:
                visit(child, trail + [child])
        state[node] = 2

    for directory in sorted(edges):
        if directory not in state:
            visit(directory, [directory])
    return edges


def _minimal(doc: dict, name: str | None = None, namespace: str | None = None) -> dict:
    """The object as the kustomize check sees it: its real metadata.name (or `name` when a rename applies). An object that has only
    generateName keeps generateName: it is never copied into name, so a patch target can match only a name the object really has."""
    real = doc["metadata"].get("name")
    metadata: dict = {}
    if name is not None:
        metadata["name"] = name
    elif isinstance(real, str) and real:
        metadata["name"] = real
    else:
        metadata["generateName"] = rules._name_of(doc)
    ns = namespace if namespace is not None else doc["metadata"].get("namespace")
    if isinstance(ns, str) and ns:
        metadata["namespace"] = ns
    return {"apiVersion": doc["apiVersion"], "kind": doc["kind"], "metadata": metadata}



def _items_of(repo: Repo, kusts: dict, directory: str, trail: tuple = ()) -> list[dict]:
    """Objects this kustomization renders before its own patches; children arrive transformed by their prefix, suffix and namespace."""
    rel, kust = kusts[directory]
    items: list[dict] = []
    for field in ("resources", "bases"):
        for entry in _strings(kust.get(field)):
            target = None if _remote(entry) else _join(directory, entry)
            if target is None:
                continue
            if target in kusts and target not in trail and len(trail) < 20:
                child_rel, child = kusts[target]
                prefix, suffix, namespace = (child.get(k) if isinstance(child.get(k), str) else "" for k in ("namePrefix", "nameSuffix", "namespace"))
                for item in _items_of(repo, kusts, target, trail + (directory,)):
                    cluster = item["kind"] in rules.CLUSTER_SCOPED_KINDS
                    real = item["metadata"].get("name")
                    named = isinstance(real, str) and bool(real)
                    renamed = item["kind"] not in PREFIX_EXEMPT_KINDS
                    changes = bool((renamed and named and (prefix or suffix)) or (namespace and not cluster))
                    new_name = (prefix + real + suffix if renamed else real) if named else None
                    items.append(_minimal(item, new_name, namespace if namespace and not cluster else None) if changes else item)
            elif target in repo.docs:
                items += [_minimal(d) for d in repo.docs[target] if _is_object(d) and not d["apiVersion"].startswith("kustomize.config.k8s.io/")]
    for field, kind in (("configMapGenerator", "ConfigMap"), ("secretGenerator", "Secret")):
        for generator in kust.get(field) or []:
            if isinstance(generator, dict) and isinstance(generator.get("name"), str) and generator["name"]:
                namespace = generator.get("namespace")
                items.append({"apiVersion": "v1", "kind": kind, "metadata": {"name": generator["name"], **({"namespace": namespace} if isinstance(namespace, str) and namespace else {})}})
    return items


def _first_document(repo: Repo, directory: str, entry) -> object:
    target = _join(directory, entry) if isinstance(entry, str) else None
    documents = repo.raw.get(target) if target is not None else None
    return documents[0] if documents else None


def _inline_patches(repo: Repo, directory: str, kust: dict) -> dict:
    """Copy of the kustomization with patch files replaced by their parsed content, so the handler can derive the targets."""
    inlined = dict(kust)
    if isinstance(kust.get("patches"), list):
        patches = []
        for entry in kust["patches"]:
            content = _first_document(repo, directory, entry.get("path")) if isinstance(entry, dict) and "patch" not in entry else None
            if isinstance(content, (dict, list)):
                patches.append({**{k: v for k, v in entry.items() if k != "path"}, "patch": content})
            else:
                patches.append(entry)
        inlined["patches"] = patches
    if isinstance(kust.get("patchesStrategicMerge"), list):
        merged = []
        for entry in kust["patchesStrategicMerge"]:
            target = _join(directory, entry) if isinstance(entry, str) and "\n" not in entry else None
            documents = repo.raw.get(target) if target is not None else None
            merged += [d for d in documents if isinstance(d, dict)] if documents else [entry]
        inlined["patchesStrategicMerge"] = merged
    if isinstance(kust.get("patchesJson6902"), list):
        converted = []
        for entry in kust["patchesJson6902"]:
            content = _first_document(repo, directory, entry.get("path")) if isinstance(entry, dict) and "patch" not in entry else None
            converted.append({**{k: v for k, v in entry.items() if k != "path"}, "patch": content} if isinstance(content, list) else entry)
        inlined["patchesJson6902"] = converted
    return inlined


def _opaque(kusts: dict, directory: str, trail: tuple = ()) -> bool:
    """Whether the tree under a kustomization renders objects that are not in the scanned files."""
    _, kust = kusts[directory]
    if kust.get("components") or kust.get("helmCharts") or kust.get("generators") or kust.get("transformers"):
        return True
    for field in ("resources", "bases"):
        for entry in _strings(kust.get(field)):
            if _remote(entry):
                return True
            child = _join(directory, entry)
            if child in kusts and child not in trail and len(trail) < 20 and _opaque(kusts, child, trail + (directory,)):
                return True
    return False


def _check_kustomize(repo: Repo) -> dict[str, tuple[str, dict]]:
    kusts = _kustomizations(repo)
    _check_references(repo, kusts)
    for directory, (rel, kust) in sorted(kusts.items()):
        every = _items_of(repo, kusts, directory)
        items = every[:500]
        if len(every) > 500:
            repo.reasons.append(f"{rel}: kustomize checked the first 500 of {len(every)} rendered objects for duplicate ids and patch targets")
        result = rules.kustomize_check({"kustomization": _inline_patches(repo, directory, kust), "resources": items})
        if result.get("state") == "SUCCESS" and _opaque(kusts, directory):
            # remote bases, components, Helm charts and generators add objects this scan cannot see: a patch target that matches none of the visible ones is unproven, not missing
            hidden = [f for f in result["findings"] if f["id"].startswith("patch-target-not-found")]
            if hidden:
                repo.reasons.append(f"{rel}: {len(hidden)} patch target(s) unproven, the tree renders objects this scan cannot see")
            result["findings"] = [f for f in result["findings"] if not f["id"].startswith("patch-target-not-found")]
        repo.from_tool("kustomize", rel, result)
        for generator in kust.get("secretGenerator") or []:
            if not isinstance(generator, dict):
                continue
            name = generator.get("name", "?")
            stringdata = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in _strings(generator.get("literals")) if "=" in item}
            probe = rules.secret_scan({"manifests": [{"apiVersion": "v1", "kind": "Secret", "metadata": {"name": str(name) or "?"}, "stringData": stringdata}]}) if stringdata else {}
            if probe.get("state") == "SUCCESS" and any(f["id"].startswith("plaintext-secret") for f in probe["findings"]):
                repo.add("secrets", rel, f"secretgenerator-literals:{name}", "blocker",
                         f"{rel}: secretGenerator {name} commits literal secret value(s) in clear")
            committed = _strings(generator.get("files")) + _strings(generator.get("envs")) + ([generator["env"]] if isinstance(generator.get("env"), str) else [])
            if committed:
                repo.add("secrets", rel, f"secretgenerator-files:{name}", "major",
                         f"{rel}: secretGenerator {name} reads {len(committed)} file(s) that must be committed in clear to be built")
    return kusts


def _check_apps(repo: Repo) -> None:
    for rel in sorted(repo.docs):
        for doc in repo.docs[rel]:
            if not _is_object(doc):
                continue
            group, kind = doc["apiVersion"].split("/", 1)[0], doc["kind"]
            environment = "prod" if repo.production(rel) else "nonprod"
            if group == "argoproj.io" and kind == "ApplicationSet":
                result = rules.appset_check({"applicationset": doc, "clusters": [], "environment": environment})
                repo.from_tool("apps", rel, result)
            elif (group, kind) in (("argoproj.io", "Application"), ("kustomize.toolkit.fluxcd.io", "Kustomization"), ("helm.toolkit.fluxcd.io", "HelmRelease")):
                repo.from_tool("apps", rel, rules.app_sync_policy({"resource": doc, "environment": environment}))


def _tree_files(repo: Repo, kusts: dict, directory: str, seen: set, fields: tuple = ("resources", "bases")) -> list[str]:
    """Files whose objects belong to the Kustomize tree rooted at directory."""
    if directory in seen:
        return []
    seen.add(directory)
    rel, kust = kusts[directory]
    files = [rel]
    for field in fields:
        for entry in _strings(kust.get(field)):
            target = None if _remote(entry) else _join(directory, entry)
            if target in kusts:
                files += _tree_files(repo, kusts, target, seen, fields)
            elif target in repo.docs:
                files.append(target)
    return files


def _check_waves(repo: Repo, kusts: dict) -> None:
    referenced = _referenced(kusts)
    units: list[tuple[str, list[str]]] = []
    covered: set[str] = set()
    for directory in sorted(kusts):
        if directory not in referenced:
            files = _tree_files(repo, kusts, directory, set())
            covered.update(files)
            units.append((kusts[directory][0], files))
    loose: dict[str, list[str]] = {}
    for rel in sorted(repo.docs):
        if rel not in covered and any(_is_object(d) for d in repo.docs[rel]):
            loose.setdefault(posixpath.dirname(rel), []).append(rel)
    units += [((directory or ".") + "/", files) for directory, files in sorted(loose.items())]
    for label, files in units:
        manifests = [d for rel in files for d in repo.docs.get(rel, []) if _is_object(d) and not d["apiVersion"].startswith("kustomize.config.k8s.io/")]
        if not any(_annotated(m, rules.WAVE_ANNOTATION) or _annotated(m, rules.HOOK_ANNOTATION) for m in manifests) and not _defines_a_kind_here(manifests):
            continue
        if len(manifests) > UNIT_LIMIT:
            repo.reasons.append(f"{label}: sync order checked for the first {UNIT_LIMIT} of {len(manifests)} objects only")
        repo.from_tool("waves", label, rules.sync_wave_check({"manifests": manifests[:UNIT_LIMIT]}))


def _images(node, budget: list[int]):
    """Image references of every containers / initContainers / ephemeralContainers list inside a document."""
    stack = [node]
    while stack and budget[0] < MAX_NODES:
        item = stack.pop()
        budget[0] += 1
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("containers", "initContainers", "ephemeralContainers") and isinstance(child, list):
                    for container in child:
                        if isinstance(container, dict) and isinstance(container.get("image"), str):
                            yield container["image"]
                stack.append(child)
        elif isinstance(item, list):
            stack.extend(item)


def _image_name(image: str) -> str:
    """Repository part of an image reference (what a kustomize `images:` entry matches by name): no digest, no tag."""
    name = image.partition("@")[0]
    return name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name


def _referenced(kusts: dict) -> set[str]:
    """Kustomization directories that another kustomization includes as a base, component or resource. The rest are roots, the overlays
    a render starts from."""
    return {child for directory, (_, kust) in kusts.items() for field in DIRECTORY_FIELDS for entry in _strings(kust.get(field))
            if not _remote(entry) and (child := _join(directory, entry)) in kusts and child != directory}


def _kust_pins(kust: dict) -> frozenset[str]:
    """Image names that this kustomization's `images:` entries pin by digest or tag: the references its render rewrites."""
    return frozenset(entry["name"] for entry in kust.get("images") or []
                     if isinstance(entry, dict) and isinstance(entry.get("name"), str)
                     and (isinstance(entry.get("digest"), str) or isinstance(entry.get("newTag"), (str, int, float))))


def _reached(repo: Repo, kusts: dict, roots: list[str], name: str | None = None) -> set[str]:
    """Files the trees rooted at roots render. With a name, only the files rendered through a path on which no kustomization pins that
    image name: a kustomization that pins it blocks its own files and everything below it. Each kustomization is entered once, so the
    walk is linear in the graph however many paths lead through a shared component."""
    pins = {directory: _kust_pins(kust) for directory, (_, kust) in kusts.items()}

    def blocked(directory: str) -> bool:
        return name is not None and name in pins[directory]

    entered = {root for root in roots if not blocked(root)}
    stack = sorted(entered)
    files: set[str] = set()
    while stack:
        directory = stack.pop()
        rel, kust = kusts[directory]
        files.add(rel)
        for field in DIRECTORY_FIELDS:
            for entry in _strings(kust.get(field)):
                target = None if _remote(entry) else _join(directory, entry)
                if target in kusts:
                    if target not in entered and not blocked(target):
                        entered.add(target)
                        stack.append(target)
                elif target in repo.docs:
                    files.add(target)
    return files


def _check_images(repo: Repo, kusts: dict) -> None:
    where: dict[str, list[str]] = {}
    roots = sorted(set(kusts) - _referenced(kusts))
    rendered = _reached(repo, kusts, roots)
    pinned_names = {name for _, kust in kusts.values() for name in _kust_pins(kust)}
    occurrences: dict[str, list[tuple[str, str]]] = {}  # image name -> (file, image reference) of each occurrence
    for rel in sorted(repo.docs):
        for doc in repo.docs[rel]:
            for image in _images(doc, [0]):
                occurrences.setdefault(_image_name(image), []).append((rel, image))
    for name in sorted(occurrences):
        # a name that no kustomization pins blocks nothing, so the plain reach answers for it and no set is built; a pinned name gets its own
        # walk, and that set is dropped once its occurrences are judged. Every render that includes a file is judged, whatever another pins.
        unpinned = _reached(repo, kusts, roots, name) if name in pinned_names else rendered
        for rel, image in occurrences.pop(name):
            if rel not in rendered or rel in unpinned:
                where.setdefault(image, []).append(rel)
    for directory, (rel, kust) in sorted(kusts.items()):
        for entry in kust.get("images") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("newName") or entry.get("name")
            if isinstance(name, str) and isinstance(entry.get("digest"), str):
                where.setdefault(f"{name}@{entry['digest']}", []).append(rel)
            elif isinstance(name, str) and isinstance(entry.get("newTag"), (str, int, float)):
                where.setdefault(f"{name}:{entry['newTag']}", []).append(rel)
    for image in sorted(where):
        if any(marker in image for marker in ("{{", "$(", "${")):
            repo.reasons.append(f"{where[image][0]}: an image reference with a template is not analysed; its value is known only at deploy time")
            continue
        rel = where[image][0]
        result = rules.image_policy({"image": image, "candidate_tags": [], "require_digest": repo.require_digest})
        if result.get("state") == "SUCCESS":
            for item in result["findings"]:
                if item["severity"] != "info":
                    extra = f" (+{len(where[image]) - 1} more file(s))" if len(where[image]) > 1 else ""
                    repo.add("images", rel, f"{item['id']}:{image}", item["severity"], item["summary"] + extra)
        else:
            repo.add("images", rel, f"image-invalid:{image}", "minor", f"{rel}: {image!r}: {result.get('message')}")


def _check_secrets(repo: Repo) -> None:
    for rel in sorted(repo.raw):
        expanded = rules._expand_lists(repo.raw[rel])  # kubectl get -o yaml emits a kind: List whose items are the objects
        objects = [d for d in expanded if _is_object(d)]
        for start in range(0, len(objects), 200):
            repo.from_tool("secrets", rel, rules.secret_scan({"manifests": objects[start:start + 200]}))
        # kustomization.yaml, Helm values and every other document that is not a Kubernetes object: credential formats and URL passwords only
        others = [{"apiVersion": "v1", "kind": "Document", "metadata": {"name": rel}, "content": doc} for doc in expanded if not _is_object(doc)]
        for start in range(0, len(others), 200):
            repo.from_tool("secrets", rel, rules.secret_scan({"manifests": others[start:start + 200]}))


def _result(repo: Repo, scanned: int) -> dict:
    skipped = []
    if repo.yaml_missing_files:
        skipped.append({"check": "kustomize, apps, waves, images, secrets (YAML files)", "reason": "skipped:tool-missing", "tool": "PyYAML",
                        "files": repo.yaml_missing_files})
    incomplete = [{"id": f["id"], "severity": f["severity"], "summary": f["summary"], "file": ".", "location": ".", "check": "scan"}
                  for f in incomplete_findings(repo.reasons, scanned)]
    ordered = sorted(repo.findings + incomplete, key=lambda f: (SEVERITY_RANK[f["severity"]], f["file"], f["id"]))
    counts = {name: sum(1 for f in ordered if f["severity"] == name) for name in SEVERITY_RANK}
    checks = {name: sum(1 for f in ordered if f["check"] == name) for name in CHECKS}
    return {
        "status": scan_status(ordered),
        "summary": f"{len(ordered)} finding(s) in {scanned} file(s): {counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info",
        "findings": ordered[:MAX_FINDINGS], "finding_count": len(ordered), "severity_counts": counts, "checks": checks,
        "files_scanned": scanned, "files_skipped": dict(repo.skipped), "files_skipped_yaml_missing": repo.yaml_missing_files,
        "truncated": repo.truncated, "skipped": skipped,
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "summary": "dry-run: gitops_repo_check would scan YAML and JSON files under --root and read nothing now",
            "findings": [], "finding_count": 0, "severity_counts": {name: 0 for name in SEVERITY_RANK}, "checks": {name: 0 for name in CHECKS},
            "files_scanned": 0, "files_skipped": {reason: 0 for reason in SKIP_REASONS}, "files_skipped_yaml_missing": 0, "truncated": False, "skipped": [],
        }
    base = (ctx.root / args.path).resolve()
    if not base.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not base.is_dir():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} is not a directory under {ctx.root}")
    repo = Repo(ctx.root, args.prod_marker or list(DEFAULT_PROD_MARKERS), args.require_digest)
    _scan(repo, base, _load_yaml())
    kusts = _check_kustomize(repo)
    _check_apps(repo)
    _check_waves(repo, kusts)
    _check_images(repo, kusts)
    _check_secrets(repo)
    return _result(repo, len(repo.raw))


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="directory under --root to scan")
    p.add_argument("--prod-marker", action="append", help="path segment that marks production (repeatable; default prod, production, prd)")
    p.add_argument("--require-digest", action="store_true", help="report image references that are pinned by tag only")


if __name__ == "__main__":
    sys.exit(AgentScript("A22", "gitops_repo_check", run, description=__doc__, add_args=add_args).main())
