#!/usr/bin/env python3
"""A14 — dependency freshness, pin hygiene, tech-debt register and CVE-driven patch tasks.

Parses requirements*.txt / pyproject.toml / package.json / go.mod / Cargo.toml for
declared dependencies, flags unpinned or wildcard versions, runs `pip list --outdated`
and `npm outdated` when those tools exist (else `skipped:tool-missing`), scans source
for TODO/FIXME/HACK/XXX/deprecated markers into .swarm/debt_register.json (with git
age per file), and cross-matches an optional --cve-file against declared deps to open
signed `patch.task` payloads (24 h deadline for critical/KEV) under .swarm/patch_tasks/.
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, sh, which, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402
from swarm.envelope import build_envelope, sign_envelope  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402

SRC_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".sh", ".yaml", ".yml")
DEBT_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b|\bdeprecated\b", re.I)
EXACT_SEMVER = re.compile(r"^=?=?v?\d+(\.\d+){1,3}[\w.+-]*$")
WILDCARDS = {"", "*", "latest", "x", ">=0", "any"}


def _pin_state(spec: str) -> str:
    s = spec.strip().strip('"').strip("'")
    if s.lower() in WILDCARDS or s.startswith("*") or "latest" in s.lower():
        return "wildcard"
    if EXACT_SEMVER.match(s) or s.startswith("==") or s.startswith("==="):
        return "pinned"
    return "range"  # ^, ~, >=, <, ~=, etc.


def _dep(eco, name, spec, path) -> dict:
    return {"ecosystem": eco, "name": name, "spec": spec, "pin": _pin_state(spec), "file": path}


def parse_deps(root: Path) -> list[dict]:
    deps = []
    for req in sorted(root.glob("requirements*.txt")) + sorted(root.glob("requirements/*.txt")):
        for line in req.read_text(errors="ignore").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "git+", "http")):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*(.*)$", line)
            if m:
                deps.append(_dep("pypi", m.group(1).split("[")[0], m.group(2).split(";")[0].strip(), str(req.relative_to(root))))
    pyproj = root / "pyproject.toml"
    if pyproj.exists():
        text, data = pyproj.read_text(errors="ignore"), {}
        try:
            import tomllib  # py3.11+; regex fallback below
            data = tomllib.loads(text)
        except Exception:  # noqa: BLE001
            pass
        items = list((data.get("project") or {}).get("dependencies", []))
        for k, v in ((data.get("tool") or {}).get("poetry", {}).get("dependencies", {}) or {}).items():
            items.append(f"{k}{v if isinstance(v, str) else (v or {}).get('version', '*')}")
        items = items or re.findall(r'"([A-Za-z0-9_.\-]+[^"]*)"', text.split("dependencies", 1)[-1][:4000])
        for item in items:
            m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(.*)$", item)
            if m and m.group(1).lower() != "python":
                deps.append(_dep("pypi", m.group(1), m.group(2).split(";")[0].strip(), "pyproject.toml"))
    pkg = root / "package.json"
    if pkg.exists():
        try:
            pj = json.loads(pkg.read_text(errors="ignore"))
        except json.JSONDecodeError:
            pj = {}
        for section in ("dependencies", "devDependencies"):
            deps += [_dep("npm", k, str(v), "package.json") for k, v in (pj.get(section) or {}).items()]
    gomod = root / "go.mod"
    if gomod.exists():
        for m in re.finditer(r"^\s*([\w./\-]+\.[\w/\-]+)\s+(v?[\w.+\-]+)", gomod.read_text(errors="ignore"), re.M):
            deps.append(_dep("go", m.group(1), m.group(2), "go.mod"))
    cargo = root / "Cargo.toml"
    if cargo.exists():
        for sect in re.split(r"^(?=\[)", cargo.read_text(errors="ignore"), flags=re.M):
            if not sect.startswith("[") or "dependencies" not in sect.split("]", 1)[0]:
                continue
            for m in re.finditer(r'^\s*([\w\-]+)\s*=\s*(?:"([^"]*)"|\{[^}]*version\s*=\s*"([^"]*)")', sect, re.M):
                deps.append(_dep("cargo", m.group(1), m.group(2) or m.group(3) or "*", "Cargo.toml"))
    return deps


def outdated(root: Path, deps: list[dict]) -> dict:
    out = {}
    if any(d["ecosystem"] == "pypi" for d in deps):
        proc = sh([sys.executable, "-m", "pip", "list", "--outdated", "--format=json", "--disable-pip-version-check"], cwd=root, timeout=120)
        try:
            names = {d["name"].lower().replace("_", "-") for d in deps}
            out["pip"] = [p for p in json.loads(proc.stdout or "[]") if p.get("name", "").lower().replace("_", "-") in names]
        except ValueError:
            out["pip"] = "skipped:tool-missing"
    if any(d["ecosystem"] == "npm" for d in deps):
        proc = sh(["npm", "outdated", "--json"], cwd=root, timeout=120) if which("npm") else None
        try:
            out["npm"] = json.loads(proc.stdout or "{}") if proc else "skipped:tool-missing"
        except ValueError:
            out["npm"] = "skipped:tool-missing"
    return out


def debt_register(root: Path, max_age_days: int) -> dict:
    git = which("git") and sh(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, timeout=30).returncode == 0
    by_file, total = {}, 0
    for p in iter_files(root, exts=SRC_EXTS):
        if p.stat().st_size > 1_000_000:
            continue
        counts: dict[str, int] = {}
        for m in DEBT_RE.finditer(p.read_text(errors="ignore")):
            tag = (m.group(1) or "DEPRECATED").upper()
            counts[tag] = counts.get(tag, 0) + 1
        if not counts:
            continue
        rel = str(p.relative_to(root))
        entry = {"counts": counts, "total": sum(counts.values()), "age_days": None}
        if git:
            proc = sh(["git", "log", "-1", "--format=%ct", "--", rel], cwd=root, timeout=30)
            if proc.stdout.strip().isdigit():
                entry["age_days"] = round((time.time() - int(proc.stdout.strip())) / 86400, 1)
        by_file[rel] = entry
        total += entry["total"]
    items = [{"item_id": f"TD-{i+1:03d}", "kind": "code", "source": "marker-scan", "location": f,
              "impact": f"{e['total']} debt marker(s)", "est_effort_h": e["total"],
              "priority_score": min(100, e["total"] * 10 + (20 if (e["age_days"] or 0) > max_age_days else 0))}
             for i, (f, e) in enumerate(sorted(by_file.items(), key=lambda kv: -kv[1]["total"]))]
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "total_markers": total,
            "files": by_file, "items": items, "git": "ok" if git else "skipped:tool-missing"}


def cve_match(cves: list[dict], deps: list[dict], task_id: str, corr) -> list[dict]:
    norm = lambda n: n.lower().replace("_", "-")  # noqa: E731
    tasks = []
    for c in cves:
        hits = [d for d in deps if norm(d["name"]) == norm(c.get("package", ""))]
        for d in hits:
            ver, spec = str(c.get("version", "")), d["spec"]
            if d["pin"] == "pinned" and ver and ver not in spec:
                continue  # pinned to a different version
            sev = str(c.get("severity", "medium")).lower()
            urgent = sev in ("critical", "kev") or bool(c.get("kev"))
            payload = {"task_id": f"{task_id}-{c.get('cve', 'CVE')}", "kind": "security-patch",
                       "driver": f"{c.get('cve')} ({sev}{', KEV' if c.get('kev') else ''})",
                       "targets": [f"{d['ecosystem']}:{d['name']}@{spec or '*'}"],
                       "deadline_s": 86400 if urgent else 7 * 86400, "gate_path": "standard",
                       "risk_class": "high" if urgent else "medium", "owner_class": "A05+A14",
                       "confidence": "high" if d["pin"] == "pinned" else "needs-triage"}
            tasks.append(sign_envelope(build_envelope(source="A14@local", target="A01", msg_type="patch.task", payload=payload,
                                                      correlation_id=corr, priority="P0" if urgent else "P1", risk_class=payload["risk_class"])))
    return tasks


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "deps": [{"ecosystem": "pypi", "name": "requests", "spec": "==2.32.0", "pin": "pinned"}],
                "unpinned": [], "outdated": {"pip": "skipped:tool-missing"}, "debt": {"total_markers": 0, "items": []},
                "patch_tasks": [], "findings": [], "summary": "dry-run: canned dependency report (0 unpinned, 0 CVE matches)"}
    deps = parse_deps(ctx.root)
    findings = []
    unpinned = [d for d in deps if d["pin"] != "pinned"]
    for i, d in enumerate(unpinned):
        findings.append(make_finding(f"MF-{i+1:03d}", "major" if d["pin"] == "wildcard" else "minor", "deps",
                                     f"{d['name']} is {d['pin']} ({d['spec'] or 'no version'})", location=d["file"], owner_suggestion="A14"))
    out = outdated(ctx.root, deps)
    debt = debt_register(ctx.root, args.max_age_days)
    SWARM_DIR.mkdir(parents=True, exist_ok=True)
    (SWARM_DIR / "debt_register.json").write_text(json.dumps(debt, indent=2))
    tasks = []
    if args.cve_file:
        cves = json.loads(Path(args.cve_file).read_text())
        tasks = cve_match(cves if isinstance(cves, list) else cves.get("cves", []), deps, args.task_id or "T-unassigned", ctx.correlation_id)
        tdir = SWARM_DIR / "patch_tasks"
        tdir.mkdir(exist_ok=True)
        for t in tasks:
            (tdir / f"{t['payload']['task_id']}.json").write_text(json.dumps(t, indent=2))
            findings.append(make_finding(f"MF-{len(findings)+1:03d}", "critical" if t["payload"]["deadline_s"] == 86400 else "major",
                                         "cve", t["payload"]["driver"], evidence=t["payload"]["targets"][0], owner_suggestion="A05"))
    status = "fail" if any(f["severity"] in ("major", "critical", "blocker") for f in findings) else "ok"
    return {"status": status, "deps": deps, "unpinned": unpinned, "outdated": out, "debt": debt,
            "patch_tasks": [t["payload"] for t in tasks], "findings": findings,
            "summary": f"{len(deps)} deps ({len(unpinned)} unpinned), {debt['total_markers']} debt markers, "
                       f"{len(tasks)} patch task(s) opened"}


def add_args(p):
    p.add_argument("--cve-file", help="JSON list of {package, version, cve, severity, kev?} to cross-match")
    p.add_argument("--max-age-days", type=int, default=90, help="debt markers older than this get a priority boost")


if __name__ == "__main__":
    sys.exit(AgentScript("A14", "maint_deps", run, description=__doc__, add_args=add_args).main())
