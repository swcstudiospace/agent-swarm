#!/usr/bin/env python3
"""A30 - Custody policy check: validate custody policy files and scan a repository for key-material patterns.

Policy files (JSON, or YAML when PyYAML imports) whose path contains custody, multisig or timelock carry any of these
sections, each shaped exactly like the input of the matching A30 tool and checked by it through the tool registry:
  multisig -> custody_multisig_policy        timelock -> custody_timelock_check
  signer_policy -> custody_signer_policy_lint    wallet_tiers -> custody_wallet_tier_caps
Every text file is scanned with custody_secret_scan for PEM private keys, BIP32 extended private keys, WIF keys, hex keys
and mnemonics assigned to key-like names, and encrypted keystores. Only locations and lengths are reported, never a value;
a path that is itself secret-shaped is shown as <redacted-path-N> (N = position in the walk).

Read-only: it reads files under --root and writes nothing. Bounded: files over 1 MiB and binary files are skipped, at most
2000 files are visited, symlinks are never followed or read. Stdlib only; YAML-dependent checks report
`skipped:tool-missing` when PyYAML is absent. Findings carry the severity info|minor|major|blocker and `status` is `fail`
when any major or worse exists. `--dry-run` prints deterministic canned output and touches nothing.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm import tool_registry  # noqa: E402
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a30_tools import _path_label as path_label  # noqa: E402  (one definition of "secret-shaped path")

AGENT = "A30"
FRAGMENT = Path(__file__).resolve().parent.parent / "registry" / "intake" / "a30-custody-compliance.tools.json"
MAX_FILES = 2000
MAX_BYTES = 1 << 20
BINARY_PROBE = 8192
SKIP_DIRS = frozenset((".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".tox", ".mypy_cache"))
POLICY_NAME_HINTS = ("custody", "multisig", "timelock")
POLICY_SUFFIXES = (".json", ".yaml", ".yml")
SECTIONS = {
    "multisig": "custody_multisig_policy",
    "timelock": "custody_timelock_check",
    "signer_policy": "custody_signer_policy_lint",
    "wallet_tiers": "custody_wallet_tier_caps",
}
SEVERITIES = ("info", "minor", "major", "blocker")
MESSAGE_LIMIT = 240


def registry_data() -> dict | None:
    """The registry document containing the A30 tools: agents.json, or agents.json plus the not yet merged intake fragment."""
    try:
        document = tool_registry.load_document()
    except (OSError, ValueError):
        return None
    if tool_registry.find_tool(document, "custody_secret_scan") is not None:
        return document
    try:
        if not FRAGMENT.is_file():  # repo-owned, but a FIFO here would block the lookup just as it would a scan input
            return None
        entries = json.loads(FRAGMENT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for agent in document.get("agents") or []:
        if isinstance(agent, dict) and agent.get("id") == AGENT:
            agent["registered_tools"] = entries
            return document
    return None


def call(document: dict, name: str, payload: dict) -> dict:
    return tool_registry.call_tool(name, AGENT, payload, data=document)


def finding(fid: str, severity: str, summary: str, location: str, **extra) -> dict:
    return {"id": fid, "severity": severity, "summary": summary[:MESSAGE_LIMIT * 2], "location": location, **extra}


def is_policy_candidate(rel: str) -> bool:
    lowered = rel.lower()
    return lowered.endswith(POLICY_SUFFIXES) and any(hint in lowered for hint in POLICY_NAME_HINTS)


def yaml_module():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed at use
    except ImportError:
        return None
    return yaml


def walk(base: Path):
    """Files below base in a deterministic order; symlinked directories are not descended into."""
    if base.is_file() or base.is_symlink():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def read_text(path: Path) -> tuple[str | None, str]:
    """(text, "") for a readable regular file within the size bound, else (None, reason). Named pipes, sockets and devices are
    "unreadable": opening a FIFO with no writer blocks the scan, so they are never opened."""
    try:
        if path.is_symlink():
            return None, "symlink"
        if not path.is_file():
            return None, "unreadable"
        if path.stat().st_size > MAX_BYTES:
            return None, "too_large"
        data = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if b"\0" in data[:BINARY_PROBE]:
        return None, "binary"
    return data.decode("utf-8", errors="replace"), ""


def check_policy(document: dict, rel: str, text: str, yaml, shown: str | None = None) -> tuple[list[dict], bool]:
    """Findings for one policy candidate; the flag says whether any known section was present. `shown` is the path findings
    display (`rel` unless it is secret-shaped)."""
    shown = rel if shown is None else shown
    try:
        parsed = json.loads(text) if rel.lower().endswith(".json") else yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - any parser error is a finding about the file, not a crash
        return [finding("policy-unparseable", "major", f"cannot parse {shown}: {type(exc).__name__}", shown)], False
    if not isinstance(parsed, dict):
        return [finding("policy-not-an-object", "major", "the policy document must be a mapping of sections", shown)], False
    out: list[dict] = []
    found = False
    for section, tool in SECTIONS.items():
        if section not in parsed:
            continue
        found = True
        body = parsed[section]
        if not isinstance(body, dict) or "probe" in body:
            out.append(finding(f"{section}:policy-section-malformed", "major", f"section {section} must be a mapping of the {tool} inputs", shown, section=section))
            continue
        result = call(document, tool, body)
        if result.get("state") == "SUCCESS":
            for item in result["findings"]:
                out.append(finding(f"{section}:{item['id']}", item["severity"], item["summary"], shown, section=section))
        elif result.get("state") == "INVALID_INPUT":
            out.append(finding(f"{section}:policy-invalid", "major", f"{result.get('field')}: {str(result.get('message'))[:MESSAGE_LIMIT]}", shown, section=section))
        else:
            out.append(finding(f"{section}:check-failed", "major", f"{tool} returned {result.get('state')}", shown, section=section))
    return out, found


def scan_secrets(document: dict, rel: str, text: str) -> tuple[list[dict], int]:
    result = call(document, "custody_secret_scan", {"files": [{"path": rel, "content": text}]})
    if result.get("state") != "SUCCESS":
        return [finding("secret-scan-failed", "major", f"key-material scan of {rel} returned {result.get('state')}", rel)], 0
    out = [finding(f"secret:{item['id']}", item["severity"], item["summary"], item["location"], kind=item["kind"], length=item["length"])
           for item in result["findings"]]
    return out, result["omitted_findings"]


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "findings": [], "files_scanned": 0, "policy_files": 0, "secret_findings": 0,
                "counts": {s: 0 for s in SEVERITIES}, "files_skipped": {"binary": 0, "symlink": 0, "too_large": 0, "unreadable": 0},
                "skipped": [], "truncated": False,
                "summary": "dry-run: would validate custody policy sections and scan --path under --root for key material"}
    target = (ctx.root / args.path).resolve()
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {ctx.root}")
    document = registry_data()
    if document is None:
        raise SwarmError(ErrorCode.E_DEP, "the A30 custody tools are not registered (agents.json and the intake fragment lack them)")
    yaml = yaml_module()
    findings: list[dict] = []
    skipped_counts = {"binary": 0, "symlink": 0, "too_large": 0, "unreadable": 0}
    skipped: list[dict] = []
    yaml_policies = scanned = policies = omitted = 0
    truncated = False
    for index, path in enumerate(walk(target)):
        if index >= MAX_FILES:
            truncated = True
            break
        rel = path.relative_to(ctx.root).as_posix()
        shown = path_label(rel, index + 1)
        text, reason = read_text(path)
        if text is None:
            skipped_counts[reason] += 1
            continue
        scanned += 1
        secret_findings, more = scan_secrets(document, shown, text)
        findings += secret_findings
        omitted += more
        if not is_policy_candidate(rel):
            continue
        if not rel.lower().endswith(".json") and yaml is None:
            yaml_policies += 1
            continue
        policies += 1
        policy_findings, found = check_policy(document, rel, text, yaml, shown)
        findings += policy_findings
        if not found and not policy_findings:
            findings.append(finding("no-policy-sections", "info", f"{shown} names a custody policy but has none of: {', '.join(SECTIONS)}", shown))
    if yaml_policies:
        skipped.append({"check": "yaml-policy-files", "status": "skipped:tool-missing", "tool": "PyYAML", "files": yaml_policies})
    if truncated:
        findings.append(finding("scan-limit-reached", "minor", f"stopped after {MAX_FILES} files; the rest of the tree was not scanned", "."))
    findings.sort(key=lambda f: (-SEVERITIES.index(f["severity"]), f["location"], f["id"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    secrets = sum(1 for f in findings if f["id"].startswith("secret:"))
    bad = counts["major"] + counts["blocker"]
    return {
        "status": "fail" if bad else "ok", "findings": findings, "files_scanned": scanned, "policy_files": policies,
        "secret_findings": secrets + omitted, "counts": counts, "files_skipped": skipped_counts, "skipped": skipped, "truncated": truncated,
        "summary": f"{scanned} file(s) scanned, {policies} policy file(s) checked, {secrets + omitted} key-material finding(s); "
                   f"{counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info",
    }


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A30", "custody_policy_check", run, description=__doc__, add_args=add_args).main())
