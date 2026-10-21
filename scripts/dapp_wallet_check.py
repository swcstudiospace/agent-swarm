#!/usr/bin/env python3
"""A29 - scan frontend sources for web3 hazards: eth_sign, unlimited approvals, embedded RPC keys, missing chain checks and unsafe signing prompts.

Scans JavaScript/TypeScript/Vue/Svelte/HTML sources line by line, `.env*` files by variable name and JSON/YAML
configuration by parsed key and value. Every finding is {id, severity, summary, location}; severities are
info|minor|major|blocker and `status` is "fail" when a major or blocker finding exists. Secret-looking values are
NEVER echoed: a finding names the location and the length of what it saw, not the value.

Read-only: it reads files under --root and writes nothing. Stdlib only (YAML structure checks need PyYAML and are
reported as `skipped:tool-missing` when it is absent). Bounded: files over 1 MiB are skipped, at most 2000 files are
examined, symlinks that leave the root are skipped and binaries are skipped. The rules are line heuristics, so a
finding is a prompt to look, not proof. `--dry-run` prints deterministic canned output and touches nothing.

`rpc_url_inspect` is shared with the A29 tools (swarm/tools/a29_tools.py) so the URL credential rules live once.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402

MAX_FILE_BYTES = 1 << 20
MAX_FILES = 2000
MAX_FINDINGS = 500  # listed findings; counts always cover all of them, so the JSON stays far below the 256 KiB result limit
BINARY_SNIFF_BYTES = 8192
SEVERITIES = ("info", "minor", "major", "blocker")
FAIL_SEVERITIES = ("major", "blocker")
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "dist", "build", ".next", ".nuxt", "coverage", "__pycache__", ".swarm",
                       ".turbo", ".cache", "out"})
CODE_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte", ".html", ".htm"})
JSON_EXTS = frozenset({".json"})
YAML_EXTS = frozenset({".yml", ".yaml"})
LOCKFILES = frozenset({"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock"})
PUBLIC_ENV_PREFIXES = ("NEXT_PUBLIC_", "VITE_", "REACT_APP_", "NUXT_PUBLIC_", "EXPO_PUBLIC_", "PUBLIC_", "GATSBY_")

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
QUERY_KEY_NAMES = frozenset({"apikey", "api_key", "api-key", "key", "token", "access_token", "auth", "secret", "project_id", "projectid"})
_KEY_SEGMENT = re.compile(r"[A-Za-z0-9]{16,}")
_KEY_SEGMENT_DASHED = re.compile(r"[A-Za-z0-9_-]{16,}")
_SLUG_PART = re.compile(r"[a-z]+|[0-9]{1,4}|v[0-9]{1,3}")
_UUID_SEGMENT = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_SEGMENT = re.compile(r"(?:0x)?[0-9a-fA-F]{32,}")
MIN_QUERY_SECRET_LEN = 8


def _segment_is_key(segment: str) -> bool:
    """A path segment that looks like an API key: one 16+ char alphanumeric token holding a digit and a letter, a 32+ char hex string, a
    UUID, or a dashed/underscored token (Alchemy keys use both) holding a digit and a letter. Slugs made only of lowercase words, short
    numbers and `v3`-style versions, such as `ethereum-mainnet-archive` or `polygon-mainnet-fullnode-2`, are not keys."""
    if _UUID_SEGMENT.fullmatch(segment) or _HEX_SEGMENT.fullmatch(segment):
        return True
    if _KEY_SEGMENT.fullmatch(segment):
        return any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment)
    if _KEY_SEGMENT_DASHED.fullmatch(segment):
        parts = [part for part in re.split(r"[-_]", segment) if part]
        if all(_SLUG_PART.fullmatch(part) for part in parts):
            return False
        return any(c.isdigit() for c in segment) and any(c.isalpha() for c in segment)
    return False


_INTEGER_HOST = re.compile(r"0x[0-9a-f]{1,8}|[0-9]{1,10}")


def _is_local_host(host: str) -> bool:
    """Loopback, private-network, link-local and unspecified addresses, `localhost`, `*.localhost`, `*.local` and single-label names:
    hosts a user's browser cannot reach on the public internet."""
    if host in LOOPBACK_HOSTS or host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if _INTEGER_HOST.fullmatch(host):
        # a one-number IPv4 host (`134744072` is 8.8.8.8): classify the address it denotes, never as a single-label name
        value = int(host, 16) if host.startswith("0x") else int(host)
        return value <= 0xFFFFFFFF and not ipaddress.IPv4Address(value).is_global
    return "." not in host


def rpc_url_inspect(url: str) -> dict:
    """Credential and transport hazards of one RPC/explorer URL, never revealing a secret value.

    Returns {valid, scheme, host, issues: [{code, detail}]} (host lower-cased, a trailing dot removed) with codes: malformed, scheme (not
    http/https/ws/wss), insecure-scheme (http/ws to a host that is not local), local-endpoint (loopback, private-network or single-label
    host), credentials (userinfo), path-key (key-looking path segment: detail names the segment index and length), query-key
    (secret-named query parameter with a value of 8+ chars: detail names the parameter and the value length).
    """
    result = {"valid": False, "scheme": "", "host": "", "issues": []}
    if any(c.isspace() or ord(c) < 32 for c in url):
        result["issues"].append({"code": "malformed", "detail": "URL contains whitespace or control characters"})
        return result
    try:
        parts = urlsplit(url)
        _ = parts.port  # raises ValueError for a bad port
    except ValueError:
        result["issues"].append({"code": "malformed", "detail": "URL does not parse"})
        return result
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if not scheme or not host:
        result["issues"].append({"code": "malformed", "detail": "URL needs a scheme and a host"})
        return result
    result.update(valid=True, scheme=scheme, host=host)
    issues = result["issues"]
    if scheme not in ("http", "https", "ws", "wss"):
        issues.append({"code": "scheme", "detail": f"scheme of {len(scheme)} chars is not http, https, ws or wss"})
    loopback = _is_local_host(host)
    if loopback:
        issues.append({"code": "local-endpoint", "detail": f"host {host} is a loopback, private-network or single-label address"})
    elif scheme in ("http", "ws"):
        issues.append({"code": "insecure-scheme", "detail": f"{scheme}:// sends traffic unencrypted; use {'https' if scheme == 'http' else 'wss'}://"})
    if parts.username is not None or parts.password is not None:
        length = len(parts.password or parts.username or "")
        issues.append({"code": "credentials", "detail": f"userinfo credentials embedded in the URL ({length} chars)"})
    for index, segment in enumerate(s for s in parts.path.split("/") if s):
        if _segment_is_key(segment):
            issues.append({"code": "path-key", "detail": f"path segment {index + 1} looks like an API key ({len(segment)} chars)"})
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        if name.lower() in QUERY_KEY_NAMES and len(value) >= MIN_QUERY_SECRET_LEN:
            issues.append({"code": "query-key", "detail": f"query parameter {name!r} carries a secret ({len(value)} chars)"})
    return result


_SCHEME_PREFIX = re.compile(r"[\x00-\x20]*([A-Za-z][A-Za-z0-9+.\-]*):")
_TAB_NEWLINE = re.compile(r"[\t\r\n]")


def url_scheme_userinfo(url: str) -> tuple[str, str]:
    """(scheme lower-cased, userinfo kind) of a URL string; the userinfo itself is never returned. Kind is `` (none), `plain`
    (credentials) or `host-like` (a dotted name before the @, the `trusted.example@evil.example` look-alike). Shared with the A29 tools,
    whose handlers may not import urllib."""
    try:
        parts = urlsplit(url)
    except ValueError:
        # urlsplit refuses some URLs (a `[` in the authority that is no IPv6 literal) that a browser still opens: read the scheme the way
        # a browser does (leading controls and spaces dropped, tabs and newlines removed), so a `javascript:` link cannot pass as malformed
        match = _SCHEME_PREFIX.match(_TAB_NEWLINE.sub("", url))
        return (match.group(1).lower() if match else ""), ""
    kind = ""
    if parts.username is not None or parts.password is not None:
        kind = "host-like" if "." in (parts.username or "") else "plain"
    return parts.scheme.lower(), kind


# ---- line rules ------------------------------------------------------------------------------------------------------

_COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*|<!--)")  # `#` is no comment in JS/TS/HTML: a `#privateKey` class field is code
_URL = re.compile(r"""(?:https?|wss?)://[^\s"'`<>)\]},;]+""")
_RPC_CONTEXT = re.compile(
    r"rpc|provider|transport|endpoint|infura|alchemy|quiknode|quicknode|ankr|chainstack|getblock|blastapi|drpc|llamarpc|nodereal|moralis"
    r"|wallet_addEthereumChain|JsonRpc", re.IGNORECASE)
_ETH_SIGN = re.compile(r"""["'`]eth_sign["'`]""")
_TYPED_V1 = re.compile(r"""["'`]eth_signTypedData["'`]""")
_APPROVE_CALL = re.compile(r"\b(approve|increaseAllowance)\b")
_MAX_UINT = re.compile(
    r"MaxUint256|MAX_UINT256|UINT256_MAX|maxUint256|MaxUint\b|type\(uint256\)\.max|uint256\.max|2n\s*\*\*\s*256n\s*-\s*1n"
    r"|2\s*\*\*\s*256\s*-\s*1|\(1\s*<<\s*256\)\s*-\s*1|0x[fF]{64}\b|(?<![0-9])115792089237316195423570985008687907853269984665640564039457584007913129639935(?![0-9])")
_APPROVE_ALL = re.compile(r"\bsetApprovalForAll\b")
# Key-named assignments. No leading \b (camelCase `deployerPrivateKey` counts); a trailing \b keeps `PRIVATE_KEY_URL` and `seedUrl` out.
_KEY_ASSIGNMENT = re.compile(
    r"""(private[_-]?key|priv[_-]?key|secret[_-]?key|mnemonic|seed[_-]?phrase|recovery[_-]?phrase|seed|secret)\b["'`]?\??\s*([:=])(?![=>])""",
    re.IGNORECASE)
_STRING_TOKEN = r"""(?:"[^"\n]*"|'[^'\n]*'|`[^`\n]*`)"""
_STRING_CHAIN = re.compile(rf"\s*{_STRING_TOKEN}(?:\s*\+\s*{_STRING_TOKEN})*")  # adjacent literals joined with +
_STRING_PART = re.compile(_STRING_TOKEN)
_VALUE_END = re.compile(r"\s*(?:[;,)}\]]|$|//|/\*)")  # what may follow a plain literal: a terminator or a comment, never `*16` or `+ x`
_ENV_VALUE = re.compile(r"\s*(?:process\.env\.|import\.meta\.env\.)\w")
_NULLISH_VALUE = re.compile(r"\s*(?:null|undefined|None|nil)\s*(?:[;,)}\]]|$|//)")
_TYPE_VALUE = re.compile(r"\s*(?:string|number|bigint|boolean|Hex|`0x\$\{string\}`)\s*(?:[;,}]|$)")  # `privateKey: string` in a type
_NON_SECRET_ENV_SUFFIX = re.compile(r"_(?:URL|URI|ENDPOINT|HOST)$", re.IGNORECASE)  # `REACT_APP_SEED_PHRASE_URL` names a URL, not a seed
_CLIENT_SIGNER = re.compile(
    r"\bnew\s+(?:ethers\.)?Wallet\s*\(|\bWallet\.fromMnemonic\b|\bfromPhrase\s*\(|\bprivateKeyToAccount\s*\(|\bmnemonicToAccount\s*\("
    r"|\bKeypair\.fromSecretKey\b")
# the `{0,300}` bounds keep every rule linear on a one-megabyte minified line (an unbounded `[^)]*` is quadratic there)
_KEY_STORAGE = re.compile(r"(?:localStorage|sessionStorage)\.setItem\s{0,20}\([^)]{0,300}(?:private|mnemonic|seed|secret)", re.IGNORECASE)
_KEY_LOG = re.compile(
    r"console\.(?:log|info|debug|warn|error)\s{0,20}\([^)]{0,300}(?:private[_-]?key|mnemonic|seed[_-]?phrase|secret[_-]?key)", re.IGNORECASE)
_BLIND_SIGN = re.compile(
    r"\bsignMessage\s*\(\s*(?:ethers\.)?(?:utils\.)?(?:arrayify|getBytes|toBeArray|keccak256|hexToBytes|toBytes|solidityKeccak256|hashMessage)\b"
    r"|\bsignMessage\s*\(\s*\{[^}]{0,300}\braw\s*:")
_WINDOW_ETHEREUM = re.compile(r"\bwindow\.ethereum\b")
_SEND = re.compile(
    r"\beth_sendTransaction\b|\bsendTransaction(?:Async)?\s*\(|\bwriteContract(?:Async)?\s*\(|\beth_signTypedData(?:_v[34])?\b"
    r"|\b_?signTypedData(?:Async)?\s*\(")
_CHAIN = re.compile(
    r"chainId|chain_id|\.chain\b|chain\.id|switchChain|wallet_switchEthereumChain|eth_chainId|getChainId|useChainId|useNetwork"
    r"|getNetwork|network\.chainId")
_SIMULATE = re.compile(
    r"simulateContract|callStatic|estimateGas|eth_estimateGas|eth_call\b|\.simulate\b|simulateTransaction|staticCall|simulateCalls|tenderly",
    re.IGNORECASE)
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_SECRET_ENV_NAME = re.compile(r"PRIVATE|SECRET|MNEMONIC|SEED", re.IGNORECASE)
_SECRET_KEY_NAME = re.compile(r"private[_-]?key|mnemonic|seed[_-]?phrase|secret[_-]?key|recovery[_-]?phrase", re.IGNORECASE)
_RPC_KEY_NAME = re.compile(r"rpc|provider|endpoint|url", re.IGNORECASE)
_ENV_REFERENCE = re.compile(r"^\s*(?:\$\{[^}]*\}|\$[A-Za-z_]\w*|process\.env\..*|env\(.*\))\s*$")


def _finding(fid: str, severity: str, summary: str, location: str) -> dict:
    return {"id": fid, "severity": severity, "summary": summary, "location": location}


def _url_findings(url: str, location: str, *, rpc_context: bool, public_env: bool = False) -> list[dict]:
    """Findings for one URL. Credentials are reported everywhere; keys and insecure transport only where the URL is an
    RPC endpoint (`rpc_context`), so image CDN paths and docs links do not read as RPC keys."""
    found = []
    info = rpc_url_inspect(url)
    for issue in info["issues"]:
        code, detail = issue["code"], issue["detail"]
        if code == "credentials":
            found.append(_finding("DAPP-RPC-CREDENTIALS", "blocker", f"URL with embedded credentials ships to every user: {detail}", location))
        elif code in ("path-key", "query-key") and rpc_context:
            where = "public client env variable" if public_env else "frontend source"
            found.append(_finding("DAPP-RPC-KEY", "major", f"RPC URL with an embedded API key in {where}: {detail}; proxy it server-side", location))
        elif code == "insecure-scheme" and rpc_context:
            found.append(_finding("DAPP-RPC-INSECURE", "major", f"RPC endpoint over cleartext transport: {detail}", location))
    return found


def _call_text(lines: list[str], index: int, start: int) -> str:
    """Text of the call whose name ends at lines[index][start:], continued over following lines until its parentheses close
    (at most 5 lines). Object-style calls without a parenthesis (viem `functionName: 'approve', args: [...]`) use the next 2 lines."""
    depth = 0
    chunks: list[str] = []
    for offset in range(5):
        if index + offset >= len(lines):
            break
        segment = lines[index + offset][start if offset == 0 else 0:]
        for position, char in enumerate(segment):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth <= 0:
                    chunks.append(segment[:position + 1])
                    return " ".join(chunks)
        chunks.append(segment)
        if offset == 0 and depth == 0:
            return " ".join([segment, *lines[index + 1:index + 3]])
    return " ".join(chunks)


def _key_material_findings(line: str, loc: str, reasons: list[str]) -> list[dict]:
    """DAPP-KEY-MATERIAL for each key-named assignment on a line whose value joins to a secret-length literal. Adjacent string literals
    joined with `+` are one literal (`"0x" + "ab12"` is 6 chars). A value that is not then a plain literal adds a scan-incomplete reason
    naming the file and line, never the value: env references, null-like keywords and TypeScript type names are exempt."""
    findings: list[dict] = []
    for match in _KEY_ASSIGNMENT.finditer(line):
        name, sep, at = match.group(1).lower(), match.group(2), match.end()
        chain = _STRING_CHAIN.match(line, at)
        if chain and _VALUE_END.match(line, chain.end()):
            joined = "".join(part.group(0)[1:-1] for part in _STRING_PART.finditer(chain.group(0)))
            if "${" not in joined:
                if "phrase" in name or "mnemonic" in name:
                    if len(joined) >= 20:
                        findings.append(_finding("DAPP-KEY-MATERIAL", "blocker", f"key material literal in source ({len(joined)} chars, value not shown)", loc))
                elif "key" in name and len(joined) >= 16 and not any(c.isspace() for c in joined):
                    findings.append(_finding("DAPP-KEY-MATERIAL", "blocker", f"key material literal in source ({len(joined)} chars, value not shown)", loc))
                continue
        if _ENV_VALUE.match(line, at) or _NULLISH_VALUE.match(line, at):
            continue
        if sep == ":" and _TYPE_VALUE.match(line, at):
            continue
        reasons.append(f"{loc}: key-named assignment is not a plain string literal (value not checked)")
    return findings


def _scan_code(rel: str, text: str, reasons: list[str]) -> list[dict]:
    findings: list[dict] = []
    lines = text.splitlines()
    first_send = 0
    has_chain = False
    has_simulation = False
    for number, line in enumerate(lines, start=1):
        if _COMMENT_LINE.match(line):
            continue
        loc = f"{rel}:{number}"
        if _ETH_SIGN.search(line):
            findings.append(_finding("DAPP-ETH-SIGN", "blocker", "eth_sign signs an arbitrary 32-byte hash blind; use personal_sign or eth_signTypedData_v4", loc))
        if _TYPED_V1.search(line):
            findings.append(_finding("DAPP-TYPED-DATA-LEGACY", "minor", "eth_signTypedData (v1) is the legacy unstructured format; use eth_signTypedData_v4", loc))
        approve = _APPROVE_CALL.search(line)
        if approve:
            if _MAX_UINT.search(_call_text(lines, number - 1, approve.end())):
                findings.append(_finding("DAPP-UNLIMITED-APPROVAL", "major",
                                         "unlimited token allowance (max uint256) requested; approve the exact amount or make unlimited an explicit opt-in", loc))
        if _APPROVE_ALL.search(line):
            findings.append(_finding("DAPP-APPROVAL-FOR-ALL", "minor",
                                     "setApprovalForAll grants control of a whole collection; show a dedicated confirmation naming operator and collection", loc))
        findings.extend(_key_material_findings(line, loc, reasons))  # every assignment on the line counts, not one per line
        if _CLIENT_SIGNER.search(line):
            findings.append(_finding("DAPP-CLIENT-SIGNER", "major", "a key-holding signer is constructed in frontend code; sign through the user's wallet instead", loc))
        if _KEY_STORAGE.search(line):
            findings.append(_finding("DAPP-KEY-STORAGE", "blocker", "key-looking data written to web storage", loc))
        if _KEY_LOG.search(line):
            findings.append(_finding("DAPP-KEY-LOG", "blocker", "key-looking identifier passed to console logging", loc))
        if _BLIND_SIGN.search(line):
            findings.append(_finding("DAPP-BLIND-SIGN", "major", "signMessage over raw bytes or a hash shows the user an opaque value (blind signing); sign readable text or typed data", loc))
        if _WINDOW_ETHEREUM.search(line):
            findings.append(_finding("DAPP-WINDOW-ETHEREUM", "info", "direct window.ethereum use; wallets collide there, discover providers with EIP-6963", loc))
        rpc_context = bool(_RPC_CONTEXT.search(line))
        for url in _URL.findall(line):
            findings.extend(_url_findings(url, loc, rpc_context=rpc_context or bool(_RPC_CONTEXT.search(url))))
        if not first_send and _SEND.search(line):
            first_send = number
        has_chain = has_chain or bool(_CHAIN.search(line))
        has_simulation = has_simulation or bool(_SIMULATE.search(line))
    if first_send and not has_chain:
        findings.append(_finding("DAPP-NO-CHAIN-CHECK", "major",
                                 "file sends transactions or signs typed data but never reads or enforces a chain id; verify the wallet chain before signing", f"{rel}:{first_send}"))
    if first_send and not has_simulation:
        findings.append(_finding("DAPP-NO-SIMULATION", "minor",
                                 "file sends transactions but never simulates or estimates them; simulate before asking for a signature", f"{rel}:{first_send}"))
    return findings


def _env_value(raw: str) -> str:
    """Value of a dotenv assignment: a quoted value ends at its closing quote, an unquoted one at the first ` #` (an inline comment)."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else raw[1:]
    comment = re.search(r"\s#", raw)
    return (raw[:comment.start()] if comment else raw).strip()


def _scan_env(rel: str, text: str) -> list[dict]:
    findings: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = _ENV_LINE.match(line)
        if not match:
            continue
        name, value = match.group(1), _env_value(match.group(2))
        public = name.upper().startswith(PUBLIC_ENV_PREFIXES)
        if not public or not value:
            continue
        loc = f"{rel}:{number}"
        if _SECRET_ENV_NAME.search(name) and not _NON_SECRET_ENV_SUFFIX.search(name):
            findings.append(_finding("DAPP-PUBLIC-SECRET-ENV", "blocker",
                                     f"{name} uses a client-exposed prefix and a secret-looking name ({len(value)} chars, value not shown)", loc))
        if re.match(r"(?:https?|wss?)://", value):
            findings.extend(_url_findings(value, loc, rpc_context=True, public_env=True))
    return findings


def _path_key(key) -> str:
    """A mapping key as shown in a finding path. A key that looks like a secret (a map keyed by API key or token) is shown by length only."""
    text = str(key)
    return f"<key {len(text)} chars>" if len(text) > 64 or _segment_is_key(text) else text


MAX_WALK_VISITS = 1_000_000  # values per file, containers and scalars; a file within the 1 MiB bound has fewer unless it aliases


class _WalkLimit(Exception):
    """A file has more value visits than MAX_WALK_VISITS (aliases shared across many keys)."""


def _order(finding: dict) -> tuple[str, str]:
    return (finding["location"], finding["id"])


class _Findings:
    """Findings of one scan: exact counts per severity, and only the MAX_FINDINGS most severe kept while collecting, so a file that
    matches on every value cannot grow the store without limit. Within a severity, findings are ordered by location and id."""

    def __init__(self) -> None:
        self.counts = {name: 0 for name in SEVERITIES}
        self._kept: dict[str, list[dict]] = {name: [] for name in SEVERITIES}

    def append(self, finding: dict) -> None:
        severity = finding["severity"]
        self.counts[severity] += 1
        kept = self._kept[severity]
        kept.append(finding)
        if len(kept) > 2 * MAX_FINDINGS:
            kept.sort(key=_order)
            del kept[MAX_FINDINGS:]

    def extend(self, findings) -> None:
        for finding in findings:
            self.append(finding)

    def listed(self) -> list[dict]:
        """The MAX_FINDINGS most severe findings: blocker first, then by location and id."""
        out: list[dict] = []
        for name in reversed(SEVERITIES):
            out.extend(sorted(self._kept[name], key=_order)[:MAX_FINDINGS - len(out)])
        return out


def _walk_structure(node, path: str, rel: str, findings: _Findings, ancestors: set[int], budget: list[int]) -> None:
    """Walk one parsed document. A YAML alias (`*anchor`) is the same object as its anchor, so a shared node is walked under every
    key that reaches it and each path is checked. Cycles are cut on the current path only (`ancestors`): a self-referencing anchor
    (`&x {again: *x}`) stops where it closes its own path. Every value visited, container or scalar, is charged to `budget`, the
    remaining visits of the file, so alias fan-out is bounded however much work each shared value causes."""
    budget[0] -= 1
    if budget[0] < 0:
        raise _WalkLimit()
    if isinstance(node, (dict, list)):
        if id(node) in ancestors:
            return
        ancestors.add(id(node))
        try:
            if isinstance(node, dict):
                for key, value in node.items():
                    _walk_structure(value, f"{path}.{_path_key(key)}" if path else _path_key(key), rel, findings, ancestors, budget)
            else:
                for index, value in enumerate(node):
                    _walk_structure(value, f"{path}[{index}]", rel, findings, ancestors, budget)
        finally:
            ancestors.discard(id(node))
    elif isinstance(node, str):
        leaf = path.rsplit(".", 1)[-1]
        if _SECRET_KEY_NAME.search(leaf) and node and not _ENV_REFERENCE.match(node):
            findings.append(_finding("DAPP-KEY-MATERIAL", "blocker", f"key {path!r} holds a secret-looking value ({len(node)} chars, value not shown)", rel))
        if re.match(r"(?:https?|wss?)://", node):
            findings.extend(_url_findings(node, f"{rel}#{path}", rpc_context=bool(_RPC_KEY_NAME.search(path))))


def _load_yaml():
    """PyYAML module or None. A function so tests can simulate its absence."""
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _scan_data(rel: str, text: str, suffix: str, yaml_module, findings: _Findings, skipped: list[str]) -> None:
    try:
        if suffix in JSON_EXTS:
            documents = [json.loads(text)]
        else:
            documents = list(yaml_module.safe_load_all(text))
    except Exception as exc:  # a config that does not parse is a finding, not a crash; its exception name is a label, never its message
        findings.append(_finding("DAPP-CONFIG-PARSE", "info", f"{type(exc).__name__}: file is not valid {suffix.lstrip('.')} and was not checked", rel))
        skipped.append(f"{rel}: skipped:unparsable {suffix.lstrip('.')} ({type(exc).__name__})")
        return
    budget = [MAX_WALK_VISITS]  # one budget for the whole file: many documents cannot multiply the work either
    for document in documents:
        try:
            _walk_structure(document, "", rel, findings, set(), budget)
        except (RecursionError, _WalkLimit):  # too deep or too aliased to walk completely: keep what was found, scan the other files
            findings.append(_finding("DAPP-CONFIG-PARSE", "info", "configuration is nested or aliased too deeply to check completely", rel))
            skipped.append(f"{rel}: only partly checked (nested or aliased too deeply)")
            break


# ---- traversal -------------------------------------------------------------------------------------------------------


def _candidate_files(base: Path):
    """Deterministic (sorted) walk below `base` that never descends into symlinked directories."""
    if base.is_file():
        yield base
        return
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _kind(path: Path) -> str:
    name = path.name
    if name in LOCKFILES or name.endswith(".d.ts"):
        return ""
    if name == ".env" or name.startswith(".env."):
        return "env"
    suffix = path.suffix.lower()
    if suffix in CODE_EXTS:
        return "code"
    if suffix in JSON_EXTS:
        return "json"
    if suffix in YAML_EXTS:
        return "yaml"
    return ""


def scan_tree(base: Path, root: Path) -> dict:
    """Scan `base` (a file or directory inside `root`). Returns the result body without `status`/`agent`/`script`. Every in-scope input that
    was not analysed in full is a label in `skipped` and a major `scan-incomplete` finding, so the status is `fail` (swarm/scan_status.py)."""
    findings = _Findings()
    skipped: list[str] = []  # one label and path per input not analysed in full: labels and paths only, never a value
    scanned = skipped_files = 0
    truncated = False
    yaml_module = _load_yaml()
    saw_yaml = False
    for path in _candidate_files(base):
        kind = _kind(path)
        if not kind:
            continue
        if scanned >= MAX_FILES:
            truncated = True
            skipped.append(f"scan stopped at the {MAX_FILES}-file limit; the remaining files were not scanned")
            break
        rel = path.relative_to(root).as_posix()
        try:
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                skipped_files += 1
                skipped.append(f"{rel}: skipped:symlink-outside-root")
                continue
            if not path.is_file():  # named pipes, sockets and devices are never opened: a read could block the whole scan
                skipped_files += 1
                skipped.append(f"{rel}: skipped:not-a-regular-file")
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped_files += 1
                skipped.append(f"{rel}: skipped:oversized")
                continue
            data = path.read_bytes()
        except OSError:
            skipped_files += 1
            skipped.append(f"{rel}: skipped:unreadable")
            continue
        if b"\0" in data[:BINARY_SNIFF_BYTES]:
            skipped_files += 1
            skipped.append(f"{rel}: skipped:binary")
            continue
        text = data.decode("utf-8", errors="replace")
        if kind == "yaml" and yaml_module is None:
            saw_yaml = True
            skipped_files += 1
            continue
        scanned += 1
        if kind == "code":
            findings.extend(_scan_code(rel, text, skipped))
        elif kind == "env":
            findings.extend(_scan_env(rel, text))
        else:
            _scan_data(rel, text, path.suffix.lower(), yaml_module, findings, skipped)
    if saw_yaml:
        skipped.append("yaml-config-structure: skipped:tool-missing (PyYAML is not installed)")
    findings.extend({**item, "location": "."} for item in incomplete_findings(skipped, scanned))
    counts = dict(findings.counts)
    failing = sum(counts[name] for name in FAIL_SEVERITIES)
    total = sum(counts.values())
    capped = total > MAX_FINDINGS
    if capped:
        skipped.append(f"findings: output capped at {MAX_FINDINGS} of {total} (counts are complete)")
    listed = findings.listed()
    return {
        "findings": listed,
        "counts": counts,
        "files_scanned": scanned,
        "files_skipped": skipped_files,
        "truncated": truncated or capped,
        "skipped": skipped,
        "summary": f"{scanned} file(s) scanned, {total} finding(s) ({failing} major or worse)"
                   + (f"; stopped at the {MAX_FILES}-file limit" if truncated else "")
                   + (f"; only the {MAX_FINDINGS} most severe are listed" if capped else ""),
        "status": scan_status(listed),
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "findings": [], "counts": {name: 0 for name in SEVERITIES}, "files_scanned": 0,
                "files_skipped": 0, "truncated": False, "skipped": [],
                "summary": "dry-run: dapp_wallet_check would scan frontend sources under --root/--path for eth_sign, unlimited approvals, "
                           "embedded RPC keys, missing chain checks and unsafe signing prompts"}
    root = ctx.root.resolve()
    target = (root / args.path).resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {root}")
    return scan_tree(target, root)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A29", "dapp_wallet_check", run, description=__doc__, add_args=add_args).main())
