#!/usr/bin/env python3
"""A03 — validate OpenAPI / AsyncAPI contracts and detect breaking changes.

Finds contract files (--file or scan of --root for *.json/*.yaml/*.yml with a
top-level `openapi`/`asyncapi` key), validates: spec version, semver `info.version`,
every operation has `operationId` + `responses` (OpenAPI) or a message/action
(AsyncAPI). With --base, reports breaking changes: removed paths/operations/channels,
newly required parameters, and required properties added to component schemas.
YAML needs PyYAML
without it YAML files are reported as `skipped:tool-missing`.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, iter_files  # noqa: E402
from swarm.gates import make_finding, derive_verdict  # noqa: E402

try:
    import yaml  # type: ignore
except Exception:  # noqa: BLE001
    yaml = None

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


def load(path: Path):
    text = path.read_text(errors="replace")
    if path.suffix == ".json":
        return json.loads(text), None
    if yaml is None:
        return None, "skipped:tool-missing (PyYAML)"
    return yaml.safe_load(text), None


def discover(root: Path) -> list[Path]:
    out = []
    for p in iter_files(root, exts=(".json", ".yaml", ".yml")):
        try:
            head = p.read_text(errors="replace")[:4000]
        except OSError:
            continue
        if re.search(r'^\s*"?(openapi|asyncapi)"?\s*:', head, re.M):
            out.append(p)
    return sorted(out)


def operations(doc: dict) -> dict[str, dict]:
    """Return {"METHOD /path" | "channel:action": op} for either spec flavour."""
    ops = {}
    for path, item in (doc.get("paths") or {}).items():
        for m in METHODS:
            if isinstance(item, dict) and m in item:
                ops[f"{m.upper()} {path}"] = item[m] or {}
    for ch, item in (doc.get("channels") or {}).items():
        for act in ("publish", "subscribe"):
            if isinstance(item, dict) and act in item:
                ops[f"{ch}:{act}"] = item[act] or {}
    for name, op in (doc.get("operations") or {}).items():  # AsyncAPI 3
        ops[f"op:{name}"] = op or {}
    return ops


def required_props(doc: dict) -> dict[str, set]:
    schemas = (doc.get("components") or {}).get("schemas") or {}
    return {n: set((s or {}).get("required") or []) for n, s in schemas.items() if isinstance(s, dict)}


def validate(doc, path: Path, findings: list, fid) -> dict:
    loc = str(path)
    if not isinstance(doc, dict) or not (doc.get("openapi") or doc.get("asyncapi")):
        findings.append(make_finding(fid(), "major", "structure", "no top-level openapi/asyncapi key", location=loc))
        return {"file": loc, "kind": "unknown"}
    kind = "openapi" if doc.get("openapi") else "asyncapi"
    ver = str(doc.get(kind))
    if kind == "openapi" and not ver.startswith("3."):
        findings.append(make_finding(fid(), "major", "structure", f"unsupported openapi version {ver} (need 3.x)", location=loc))
    if kind == "asyncapi" and ver[:1] not in "23":
        findings.append(make_finding(fid(), "major", "structure", f"unsupported asyncapi version {ver} (need 2.x/3.x)", location=loc))
    info_ver = str((doc.get("info") or {}).get("version", ""))
    if not SEMVER.match(info_ver):
        findings.append(make_finding(fid(), "major", "versioning", f"info.version {info_ver!r} is not semver", location=loc))
    ops = operations(doc)
    if not ops:
        findings.append(make_finding(fid(), "minor", "structure", "contract declares no operations", location=loc))
    seen_ids = set()
    for key, op in ops.items():
        oid = op.get("operationId")
        if kind == "openapi" or ":" in key:
            if not oid:
                findings.append(make_finding(fid(), "major", "structure", f"{key} has no operationId", location=loc))
            elif oid in seen_ids:
                findings.append(make_finding(fid(), "major", "structure", f"duplicate operationId {oid}", location=loc))
            seen_ids.add(oid)
        if kind == "openapi" and not op.get("responses"):
            findings.append(make_finding(fid(), "major", "structure", f"{key} has no responses", location=loc))
        if kind == "asyncapi" and not (op.get("message") or op.get("messages") or op.get("action")):
            findings.append(make_finding(fid(), "major", "structure", f"{key} has no message/action", location=loc))
    return {"file": loc, "kind": kind, "spec_version": ver, "info_version": info_ver, "operations": len(ops)}


def breaking(base: dict, new: dict, loc: str, findings: list, fid) -> list[str]:
    changes = []
    b_ops, n_ops = operations(base), operations(new)
    for key in b_ops:
        if key not in n_ops:
            changes.append(f"removed operation {key}")
            continue
        b_req = {p.get("name") for p in b_ops[key].get("parameters") or [] if p.get("required")}
        n_req = {p.get("name") for p in n_ops[key].get("parameters") or [] if p.get("required")}
        for name in sorted(n_req - b_req):
            changes.append(f"{key}: new required parameter {name}")
        for code in set(b_ops[key].get("responses") or {}) - set(n_ops[key].get("responses") or {}):
            changes.append(f"{key}: removed response {code}")
    b_schemas, n_schemas = required_props(base), required_props(new)
    for name, req in b_schemas.items():
        if name not in n_schemas:
            changes.append(f"removed schema {name}")
        else:
            for prop in sorted(n_schemas[name] - req):
                changes.append(f"schema {name}: new required property {prop}")
    for c in changes:
        findings.append(make_finding(fid(), "major", "breaking", c, location=loc, owner_suggestion="A03"))
    b_v, n_v = str((base.get("info") or {}).get("version")), str((new.get("info") or {}).get("version"))
    if changes and b_v.split(".")[0] == n_v.split(".")[0]:
        findings.append(make_finding(fid(), "major", "versioning",
                                     f"breaking change without major bump ({b_v} → {n_v})", location=loc))
    return changes


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "summary": "dry-run: 1 contract valid, 0 breaking changes", "dry_run": True,
                "contracts": [{"file": "contracts/orders.yaml", "kind": "openapi", "spec_version": "3.1.0",
                               "info_version": "1.3.0", "operations": 4}], "breaking": [], "findings": [], "verdict": "pass"}
    counter = [0]

    def fid():
        counter[0] += 1
        return f"CF-{counter[0]:03d}"
    files = [ctx.root / args.file if not Path(args.file).is_absolute() else Path(args.file)] if args.file else discover(ctx.root)
    findings, contracts, changes, skipped = [], [], [], []
    for f in files:
        if not f.exists():
            findings.append(make_finding(fid(), "major", "input", f"file not found: {f}"))
            continue
        try:
            doc, skip = load(f)
        except Exception as e:  # noqa: BLE001 - parse errors are findings, not crashes
            findings.append(make_finding(fid(), "major", "input", f"cannot parse: {e}", location=str(f)))
            continue
        if skip:
            skipped.append({"file": str(f), "reason": skip})
            continue
        contracts.append(validate(doc, f, findings, fid))
        if args.base:
            base_path = ctx.root / args.base if not Path(args.base).is_absolute() else Path(args.base)
            base_doc, bskip = load(base_path) if base_path.exists() else (None, "missing")
            if base_doc is None:
                findings.append(make_finding(fid(), "major", "input", f"base contract unavailable: {bskip}", location=str(base_path)))
            else:
                changes += breaking(base_doc, doc, str(f), findings, fid)
    if not files:
        return {"status": "ok", "summary": "no OpenAPI/AsyncAPI contracts found", "contracts": [], "findings": [], "verdict": "pass"}
    verdict = derive_verdict(findings)
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict, "contracts": contracts,
            "breaking": changes, "skipped": skipped, "findings": findings,
            "summary": f"contract check {verdict.upper()} — {len(contracts)} contract(s), {len(changes)} breaking change(s), {len(findings)} finding(s)"}


def add_args(p):
    p.add_argument("--file", help="contract file to validate (default: scan --root)")
    p.add_argument("--base", help="previous contract revision to diff against for breaking changes")


if __name__ == "__main__":
    sys.exit(AgentScript("A03", "arch_contract_check", run, description=__doc__, add_args=add_args).main())
