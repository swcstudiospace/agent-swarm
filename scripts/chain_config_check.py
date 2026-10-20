#!/usr/bin/env python3
"""A28 - validate chain and RPC configuration files: https endpoints, no credentials embedded in URLs, chain-id consistency and an explicit mainnet opt-in.

Scans JSON, TOML, YAML (only when PyYAML imports) and .env files under --root/--path for chain configuration: RPC URLs
(keys that contain rpc, endpoint, websocket, nodeurl or providerurl) and chain ids (chainId, chain_id, EIP-3085 hex or
decimal text). It reports: userinfo credentials and API keys in URLs (blocker), plaintext http or ws to a non-loopback
host (major), a mainnet-grade or unknown chain id without an explicit opt-in key such as allowMainnet (major), a chain id
that contradicts the network name or the endpoint host (blocker or major) and one network name mapped to two chain ids
(major). URL values, network names that could hold a secret and odd config keys are never printed: findings carry the file
and a key path whose segments are plain names. A value made only of environment-variable templates (${KEY}, $KEY, {{KEY}},
<KEY>, %KEY%) is a reference, not a credential.
Read-only: nothing is written. Stdlib only; it skips files over 1 MiB, binaries and symlinks, stops after 2000 candidate
files (a minor scan-truncated finding says so) and never follows a symlink out of the root; one document is read for at most
200000 nodes (a YAML alias bomb is cut off and reported). At most 500 findings are listed, most severe first, and the ones
that do not fit still count in `counts` and in the status. PyYAML missing is reported as skipped:tool-missing for the YAML
check; JSON, TOML and .env are handled natively. `--dry-run` prints deterministic canned output and touches nothing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a28_tools import CHAINS, MAX_CHAIN_ID, SEVERITY_RANK, TESTNET_NAME_TOKENS, _secret_like  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_CANDIDATES = 2000
MAX_VISITED = 50000
MAX_FINDINGS = 500  # listed; up to MAX_STORED are collected so the most severe are the ones listed
MAX_STORED = 20000
MAX_DEPTH = 30
MAX_NODES = 200_000  # per document: a YAML alias bomb is a small file that expands into a huge shared graph
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".hg", ".svn"}
SKIP_FILES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "composer.lock"}
SUFFIX_KIND = {".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".env": "env"}
CHAIN_ID_KEYS = {"chainid", "evmchainid", "networkchainid"}
NAME_KEYS = {"name", "network", "chain", "chainname", "networkname"}
OPT_IN_KEYS = {"allowmainnet", "mainnetoptin", "mainnetapproved", "enablemainnet", "mainnetenabled"}
URL_KEY_PARTS = ("rpc", "endpoint", "websocket", "nodeurl", "providerurl")
NOT_RPC_URL_PARTS = ("explorer", "faucet", "icon", "website", "docs", "info", "homepage")
SECRET_PARAMS = {"key", "apikey", "api_key", "api-key", "token", "access_token", "accesstoken", "auth", "secret", "authorization", "x-api-key", "password", "passwd", "pwd", "jwt", "bearer", "client_secret", "private_key", "privatekey"}
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
SEGMENT_KEY_RE = re.compile(r"[A-Za-z0-9_-]{20,}")
# environment-variable templates (${KEY}, $KEY, {{KEY}}, {KEY}, <KEY>, %KEY%): a bare "%" or "$" inside a literal secret is not one
TEMPLATE_RE = re.compile(r"\$\{[^{}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|\{\{[^{}]+\}\}|\{[A-Za-z_][A-Za-z0-9_]*\}|<[^<>]+>|%[A-Za-z_][A-Za-z0-9_]*%")

NAME_TO_ID: dict[str, int] = {}
for _id, (_name, _cls, _aliases) in CHAINS.items():
    NAME_TO_ID[re.sub(r"[^a-z0-9]+", "-", _name.lower()).strip("-")] = _id
    for _alias in _aliases:
        NAME_TO_ID[_alias] = _id


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _safe_key(key) -> str:
    """A config key as it may appear in a location: short names only, anything that could hold a secret is replaced."""
    text = str(key)
    return text if re.fullmatch(r"[A-Za-z0-9 _.:-]{1,60}", text) and not _secret_like(text) else "<key>"


def _safe_name(name: str) -> str:
    text = name.strip()
    return text if re.fullmatch(r"[A-Za-z0-9 ._-]{1,60}", text) and not _secret_like(text) else "<redacted>"


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


CANONICAL_NAME_TO_ID: dict[str, int] = {_norm_name(name): _id for _id, (name, _cls, _aliases) in CHAINS.items()}


def _ambiguous_name(norm: str) -> bool:
    """True for a name that is also the leading word(s) of another chain's full name ("base" of "base-sepolia")."""
    return any(other.startswith(norm + "-") for other in CANONICAL_NAME_TO_ID)


def _tokens(text: str) -> set[str]:
    """Whole words of a name or host: a testnet word must match a whole token, never a piece of another word."""
    return {part for part in re.split(r"[^a-z0-9]+", text.lower()) if part}


def _parse_chain_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = int(text, 16) if text.lower().startswith("0x") else int(text, 10) if text.isdigit() else -1
        except ValueError:
            return None
    else:
        return None
    return parsed if 0 < parsed <= MAX_CHAIN_ID else None


def _truthy(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "1", "yes"))


def _chain_class(chain_id: int) -> str:
    known = CHAINS.get(chain_id)
    return known[1] if known else "unknown"


def _placeholder(value: str | None) -> bool:
    """True when the value is made only of environment-variable templates and punctuation, never of literal text."""
    return bool(value) and TEMPLATE_RE.search(value) is not None and re.search(r"[A-Za-z0-9]", TEMPLATE_RE.sub("", value)) is None


def _url_problems(url: str) -> list[tuple[str, str, str]]:
    """(rule, severity, message) for one URL; the URL itself is never put in a message."""
    out: list[tuple[str, str, str]] = []
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return [("rpc-url-malformed", "minor", "the endpoint is not a parseable URL")]
    scheme = parts.scheme.lower()
    userinfo = [part for part in (parts.username, parts.password) if part is not None]
    if userinfo and not all(_placeholder(part) for part in userinfo):  # ${USER}:${PASS}@host is a template, not a credential
        out.append(("rpc-url-credentials", "blocker", "the endpoint embeds user:password credentials; move them to a secret store and reference an environment variable"))
    if scheme in ("http", "ws"):
        if host in LOOPBACK:
            out.append(("rpc-url-local-plaintext", "info", f"plaintext {scheme} to a loopback host is acceptable for a local node"))
        else:
            out.append(("rpc-url-insecure-scheme", "major", f"the endpoint uses plaintext {scheme}: use {'https' if scheme == 'http' else 'wss'}"))
    elif scheme not in ("https", "wss"):
        out.append(("rpc-url-scheme", "minor", f"the endpoint scheme {scheme or '(none)'} is not https or wss"))
    leaked = [name for name, val in parse_qsl(parts.query, keep_blank_values=True) if name.lower() in SECRET_PARAMS and val and not _placeholder(val)]
    segments = [s for s in parts.path.split("/") if SEGMENT_KEY_RE.fullmatch(s) and s.count("-") <= 1 and re.search(r"\d", s) and re.search(r"[A-Za-z]", s)]
    if leaked or segments:
        where = f"query parameter {leaked[0].lower()}" if leaked else "a long token-like path segment"
        out.append(("rpc-url-embedded-key", "blocker", f"the endpoint carries an API key in {where}; reference it from an environment variable instead"))
    return out


def _host_tokens(url: str) -> str:
    try:
        parts = urlsplit(url)
        return ((parts.hostname or "") + parts.path).lower()
    except ValueError:
        return ""


class _Scan:
    def __init__(self) -> None:
        self.findings: list[dict] = []
        self.truncated = False
        self.dropped = {sev: 0 for sev in SEVERITY_RANK}  # findings past MAX_STORED still count toward the status
        self.names: dict[str, dict[int, str]] = {}
        self.budget = MAX_NODES
        self.exhausted = False

    def add(self, rel: str, rule: str, severity: str, where: str, message: str) -> None:
        if len(self.findings) >= MAX_STORED:
            self.truncated = True
            self.dropped[severity] += 1
            return
        location = f"{rel}:{where}" if where else rel
        self.findings.append({"id": f"{rule}:{location}", "severity": severity, "rule": rule, "file": rel, "location": location, "summary": message, "message": message})

    def walk(self, rel: str, node, path: str, ctx: dict, depth: int, in_rpc: bool = False) -> None:
        if depth > MAX_DEPTH or self.budget <= 0:
            self.exhausted = self.exhausted or self.budget <= 0
            return
        self.budget -= 1
        if isinstance(node, dict):
            keys = {_norm_key(k): k for k in node}
            chain_key = next((keys[k] for k in sorted(keys) if k in CHAIN_ID_KEYS), None)
            if chain_key is None and "id" in keys and ("rpcurls" in keys or "nativecurrency" in keys):
                chain_key = keys["id"]
            opt_key = next((k for k in keys if k in OPT_IN_KEYS and _truthy(node[keys[k]])), None)
            here = dict(ctx)
            if opt_key is not None:
                here["opt_in"] = True
            if chain_key is not None:
                chain_id = _parse_chain_id(node[chain_key])
                where = f"{path}.{_safe_key(chain_key)}" if path else _safe_key(chain_key)
                if chain_id is None:
                    self.add(rel, "chain-id-unparseable", "minor", where, "the chain id is not a positive integer, decimal text or 0x hex text")
                else:
                    here.update(chain_id=chain_id, where=where)
                    self.check_chain(rel, chain_id, where, here, next((node[keys[k]] for k in sorted(keys) if k in NAME_KEYS and isinstance(node[keys[k]], str)), None))
            for key, value in node.items():
                child = f"{path}.{_safe_key(key)}" if path else _safe_key(key)
                norm = _norm_key(key)
                rpc_here = in_rpc or (any(part in norm for part in URL_KEY_PARTS) and not any(part in norm for part in NOT_RPC_URL_PARTS))
                if not rpc_here and chain_key is not None and "url" in norm and not any(part in norm for part in NOT_RPC_URL_PARTS):
                    rpc_here = True
                self.walk(rel, value, child, here, depth + 1, rpc_here)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                self.walk(rel, value, f"{path}[{index}]", ctx, depth + 1, in_rpc)
        elif isinstance(node, str) and in_rpc and URL_RE.match(node.strip()):
            self.check_url(rel, node.strip(), path, ctx)

    def check_chain(self, rel: str, chain_id: int, where: str, ctx: dict, name) -> None:
        cls = _chain_class(chain_id)
        if cls in ("mainnet", "unknown") and not ctx.get("opt_in"):
            self.add(rel, "mainnet-without-opt-in", "major", where, f"chain id {chain_id} ({'unknown, treated as mainnet' if cls == 'unknown' else CHAINS[chain_id][0]}) is configured without an explicit mainnet opt-in key such as allowMainnet")
        if cls == "unknown":
            self.add(rel, "chain-id-unknown", "minor", where, f"chain id {chain_id} is not in the reference table of known networks")
        if name is None:
            return
        norm = _norm_name(name)
        shown = _safe_name(name)
        expected = NAME_TO_ID.get(norm)
        if expected is not None and expected != chain_id:
            if CANONICAL_NAME_TO_ID.get(norm) == expected and not _ambiguous_name(norm):
                self.add(rel, "chain-id-name-mismatch", "blocker", where, f"the network name {shown!r} belongs to chain id {expected}, the file says {chain_id}")
            else:
                self.add(rel, "chain-id-name-mismatch", "minor", where, f"the network name {shown!r} is not the full name of chain id {expected} and may mean another network; the file says {chain_id}")
        elif expected is None:
            says_test = not _tokens(norm).isdisjoint(TESTNET_NAME_TOKENS)
            if says_test and cls == "mainnet":
                self.add(rel, "chain-id-name-mismatch", "blocker", where, f"the network name {shown!r} says test network but chain id {chain_id} is a mainnet")
            elif "mainnet" in norm and cls in ("testnet", "local"):
                self.add(rel, "chain-id-name-mismatch", "major", where, f"the network name {shown!r} says mainnet but chain id {chain_id} is a {cls} chain")
        by_id = self.names.setdefault(norm, {})
        if chain_id not in by_id:
            if by_id:
                first_id, first_where = sorted(by_id.items())[0]
                self.add(rel, "chain-id-conflict", "major", where, f"the network name {shown!r} maps to chain id {chain_id} here and to {first_id} at {first_where}")
            by_id[chain_id] = f"{rel}:{where}"

    def check_url(self, rel: str, url: str, where: str, ctx: dict) -> None:
        for rule, severity, message in _url_problems(url):
            self.add(rel, rule, severity, where, message)
        chain_id = ctx.get("chain_id")
        if chain_id is None:
            return
        cls = _chain_class(chain_id)
        host = _host_tokens(url)
        if not _tokens(host).isdisjoint(TESTNET_NAME_TOKENS) and cls == "mainnet":
            self.add(rel, "rpc-host-chain-mismatch", "major", where, f"the endpoint host names a test network but the chain id {chain_id} is a mainnet")
        elif "mainnet" in host and cls in ("testnet", "local"):
            self.add(rel, "rpc-host-chain-mismatch", "major", where, f"the endpoint host names a mainnet but the chain id {chain_id} is a {cls} chain")


def _load_yaml_module():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed on use
    except ImportError:
        return None
    return yaml


def _load_toml_module():
    try:
        import tomllib  # noqa: PLC0415 - stdlib on Python 3.11+, absent on older interpreters
    except ImportError:
        return None
    return tomllib


def _env_value(raw: str) -> str:
    """A dotenv value: up to the matching closing quote when quoted, else up to a ` #` inline comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        out[key.strip()] = _env_value(value)
    return out


def _candidates(base: Path, root: Path):
    """Yield (path, kind) for chain-config candidates, depth-first in sorted order, symlinks never followed."""
    visited = 0
    if base.is_file():
        kind = _kind(base)
        if kind and not base.is_symlink():
            yield base, kind
        return
    for current, dirs, files in os.walk(base, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(current, d)))
        for name in sorted(files):
            visited += 1
            if visited > MAX_VISITED:
                return
            path = Path(current) / name
            kind = _kind(path)
            if kind and name not in SKIP_FILES:
                yield path, kind


def _kind(path: Path) -> str | None:
    if path.name == ".env" or path.name.startswith(".env."):
        return "env"
    return SUFFIX_KIND.get(path.suffix.lower())


def scan(root: Path, base: Path) -> dict:
    yaml_mod, toml_mod = _load_yaml_module(), _load_toml_module()
    scanner = _Scan()
    seen = {"json": 0, "yaml": 0, "toml": 0, "env": 0}
    skipped = {"too_large": 0, "binary": 0, "symlink": 0, "unparsed": 0}
    candidates, truncated_files = 0, False
    for path, kind in _candidates(base, root):
        if path.is_symlink():
            skipped["symlink"] += 1
            continue
        if candidates >= MAX_CANDIDATES:
            truncated_files = True
            break
        candidates += 1
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        if kind == "yaml" and yaml_mod is None:
            seen["yaml"] += 1
            continue
        if kind == "toml" and toml_mod is None:
            seen["toml"] += 1
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped["too_large"] += 1
                continue
            raw = path.read_bytes()
        except OSError:
            skipped["unparsed"] += 1
            continue
        if b"\0" in raw:
            skipped["binary"] += 1
            continue
        text = raw.decode("utf-8", errors="replace")
        try:
            if kind == "json":
                docs = [json.loads(text)]
            elif kind == "toml":
                docs = [toml_mod.loads(text)]
            elif kind == "yaml":
                docs = list(yaml_mod.safe_load_all(text))
            else:
                docs = [_parse_env(text)]
        except (ValueError, RecursionError, yaml_mod.YAMLError if yaml_mod else ValueError):
            skipped["unparsed"] += 1
            continue
        seen[kind] += 1
        for doc in docs:
            scanner.budget, scanner.exhausted = MAX_NODES, False
            scanner.walk(rel, doc, "", {}, 0)
            if scanner.exhausted:
                scanner.add(rel, "scan-truncated", "minor", "", f"the document has more than {MAX_NODES} nodes: only part of it was checked")
    if truncated_files:
        scanner.add(".", "scan-truncated", "minor", "", f"more than {MAX_CANDIDATES} candidate files: the rest were not scanned")
    checks = {
        "json": "ran" if seen["json"] else "no-files",
        "toml": "skipped:tool-missing" if toml_mod is None and seen["toml"] else "ran" if seen["toml"] else "no-files",
        "yaml": "skipped:tool-missing" if yaml_mod is None and seen["yaml"] else "ran" if seen["yaml"] else "no-files",
        "env": "ran" if seen["env"] else "no-files",
    }
    ordered = sorted(scanner.findings, key=lambda f: (-SEVERITY_RANK[f["severity"]], f["file"], f["rule"], f["location"]))
    findings, cut = ordered[:MAX_FINDINGS], ordered[MAX_FINDINGS:]
    dropped = dict(scanner.dropped)
    for finding in cut:
        dropped[finding["severity"]] += 1
    unlisted = sum(dropped.values())
    # a finding that did not fit in the list still counts: a blocker past the cap must not leave the status ok
    counts = {sev: sum(1 for f in findings if f["severity"] == sev) + dropped[sev] for sev in SEVERITY_RANK}
    failing = counts["major"] > 0 or counts["blocker"] > 0
    tail = f" ({unlisted} not listed)" if unlisted else ""
    return {
        "status": "fail" if failing else "ok",
        "files_scanned": sum(seen[k] for k in seen if checks[k] != "skipped:tool-missing"),
        "skipped": skipped,
        "checks": checks,
        "truncated": truncated_files or scanner.truncated or unlisted > 0,
        "counts": counts,
        "findings": findings,
        "summary": f"{sum(counts.values())} finding(s): {counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info{tail}",
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "files_scanned": 0, "skipped": {"too_large": 0, "binary": 0, "symlink": 0, "unparsed": 0},
            "checks": {"json": "no-files", "toml": "no-files", "yaml": "no-files", "env": "no-files"}, "truncated": False,
            "counts": {"info": 0, "minor": 0, "major": 0, "blocker": 0}, "findings": [],
            "summary": "dry-run: chain_config_check would scan --path under --root for chain and RPC configuration",
        }
    target = (ctx.root / args.path).resolve()
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {ctx.root}")
    return scan(ctx.root, target)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A28", "chain_config_check", run, description=__doc__, add_args=add_args).main())
