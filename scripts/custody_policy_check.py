#!/usr/bin/env python3
"""A30 - Custody policy check: validate custody policy files and scan a repository for key-material patterns.

Policy files (JSON, or YAML when PyYAML imports) whose path contains custody, multisig or timelock carry any of these
sections, each shaped exactly like the input of the matching A30 tool and checked by it through the tool registry:
  multisig -> custody_multisig_policy        timelock -> custody_timelock_check
  signer_policy -> custody_signer_policy_lint    wallet_tiers -> custody_wallet_tier_caps
Every text file is scanned with custody_secret_scan for PEM private keys, BIP32 extended private keys, WIF keys, hex keys and
mnemonics assigned to key-like names, and encrypted keystores, in every directory except the silent skips: .git, .hg, .svn,
node_modules, .venv, venv, __pycache__, .tox, .mypy_cache, .swarm and .terraform. build and dist are scanned like any other
directory. Only locations and lengths are reported, never a value. A path with any secret-shaped part (a credential, a key, or
a credential label followed by key material, such as secret=<64 hex>) is shown as <redacted-path-N> (N = position in the walk;
for a directory reason, its position among the directory reasons), in the reasons too.
A credential label is one of these names, matched wherever it starts and in any letter case: private key, priv key, signing
key, secret, seed, mnemonic, password, passwd, token, api key, access key, pkey and pk. The words of a multi-word name may be
joined by '-', by '_' or by nothing. The label must end where a character that is not an ASCII letter or digit begins (tokens:
does not match token). From that character a suffix of up to 40 word characters, '.' or '-' may follow (secret_key, token_v2). It is
followed by optional whitespace, an optional quote, optional whitespace, one of '=', ':' or '=>', optional whitespace, an optional
quote and an optional 0x or 0X prefix.
The value is the run of A-Za-z0-9+/_- that follows, and also U+017F, U+212A, U+0131 and U+0130, which case-insensitive matching
admits, with up to two trailing '=' not counted (the core). A credential label's
value is key material when the core is 32 or more characters, its bare form (the core with '-' and '_' removed) is not one
repeated character (a placeholder), and one of these holds:
  - the bare form is hex of 32 or more digits, grouped or not;
  - the value has no '-' or '_' and holds a letter and a digit, or holds both upper and lower case letters;
  - the value has '-' or '_' and the bare form is 32 or more characters and holds both upper and lower case letters;
  - the value has '-' or '_' and its first segment (the run before the first '-' or '_') is hex of 32 or more digits and is not
    one repeated character, so a key followed by a suffix such as -missing or _old is key material.
Accepted behaviour and open decisions:
  - a path separator does not end such a value ('/' is in the base64 alphabet), so reports/token=Q3CustodyAuditReview2026/summary.json
    is redacted as a whole, file name included (an over-redaction);
  - a separated value whose bare form (the core with '-' and '_' removed) is 32 or more characters and holds both an upper-case
    and a lower-case letter is refused as an identifier, and redacted in a path, when it follows a credential label (for example
    token: approval-ticket-quarterly-OPS-review-2026-final). This is an open decision for the owner, and the rule is not changed.
    The remedy is a label that is not a credential label, or a shorter identifier.
An unseparated value that is key material by the rule above is refused as an identifier and redacted in a path on the same terms.
A reason is recorded for each case below. The reasons are listed inside one major `scan/incomplete` finding (up to five are
shown, then a count of the rest), so `status` is `fail` until every in-scope input is analysed:
  - a directory that cannot be listed;
  - a symlinked entry (a symlinked file or directory: never followed or read);
  - the size limit (a file over 1 MiB);
  - a binary file (a NUL byte in its first 8 KiB);
  - a FIFO, socket, device or other non-regular file, or a file that cannot be read;
  - the file limit (more than 2000 files: the rest of the tree is not scanned);
  - a secret-scan failure (the key-material scan of a file did not complete);
  - a policy file with no recognised section, or one that does not parse as a mapping;
  - PyYAML missing (a YAML policy file is not checked; reported as `skipped:tool-missing`).

Read-only: it reads files under --root and writes nothing. Bounded: files over 1 MiB, binary files and symlinks are skipped
(a symlink is never followed or read, not even when it is the --path), and at most 2000 files are visited. Stdlib only;
YAML-dependent checks report `skipped:tool-missing` when PyYAML is absent. Findings carry the severity info|minor|major|blocker
and `status` is `fail` when any major or worse exists. `--dry-run` prints deterministic canned output and touches nothing.
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
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.tools.a30_tools import _path_label as path_label  # noqa: E402  (one definition of "secret-shaped path")

AGENT = "A30"
FRAGMENT = Path(__file__).resolve().parent.parent / "registry" / "intake" / "a30-custody-compliance.tools.json"
MAX_FILES = 2000
MAX_BYTES = 1 << 20
BINARY_PROBE = 8192
# Silent by design, never read and never a reason: VCS metadata, installed third-party packages, generated tool caches, and the
# swarm's own state. build and dist are not in this set: they are scanned like any other directory.
SKIP_DIRS = frozenset((".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".swarm", ".tox", ".mypy_cache",
                       ".terraform"))
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


def dir_label(path: str, root: Path, ordinal: int) -> str:
    """A directory as a reason shows it: repository-relative, then the same redaction as file paths, so a directory named like
    a key shows as its <redacted-path-N> label and never by its name."""
    p = Path(path)
    rel = p.relative_to(root).as_posix() if p.is_relative_to(root) else "<outside-root>"
    return path_label(rel, ordinal)


def walk(base: Path, reasons: list[str], root: Path):
    """Files below base in a deterministic order. A symlink (a file, a directory, or base itself) is yielded and never followed:
    read_text reports it as skipped. A directory that cannot be listed is recorded in `reasons` (labelled, never named) because
    its contents were not analysed. SKIP_DIRS are out of scope by design and silent."""
    if base.is_symlink() or not base.is_dir():
        yield base
        return
    unlistable = 0

    def unreadable(error: OSError) -> None:
        nonlocal unlistable
        unlistable += 1
        reasons.append(f"{dir_label(error.filename or str(base), root, unlistable)} directory not listed: {type(error).__name__}")

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False, onerror=unreadable):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        linked_dirs = [d for d in dirnames if (Path(dirpath) / d).is_symlink()]
        for name in sorted([*filenames, *linked_dirs]):
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



def invalid_summary(document: dict, tool: str, section: str, body: dict, result: dict) -> str:
    """The policy-invalid summary. The registry quotes an unexpected key in its message, at any depth (a signer's extra key is
    reported under `signers`), and a key can be secret-shaped, so an unexpected-field error is reported without the message:
    the count of unexpected top-level keys comes from the section body against the tool's schema. When that count is zero the
    key is nested, and the summary says so without a number."""
    message = str(result.get("message"))
    if "unexpected field" in message:
        spec = tool_registry.find_tool(document, tool) or {}
        known = (spec.get("input_schema") or {}).get("properties") or {}
        unexpected = sum(1 for key in body if key not in known)
        if unexpected:
            return f"{section} section has {unexpected} unexpected top-level key(s); a nested object may hold more; names and values are not reported"
        return f"{section} section has an unexpected key in a nested object; the key is not named and its value is not reported"
    return f"{result.get('field')}: {message[:MESSAGE_LIMIT]}"




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
            out.append(finding(f"{section}:policy-invalid", "major", invalid_summary(document, tool, section, body, result), shown, section=section))
        else:
            out.append(finding(f"{section}:check-failed", "major", f"{tool} returned {result.get('state')}", shown, section=section))
    return out, found


def scan_secrets(document: dict, rel: str, text: str) -> tuple[list[dict], int, bool]:
    """(findings, omitted count, whether the scan ran to completion)."""
    result = call(document, "custody_secret_scan", {"files": [{"path": rel, "content": text}]})
    if result.get("state") != "SUCCESS":
        return [finding("secret-scan-failed", "major", f"key-material scan of {rel} returned {result.get('state')}", rel)], 0, False
    out = [finding(f"secret:{item['id']}", item["severity"], item["summary"], item["location"], kind=item["kind"], length=item["length"])
           for item in result["findings"]]
    return out, result["omitted_findings"], True


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "findings": [], "files_scanned": 0, "policy_files": 0, "secret_findings": 0,
                "counts": {s: 0 for s in SEVERITIES}, "files_skipped": {"binary": 0, "symlink": 0, "too_large": 0, "unreadable": 0},
                "skipped": [], "truncated": False,
                "summary": "dry-run: would validate custody policy sections and scan --path under --root for key material"}
    lexical = Path(os.path.normpath(ctx.root / args.path))
    target = lexical.parent.resolve() / lexical.name  # the final component is not resolved: a symlink is skipped, never read
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {path_label(args.path, 1)!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {path_label(args.path, 1)!r} does not exist under {ctx.root}")
    document = registry_data()
    if document is None:
        raise SwarmError(ErrorCode.E_DEP, "the A30 custody tools are not registered (agents.json and the intake fragment lack them)")
    yaml = yaml_module()
    findings: list[dict] = []
    reasons: list[str] = []
    skipped_counts = {"binary": 0, "symlink": 0, "too_large": 0, "unreadable": 0}
    skipped: list[dict] = []
    yaml_policies = scanned = policies = omitted = 0
    truncated = False
    for index, path in enumerate(walk(target, reasons, ctx.root)):
        if index >= MAX_FILES:
            truncated = True
            break
        rel = path.relative_to(ctx.root).as_posix()
        shown = path_label(rel, index + 1)
        text, reason = read_text(path)
        if text is None:
            skipped_counts[reason] += 1
            reasons.append(f"{shown} skipped: {reason}")
            continue
        scanned += 1
        secret_findings, more, complete = scan_secrets(document, shown, text)
        findings += secret_findings
        omitted += more
        if not complete:
            reasons.append(f"{shown} not scanned for key material")
        if not is_policy_candidate(rel):
            continue
        if not rel.lower().endswith(".json") and yaml is None:
            yaml_policies += 1
            reasons.append(f"{shown} policy not checked: PyYAML is missing")
            continue
        policies += 1
        policy_findings, found = check_policy(document, rel, text, yaml, shown)
        findings += policy_findings
        if not found:
            reasons.append(f"{shown} policy not analysed: no recognised section, or not a parseable mapping")
    if yaml_policies:
        skipped.append({"check": "yaml-policy-files", "status": "skipped:tool-missing", "tool": "PyYAML", "files": yaml_policies})
    if truncated:
        reasons.append(f"scan stopped after {MAX_FILES} files; the rest of the tree was not scanned")
    findings += [{**item, "location": "."} for item in incomplete_findings(reasons, scanned)]
    findings.sort(key=lambda f: (-SEVERITIES.index(f["severity"]), f["location"], f["id"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    secrets = sum(1 for f in findings if f["id"].startswith("secret:"))
    return {
        "status": scan_status(findings), "findings": findings, "files_scanned": scanned, "policy_files": policies,
        "secret_findings": secrets + omitted, "counts": counts, "files_skipped": skipped_counts, "skipped": skipped, "truncated": truncated,
        "summary": f"{scanned} file(s) scanned, {policies} policy file(s) checked, {secrets + omitted} key-material finding(s); "
                   f"{counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info",
    }


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A30", "custody_policy_check", run, description=__doc__, add_args=add_args).main())
