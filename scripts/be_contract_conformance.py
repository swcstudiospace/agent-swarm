#!/usr/bin/env python3
"""A05 — check backend route implementations against an A03 `api.contract`.

Given --contract (OpenAPI 3.x JSON or YAML) and --root, greps route definitions
in source (FastAPI/Flask decorators, Express `app.get(...)`/`router.post(...)`,
Go net/http `HandleFunc`, chi/gorilla/gin `r.Get(...)`) and reports:
  * contract paths+methods with no implementation  (major ⇒ E-CONTRACT)
  * implemented routes not present in the contract  (minor ⇒ contract.change.request)
Path parameters are normalised ({id}, :id, <int:id>, {id:[0-9]+}) before comparison.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding  # noqa: E402

METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
SRC_EXTS = (".py", ".js", ".ts", ".mjs", ".cjs", ".go")
ROUTE_PATTERNS = [
    # FastAPI / Flask: @app.get("/x"), @router.route("/x", methods=["GET"])
    re.compile(r"@\w+(?:\.\w+)*\.(get|post|put|patch|delete|head|options)\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"@\w+(?:\.\w+)*\.route\(\s*['\"]([^'\"]+)['\"](?:.*?methods\s*=\s*\[([^\]]*)\])?"),
    # Express / Koa / Hono: app.get('/x', ...), router.post("/x", ...)
    re.compile(r"\b(?:app|router|server|api|r)\s*\.\s*(get|post|put|patch|delete|head|options)\(\s*['\"`]([^'\"`]+)['\"`]"),
    # Go chi/gorilla/gin: r.Get("/x", ...), router.POST("/x", ...)
    re.compile(r"\b\w+\.(Get|Post|Put|Patch|Delete|Head|Options|GET|POST|PUT|PATCH|DELETE)\(\s*\"([^\"]+)\""),
    # Go net/http: http.HandleFunc("/x", ...) / mux.Handle("/x", ...)  (method unknown ⇒ any)
    re.compile(r"\.(?:HandleFunc|Handle)\(\s*\"([^\"]+)\""),
]


def norm_path(p: str) -> str:
    p = p.split("?")[0].rstrip("/") or "/"
    p = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", p)          # Flask <int:id>
    p = re.sub(r":(\w+)", r"{\1}", p)                      # Express :id
    p = re.sub(r"\{(\w+):[^}]*\}", r"{\1}", p)             # chi {id:[0-9]+}
    return re.sub(r"\{[^}]+\}", "{}", p)                   # anonymise param names


def load_contract(path: Path) -> dict:
    text = path.read_text()
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
            return yaml.safe_load(text) or {}
        except ImportError:
            return _yaml_paths_fallback(text)
    return json.loads(text)


def _yaml_paths_fallback(text: str) -> dict:
    """Minimal YAML reader: extracts `paths:` → path → method keys by indentation only."""
    paths, cur, in_paths = {}, None, False
    for line in text.splitlines():
        if re.match(r"^paths:\s*$", line):
            in_paths = True
            continue
        if in_paths and re.match(r"^\S", line):
            break
        m = re.match(r"^  (/\S*):\s*$", line)
        if in_paths and m:
            cur = m.group(1)
            paths[cur] = {}
            continue
        m = re.match(r"^    (\w+):\s*$", line)
        if in_paths and cur and m and m.group(1).lower() in METHODS:
            paths[cur][m.group(1).lower()] = {}
    return {"paths": paths}


def contract_routes(doc: dict) -> set[tuple[str, str]]:
    routes = set()
    for p, ops in (doc.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for m in ops:
            if m.lower() in METHODS:
                routes.add((m.upper(), norm_path(p)))
    return routes


def implemented_routes(root: Path, prefix: str) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for f in iter_files(root, exts=SRC_EXTS):
        if any(part in ("tests", "test", "__tests__", "spec") for part in f.relative_to(root).parts):
            continue
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if line.lstrip().startswith(("#", "//", "*")):
                continue
            for pat in ROUTE_PATTERNS:
                m = pat.search(line)
                if not m:
                    continue
                g = m.groups()
                if len(g) == 1:
                    methods, path = ["ANY"], g[0]
                elif pat is ROUTE_PATTERNS[1]:
                    path = g[0]
                    methods = [x.strip(" '\"").upper() for x in (g[1] or "GET").split(",") if x.strip()]
                else:
                    methods, path = [g[0].upper()], g[1]
                if not path.startswith("/"):
                    continue
                for meth in methods:
                    found.setdefault((meth, norm_path(prefix + path)), f"{f.relative_to(root)}:{i}")
    return found


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "contract_routes": 3, "implemented_routes": 3, "missing": [], "extra": [],
                "findings": [], "dry_run": True, "summary": "dry-run: 3/3 contract routes implemented, 0 extra"}
    impl = implemented_routes(ctx.root, args.prefix.rstrip("/"))
    if not args.contract:
        return {"status": "ok", "contract_routes": 0, "implemented_routes": len(impl), "missing": [],
                "extra": sorted(f"{m} {p} ({loc})" for (m, p), loc in impl.items()), "findings": [],
                "summary": f"no --contract given; inventoried {len(impl)} implemented routes"}
    cpath = Path(args.contract)
    if not cpath.is_absolute():
        cpath = ctx.root / cpath
    if not cpath.exists():
        return {"status": "fail", "findings": [make_finding("BC-000", "major", "input", f"contract not found: {cpath}",
                                                            owner_suggestion="A03")],
                "summary": f"E-INPUT: contract file {cpath} missing"}
    want = contract_routes(load_contract(cpath))
    have_any = {p for (m, p) in impl if m == "ANY"}
    findings, missing, extra = [], [], []
    for meth, path in sorted(want):
        if (meth, path) not in impl and path not in have_any:
            missing.append(f"{meth} {path}")
            findings.append(make_finding(f"BC-{len(findings)+1:03d}", "major", "contract",
                                         f"contract route {meth} {path} has no implementation",
                                         evidence=str(cpath.name), owner_suggestion="A05"))
    want_paths = {p for _, p in want}
    for (meth, path), loc in sorted(impl.items()):
        if (meth, path) not in want and not (meth == "ANY" and path in want_paths):
            extra.append(f"{meth} {path} ({loc})")
            findings.append(make_finding(f"BC-{len(findings)+1:03d}", "minor", "contract",
                                         f"implemented route {meth} {path} not in contract (needs contract.change.request)",
                                         location=loc, owner_suggestion="A03"))
    status = "fail" if missing else "ok"
    return {"status": status, "contract": str(cpath), "contract_routes": len(want), "implemented_routes": len(impl),
            "missing": missing, "extra": extra, "findings": findings,
            "summary": f"contract conformance {status.upper()} — {len(want)-len(missing)}/{len(want)} routes implemented, "
                       f"{len(extra)} undocumented"}


def add_args(p):
    p.add_argument("--contract", help="OpenAPI 3.x contract (json/yaml); omit to only inventory routes")
    p.add_argument("--prefix", default="", help="mount prefix applied to implemented routes, e.g. /api/v1")


if __name__ == "__main__":
    sys.exit(AgentScript("A05", "be_contract_conformance", run, description=__doc__, add_args=add_args).main())
