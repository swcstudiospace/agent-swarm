#!/usr/bin/env python3
"""A11 — discover CI/CD, container, IaC and Kubernetes configs and lint them for platform hygiene.

Finds .github/workflows/*.yml, .gitlab-ci.yml, Jenkinsfile, Dockerfile*, docker-compose*.yml,
*.tf and k8s manifests under --root, then checks: pinned action versions (flags @main/@master
and unpinned uses), no plaintext secrets, Dockerfile non-root USER / pinned base tag / HEALTHCHECK,
k8s Deployments carrying resources.limits and readinessProbe. Uses PyYAML when present,
otherwise a regex-based fallback. Never crashes on malformed files.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding, derive_verdict  # noqa: E402

try:  # optional third-party parser; fallback is regex
    import yaml  # type: ignore
except Exception:  # noqa: BLE001
    yaml = None

SECRET_RE = re.compile(
    r"(?i)(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{12,}['\"]?)")
SAFE_REF_RE = re.compile(r"\$\{\{|\$[A-Z_]|<[^>]+>|\*{3,}|x{6,}|example|changeme|dummy|placeholder", re.I)
K8S_WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet"}


def classify(p: Path) -> str | None:
    parts, n = p.parts, p.name
    if ".github" in parts and "workflows" in parts and p.suffix in (".yml", ".yaml"):
        return "gha"
    if n == ".gitlab-ci.yml":
        return "gitlab"
    if n.startswith("Jenkinsfile"):
        return "jenkins"
    if n.startswith("Dockerfile") or n.endswith(".Dockerfile"):
        return "dockerfile"
    if re.match(r"(docker-)?compose[\w.-]*\.ya?ml$", n):
        return "compose"
    if p.suffix in (".tf", ".tfvars"):
        return "terraform"
    if p.suffix in (".yml", ".yaml"):
        try:
            head = p.read_text(errors="ignore")[:4000]
        except OSError:
            return None
        if re.search(r"^apiVersion:", head, re.M) and re.search(r"^kind:", head, re.M):
            return "k8s"
    return None


def discover(root: Path) -> list[tuple[str, Path]]:
    return [(k, p) for p in iter_files(root) if (k := classify(p))]


def _add(findings, sev, kind, summary, path, evidence="", owner="A11"):
    fid = f"DF-{len(findings)+1:03d}"
    findings.append(make_finding(fid, sev, kind, summary, evidence=evidence[:300], owner_suggestion=owner, location=str(path)))


def check_workflow(text: str, rel: str, findings: list) -> None:
    for m in re.finditer(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)", text, re.M):
        ref = m.group(1)
        if ref.startswith(("./", "docker://")):
            continue
        action, _, ver = ref.partition("@")
        if not ver:
            _add(findings, "major", "supply-chain", f"unpinned action {action}", rel, ref)
        elif ver in ("main", "master", "latest") or ver.startswith(("main/", "master/")):
            _add(findings, "major", "supply-chain", f"action pinned to mutable branch {ref}", rel, ref)
        elif not re.match(r"^(v?\d[\w.-]*|[0-9a-f]{40})$", ver):
            _add(findings, "minor", "supply-chain", f"action ref {ref} is neither tag nor SHA", rel, ref)
    if re.search(r"pull_request_target", text) and re.search(r"ref:\s*\$\{\{\s*github\.event\.pull_request", text):
        _add(findings, "critical", "supply-chain", "pull_request_target checks out PR head (pwn-request)", rel)


def check_dockerfile(text: str, rel: str, findings: list) -> None:
    froms = re.findall(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M | re.I)
    aliases = {a.lower() for a in re.findall(r"^\s*FROM\s+\S+\s+AS\s+(\S+)", text, re.M | re.I)}
    for img in froms:
        if img.lower() in aliases or img.lower() == "scratch":
            continue
        if "@sha256:" in img:
            continue
        if ":" not in img.split("/")[-1] or img.endswith(":latest"):
            _add(findings, "major", "supply-chain", f"base image not pinned ({img})", rel, img)
    users = re.findall(r"^\s*USER\s+(\S+)", text, re.M | re.I)
    if not users or users[-1] in ("root", "0"):
        _add(findings, "major", "hardening", "container runs as root (no non-root USER)", rel)
    if re.search(r"^\s*(EXPOSE|CMD|ENTRYPOINT)\b", text, re.M | re.I) and not re.search(r"^\s*HEALTHCHECK", text, re.M | re.I):
        _add(findings, "minor", "resilience", "service image lacks HEALTHCHECK", rel)


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}.{k}", v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def check_k8s(text: str, rel: str, findings: list) -> None:
    docs = []
    if yaml is not None:
        try:
            docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        except Exception:  # noqa: BLE001
            docs = []
    if not docs:  # regex fallback: one coarse pass over the whole file
        for chunk in re.split(r"^---\s*$", text, flags=re.M):
            km = re.search(r"^kind:\s*(\w+)", chunk, re.M)
            if km and km.group(1) in K8S_WORKLOADS:
                nm = re.search(r"^\s+name:\s*(\S+)", chunk, re.M)
                docs.append({"kind": km.group(1), "metadata": {"name": nm.group(1) if nm else "?"},
                             "_limits": bool(re.search(r"^\s+limits:", chunk, re.M)),
                             "_ready": "readinessProbe" in chunk})
    for d in docs:
        if d.get("kind") not in K8S_WORKLOADS:
            continue
        name = f"{d['kind']}/{(d.get('metadata') or {}).get('name', '?')}"
        if "_limits" in d:
            has_limits, has_ready = d["_limits"], d["_ready"]
        else:
            containers = [v for k, v in _walk(d) if k.endswith(".containers") and isinstance(v, list)]
            flat = [c for cs in containers for c in cs if isinstance(c, dict)]
            has_limits = bool(flat) and all((c.get("resources") or {}).get("limits") for c in flat)
            has_ready = bool(flat) and all(c.get("readinessProbe") for c in flat)
        if not has_limits:
            _add(findings, "major", "capacity", f"{name} missing resources.limits", rel)
        if not has_ready:
            _add(findings, "major", "resilience", f"{name} missing readinessProbe", rel)


def check_secrets(text: str, rel: str, findings: list) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        m = SECRET_RE.search(line)
        if m and not SAFE_REF_RE.search(line):
            _add(findings, "critical", "secret", "possible plaintext credential", f"{rel}:{i}",
                 line.strip()[:80], owner="A10")


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "summary": "dry-run: 2 configs scanned, 0 findings", "dry_run": True,
                "configs": [{"kind": "gha", "path": ".github/workflows/ci.yml"}, {"kind": "dockerfile", "path": "Dockerfile"}],
                "findings": [], "verdict": "pass"}
    root = Path(args.path).resolve() if args.path else ctx.root
    if root.is_file():
        root, configs = root.parent, [(classify(root) or "unknown", root)]
    else:
        configs = discover(root)
    findings: list[dict] = []
    for kind, p in configs:
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        try:
            text = p.read_text(errors="ignore")
        except OSError as e:
            _add(findings, "minor", "io", f"unreadable config: {e}", rel)
            continue
        check_secrets(text, rel, findings)
        if kind == "gha":
            check_workflow(text, rel, findings)
        elif kind == "dockerfile":
            check_dockerfile(text, rel, findings)
        elif kind == "k8s":
            check_k8s(text, rel, findings)
    verdict = derive_verdict(findings)
    counts = {}
    for k, _ in configs:
        counts[k] = counts.get(k, 0) + 1
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict,
            "configs": [{"kind": k, "path": str(p.relative_to(root))} for k, p in configs],
            "counts": counts, "findings": findings, "yaml_parser": "pyyaml" if yaml else "regex-fallback",
            "summary": f"ci check {verdict.upper()} — {len(configs)} config(s) {counts or ''}, {len(findings)} finding(s)"}


def add_args(p):
    p.add_argument("--path", help="subdirectory or single file to scan instead of --root")


if __name__ == "__main__":
    sys.exit(AgentScript("A11", "devops_ci_check", run, description=__doc__, add_args=add_args).main())
