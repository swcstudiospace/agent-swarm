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
A key-named list longer than the scan window (LIST_WINDOW_LINES lines) fails the scan; the remedy is to split or rename the list.
Residual: a lowercase value under a bare `seed` or `secret` name, with no digit or uppercase letter, is not key material in source or JSON,
as the A30 scanner treats a lowercase passphrase; key-kind names such as `privateKey` still flag it. Each key-named value is read through
at most LIST_WINDOW_LINES lines. SCAN_LINE_BUDGET (2,000,000) is the scan's work budget for those readers: one unit per character a
key-named value's reader consumes and one per window line it enters. Past it the rest of the scan is not analysed and the scan fails. A
single line costs at most its length in units, plus the window lines its last key name reads, since each key name reads only up to the next.

`rpc_url_inspect` is shared with the A29 tools (swarm/tools/a29_tools.py) so the URL credential rules live once.
"""
from __future__ import annotations

import bisect
import functools
import ipaddress
import json
import os
import re
import stat
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
MAX_SKIPPED_LISTED = 50  # skipped reasons listed in the result; the status counts all of them, and the rest is named by an exact remainder entry
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
        issues.append({"code": "local-endpoint", "detail": f"host of {len(host)} chars is a loopback, private-network or single-label address"})
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

# One line at a time, in order. A comment (`//`, `/* … */`, `<!-- … -->`) is blanked to spaces wherever it sits, so text inside it is never
# read as code and keeps its columns. A string or template literal is kept, and its span is recorded so that a key name inside it is not an
# occurrence. A block comment or template literal still open at the line's end carries to the next line as the state. `#` is no comment in
# JS/TS/HTML: a `#privateKey` class field is code.


def _blank(code: list[str], start: int, stop: int) -> None:
    for index in range(start, stop):
        code[index] = " "


def _close_index(line: str, start: int, quote: str) -> int:
    """Index of the next unescaped `quote` at or after `start`, or -1 when the line has none."""
    index = start
    while index < len(line):
        if line[index] == "\\":
            index += 2
            continue
        if line[index] == quote:
            return index
        index += 1
    return -1


_OPENER_PUNCT = frozenset("=([{,:;?!&|+-*/%<")  # a quote or backtick after one of these starts a literal; a bare `>` does not (a JSX tag closes with it)
_VALUE_KEYWORDS = frozenset({"return", "case", "typeof", "in", "of", "yield", "await", "throw"})  # and after one of these words
_MARKUP_SUFFIXES = (".html", ".htm", ".vue", ".svelte")  # the only files where `<!-- ... -->` is a comment


def _opens(prev: str) -> bool:
    """Whether a quote or backtick after the previous token starts a literal: at the start of the file it does, otherwise only after an
    operator or punctuation, the arrow `=>`, or one of `_VALUE_KEYWORDS`. After a word (`Don't`, `He said "hi`), or after a bare `>` (a JSX
    tag's close, `<p>"a</p>`), it is text and masks nothing."""
    return prev == "" or prev in _OPENER_PUNCT or prev == "=>" or prev in _VALUE_KEYWORDS


_URL_SCHEMES = frozenset({"http", "https", "ftp", "ftps", "file", "ws", "wss", "mailto", "data"})  # a `//` after one of these colons is a URL


def _url_scheme_before(line: str, colon: int) -> bool:
    """Whether the word before the colon at `colon` is a URL scheme, so that a `//` after it belongs to a URL (`https://`). Any other word
    (`c://`) makes the `//` a comment."""
    start = colon
    while start and line[start - 1].isalnum():
        start -= 1
    return line[start:colon].lower() in _URL_SCHEMES


def _regex_end(line: str, start: int, memo: dict[tuple[int, bool], int]) -> int:
    """Index of the slash that closes a regex literal whose body starts at `start`, or -1 when none closes on this line. The body ends at
    the first unescaped `/` outside a character class. `memo` holds, per (position, in-class) state, where the body closes from there, so
    the scan of a line is linear however many slashes start regex candidates."""
    visited: list[tuple[int, bool]] = []
    index, in_class, result = start, False, -1
    while index < len(line):
        state = (index, in_class)
        if state in memo:
            result = memo[state]
            break
        visited.append(state)
        char = line[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            result = index
            break
        index += 1
    for state in visited:
        memo[state] = result
    return result


def _scan_line(line: str, state: tuple[tuple, str], markup: bool) -> tuple[str, list[tuple[int, int]], tuple[tuple, str]]:
    """(code, strings, state) of one source line. `code` is the line with its comments blanked to spaces, `//` after a colon excepted (a
    URL); `strings` lists the (start, end) spans of string literals and of the text of template literals, in order; template substitutions
    `${...}` are code, with braces counted, and may hold strings and templates of their own. `state` carries over to the next line: the stack
    of open constructs (`*/` or `-->` for a block comment, "`" for template text, an int for a substitution's open braces) and the previous
    token, which decides whether a following quote opens a literal. `<!-- -->` is a comment only when `markup` is set."""
    stack, prev = list(state[0]), state[1]
    code = list(line)
    strings: list[tuple[int, int]] = []
    size = len(line)
    index = 0
    span = 0  # where the template text being read starts, while a template is open
    regex_memo: dict[tuple[int, bool], int] = {}  # per line: where a regex body closes from each (position, in-class) state
    while index < size:
        top = stack[-1] if stack else None
        if top == "`":  # template text: up to the closing backtick or a `${` substitution
            char = line[index]
            if char == "\\":
                index += 2
            elif char == "`":
                strings.append((span, index + 1))
                stack.pop()
                prev = "str"
                index += 1
            elif line.startswith("${", index):
                strings.append((span, index))
                stack.append(0)  # the substitution is code, its braces counted
                prev = "{"
                index += 2
            else:
                index += 1
            continue
        if top in ("*/", "-->"):
            end = line.find(top, index)
            stop = size if end < 0 else end + len(top)
            _blank(code, index, stop)
            if end < 0:
                break
            stack.pop()
            index = stop
            continue
        char = line[index]
        if char in " \t\r\n\f\v":
            index += 1
        elif line.startswith("//", index) and not (index and line[index - 1] == ":" and _url_scheme_before(line, index - 1)):  # `https://` is a URL
            _blank(code, index, size)
            break
        elif line.startswith("/*", index) or (markup and line.startswith("<!--", index)):
            closer = "*/" if line.startswith("/*", index) else "-->"
            end = line.find(closer, index + (2 if closer == "*/" else 4))
            stop = size if end < 0 else end + len(closer)
            _blank(code, index, stop)
            if end < 0:
                stack.append(closer)
                break
            index = stop
        elif char.isalnum() or char in "_$":
            end = index + 1
            while end < size and (line[end].isalnum() or line[end] in "_$"):
                end += 1
            prev = line[index:end]
            index = end
        elif char in "\"'":
            end = _close_index(line, index + 1, char) if _opens(prev) else -1
            if end < 0:  # text: an apostrophe in prose, or a quote that opens no literal, masks nothing and the rest is code
                prev = char
                index += 1
            else:
                strings.append((index, end + 1))
                prev = "str"
                index = end + 1
        elif char == "`":
            if _opens(prev):
                stack.append("`")
                span = index + 1
            prev = char
            index += 1
        elif char == "{":
            if top is not None:  # inside a substitution: one more open brace
                stack[-1] += 1
            prev = char
            index += 1
        elif char == "}":
            if top is not None and stack[-1] == 0:  # the substitution closes: the template text resumes
                stack.pop()
                span = index + 1
            elif top is not None:
                stack[-1] -= 1
            prev = char
            index += 1
        else:
            if char == "/" and not (index and line[index - 1] == "<") and _opens(prev):  # a regex literal, when its body closes on this line; `</p>` is a closing tag
                end = _regex_end(line, index + 1, regex_memo)
                if end >= 0:
                    stop = end + 1
                    while stop < size and line[stop].isalpha():  # the flags
                        stop += 1
                    strings.append((index, stop))
                    prev = "str"
                    index = stop
                    continue
            if char == ">" and index and line[index - 1] == "=":
                prev = "=>"  # the arrow is one token; a bare `>` is not an opener
            elif char in "+-" and index and line[index - 1] == char:
                prev = char * 2  # `++` and `--` end an operand, so a `/` after them divides
            else:
                prev = char
            index += 1
    if stack and stack[-1] == "`":  # a template open at the line's end runs on to the next line
        strings.append((span, size))
    return "".join(code), strings, (tuple(stack), prev)


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
# Key names, shared by the source assignments and the JSON walk. After a keyword a key name may carry an optional plural `s`, digits, a
# separator `_`, or a `Key` word (`privateKeys`, `seedKey`, `seed_key`). A locator or collection word is never key material (`PRIVATE_KEY_URL`,
# `seedUrls`, `secretName`, `privateKeyPath`).
_KEY_VALUE_WORD = r"(?:hex|bytes|raw|base64|b64|value|string|str|data)"  # a value word after a key word, as in privateKeyHex or secretBytes
_KEY_WORD = re.compile(
    r"(?:private[_-]?key|priv[_-]?key|secret[_-]?key|mnemonic|seed[_-]?phrase|recovery[_-]?phrase|seed|secret)"
    rf"(?=s?\d*+(?:[_.\-]|$)|key(?:s)?\d*+(?:[_.\-]|$)|s?{_KEY_VALUE_WORD}s?\d*+(?:[_.\-]|$))")
_KEY_ASSIGNMENT = re.compile(r"""(?:["'`]([\w.\-]+)["'`]|\b(\w+)["'`]?)\??\s*([:=])(?![=>])""")  # a quoted key whole (dots, hyphens in), or a bare identifier
_KEY_NAME_END = re.compile(r"\b(\w+)\??\s*$")  # a bare identifier that ends its line: its separator may start a following line
_LOCATOR_NAME = re.compile(r"(?:url|uri|endpoint|host|path|name|nodes?|list|file)s?$")  # on a lower-cased word
LIST_WINDOW_LINES = 200  # how far a key-named value (list, annotation or expression) is read past its line; one not closed by then gives a reason
MAX_LIST_DEPTH = 8  # how deeply a key-named list nested in a list is read; deeper gives a reason
SCAN_LINE_BUDGET = 2_000_000  # work units for the key-named readers across a whole scan: one per character a reader consumes and one per window line it enters; once spent, the rest of the scan is not analysed and the scan fails


class _BudgetSpent(Exception):
    """The scan's work budget ran out while a key-named value was being read, or a key line was next."""


class _LineBudget:
    """Work units left for the whole scan (see SCAN_LINE_BUDGET). `charge` takes units, or raises _BudgetSpent when fewer are left; the
    budget then stays spent, and the scan analyses no more key-named values."""

    def __init__(self, lines: int) -> None:
        self.remaining = lines
        self.spent = False

    def charge(self, units: int = 1) -> None:
        if self.remaining < units:
            self.remaining = 0
            self.spent = True
            raise _BudgetSpent()
        self.remaining -= units


_TYPE_EXTRA = frozenset("_$.<>[]|&, \t\r\n")  # characters a type may hold besides letters and digits
_ASSERTION_KEYWORD = re.compile(r"\s(?:as|satisfies)\s")  # one whitespace, the keyword, one whitespace: a candidate `as T` or `satisfies T`
_QUOTES = frozenset("\"'`")
_VALUE_OPENERS = frozenset("\"'`[")  # a value starting with one of these is read directly, not as a type annotation
_CONTINUE_START = frozenset("+-*/%|&?.")  # a line starting with one of these continues the expression above it
_OPERATOR_END = frozenset("+-*/%|&?=<:")  # a line ending with one of these continues the expression below it; a `>` does so only as an arrow
_OPEN_AT_END = frozenset("+-*/%|&?=<")  # a binary operator ending the file keeps a value open; `>` (a JSX close, an arrow's tail) does not
_CAST_ONE = r"<[A-Za-z_$][\w$.]*(?:<[\w$.\[\]|&, ]*>)?>"  # one cast in value position: `<Hex>`, `<Array<Hex>>`, `<Map<string, string>>`
_CAST = re.compile(rf"{_CAST_ONE}(?:\s*{_CAST_ONE})*")  # one or more casts, separated by whitespace (blank lines included): `<Hex> <Hex>`
_VALUE_START = frozenset("\"'`(<")  # a line starting with one of these, after a bare `>` at a line break, starts the value's next operand (a cast, a literal, a call)
_WORD = re.compile(r"[A-Za-z_$][\w$]*")  # the first word of the next line, for the statement-keyword test
_STATEMENT_WORDS = frozenset({"const", "var", "return", "if", "else", "for", "while", "do", "switch", "throw", "try", "export",
                              "function", "class", "interface", "enum", "break", "continue"})  # words that begin a statement, so they cannot begin an operand; contextual words (type, import, ...) can


def _starts_angle(code: list[str]) -> bool:
    """Whether the value read so far begins with `<`: its first piece that is not blank."""
    return next((piece for piece in code if piece.strip()), "").lstrip().startswith("<")

_CODE_RUN = re.compile(r"[^\"'`/()\[\]{}<>;,=\s]+")  # ordinary code characters, read in one step
_LIST_RUN = re.compile(r"[^\"'`/()\[\]{},\n]+")  # list text between the characters that matter to the list reader
_BLOCK_RUN = re.compile(r"[^*\n]+")  # text inside a block comment
_SCALAR_REASON = "key-named assignment is not a plain string literal (value not checked)"
_ELEMENT_REASON = "key-named list has an element that is not a plain string literal (value not checked)"
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


def _keyword_kind(keyword: str) -> str:
    """The length rule a key keyword takes: `phrase` (mnemonic, seed phrase, recovery phrase), `key` (private key, secret key) or `bare`
    (`seed` and `secret` alone)."""
    if "phrase" in keyword or "mnemonic" in keyword:
        return "phrase"
    if "key" in keyword:
        return "key"
    return "bare"


def _name_kinds(word: str) -> frozenset[str]:
    """The length-rule kinds of one identifier or key word. A key name counts when a keyword ends the word, optionally followed by digits,
    or is followed by a separator (`_`), or by a value word (`privateKeyHex`): `deployerPrivateKey2026`, `MY_PRIVATE_KEY`, `seedPhrase` and
    `privateKeyHex` count, `privateKeyPath` and `seedNodes` do not. A locator or collection word (url, uri, endpoint, host, path, name, nodes,
    list, file) is never key material. A mnemonic is key material under any key-kind name (privateKey, secretKey, privateKeys) and under a
    name with a seed, mnemonic or phrase word, so those names also get the `mnemonic` kind, which alone admits a spaced BIP-39 mnemonic. A
    bare `secret` gets no mnemonic kind, so a sentence under it stays excluded."""
    lower = word.lower()
    if _LOCATOR_NAME.search(lower):
        return frozenset()
    kinds: set[str] = set()
    for match in _KEY_WORD.finditer(lower):
        keyword = match.group(0)
        kind = _keyword_kind(keyword)
        kinds.add(kind)
        if kind != "bare" or "seed" in keyword:
            kinds.add("mnemonic")
    return frozenset(kinds)


_MNEMONIC = re.compile(r"[a-z]+(?: [a-z]+)*")  # lowercase words separated by single spaces
MNEMONIC_LENGTHS = frozenset({12, 15, 18, 21, 24})  # the word counts of a BIP-39 mnemonic


def _key_length_ok(kind: str, joined: str) -> bool:
    """Whether a plain literal under a key name of this kind is key material. A value containing `://` is a locator, never key material.
    A value with whitespace is key material only when it is exactly a BIP-39 mnemonic shape (12, 15, 18, 21 or 24 lowercase words separated
    by single spaces) under a name that carries a seed, mnemonic or phrase word; any other spaced value, such as a sentence under `secret`,
    is not. Without whitespace a phrase needs 20 characters and a key 16; a bare `seed` or `secret` needs 16 characters and a digit or an
    uppercase letter, so a lowercase slug such as `your-private-key-here-placeholder` under a bare name is not key material, while under a
    key-kind name it stays a blocker (RG2). Hex keys and mixed-case keys match."""
    if "://" in joined:
        return False
    if any(c.isspace() for c in joined):
        return kind in ("phrase", "mnemonic") and _MNEMONIC.fullmatch(joined) is not None and len(joined.split(" ")) in MNEMONIC_LENGTHS
    if kind == "mnemonic":  # the seed-word kind admits the mnemonic shape only; the base kind of the same name decides the rest
        return False
    if len(joined) < (20 if kind == "phrase" else 16):
        return False
    if kind == "bare":
        return any(c.isdigit() or c.isupper() for c in joined)
    return True


def _string_end(text: str, start: int) -> int:
    """Index just past the string literal opening at `start`, or the end of the text when it is unclosed."""
    quote, index = text[start], start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    return len(text)


_JOIN_OPERATORS = frozenset("+*")  # operators that join string fragments into one value: a concatenation or a repetition


def _tokens(text: str) -> list[tuple[str, str, bool]]:
    """The tokens of one value's text, whitespace dropped, as (kind, text, standalone). `str` is a string literal, `word` an identifier or a
    number, `open` and `close` a bracket, `op` any other character. A string is standalone when no enclosing bracket is a call's, an array's
    or an object's, so a grouping parenthesis keeps the literal standalone and a call's argument does not. An unclosed string is never
    standalone: nothing can be read from it."""
    tokens: list[tuple[str, str, bool]] = []
    stack: list[bool] = []  # per open bracket: True for a grouping parenthesis
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char in _QUOTES:
            end = _string_end(text, index)
            literal = text[index:end]
            closed = len(literal) >= 2 and literal[-1] == char
            tokens.append(("str", literal, closed and all(stack)))
            index = end
        elif char.isalnum() or char in "_$":
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            tokens.append(("word", text[index:end], False))
            index = end
        elif char in "([{":
            previous = tokens[-1] if tokens else None
            grouping = char == "(" and (previous is None or previous[0] in ("op", "open"))
            stack.append(grouping)
            tokens.append(("open", char, False))
            index += 1
        elif char in ")]}":
            if stack:
                stack.pop()
            tokens.append(("close", char, False))
            index += 1
        else:
            tokens.append(("op", char, False))
            index += 1
    return tokens


def _judge(code: str, kinds: frozenset[str], loc: str, findings: list[dict], reasons: list[str], reason: str) -> None:
    """The rule for one value's complete text, comments removed and string literals whole. Every standalone string literal is a value of
    its own, wherever it sits: as a fallback (`"0x…" || process.env.PK`), in grouping parentheses, after a cast, before an operator or a
    member call (`"0x…".slice(2)`). A run of literals joined by `+` is one value. A run joined to a non-literal part (`"0x" + getKey()`,
    `getKey() + "api-"`) gives the reason, and so does a literal followed by junk (`"abc" junk`); a member call or an operator after a
    literal does not. A run of key length is key material, a run with a `${` template gives the reason, and a placeholder is a reference.
    Strings inside a call's arguments, an array or an object are not read here."""
    text = _strip_type_assertion(code.strip())
    tokens = _tokens(text)
    index = 0
    while index < len(tokens):
        token_kind, value, standalone = tokens[index]
        if token_kind != "str" or not standalone:
            index += 1
            continue
        run = [value]
        end = index + 1
        while end + 1 < len(tokens) and tokens[end] == ("op", "+", False) and tokens[end + 1][0] == "str" and tokens[end + 1][2]:
            run.append(tokens[end + 1][1])
            end += 2
        before = tokens[index - 1] if index else None
        after = tokens[end] if end < len(tokens) else None
        joins = any(token is not None and token[0] == "op" and token[1] in _JOIN_OPERATORS for token in (before, after))
        if joins:  # a fragment joined to a non-literal part: the expression is not one literal
            reasons.append(f"{loc}: {reason}")
        elif any(item.startswith("`") and "${" in item for item in run):
            reasons.append(f"{loc}: {reason}")
        else:
            joined = "".join(item[1:-1] for item in run)
            if not _ENV_REFERENCE.match(joined) and any(_key_length_ok(kind, joined) for kind in kinds):
                findings.append(_finding("DAPP-KEY-MATERIAL", "blocker", f"key material literal in source ({len(joined)} chars, value not shown)", loc))
            if after is not None and after[0] in ("word", "str"):  # junk after the literal: the expression is not understood
                reasons.append(f"{loc}: {reason}")
        index = end


def _check_list(items: list[str], kinds: frozenset[str], loc: str, findings: list[dict], reasons: list[str], depth: int,
                budget: _LineBudget) -> None:
    """The rule for each element of a key-named list: a nested list is read as a list of its own, up to MAX_LIST_DEPTH, and every other
    element is judged as a value with the element reason."""
    for item in items:
        text = _strip_type_assertion(item.strip())
        if text.startswith("["):
            if depth + 1 > MAX_LIST_DEPTH:
                reasons.append(f"{loc}: key-named list is nested too deeply (value not checked)")
                continue
            nested = _read_list(_Cursor(text, [], 0, 0, at_file_end=True, budget=budget))
            if nested is None:
                reasons.append(f"{loc}: key-named list has a nested list that is not closed (value not checked)")
            else:
                _check_list(nested, kinds, loc, findings, reasons, depth + 1, budget)
            continue
        _judge(text, kinds, loc, findings, reasons, _ELEMENT_REASON)


class _Cursor:
    """A reader's view of a window of source lines: row 0 is `head` (the raw key line, whose comments the reader skips) and row k is
    lines[index + k], read in place, so no copy of the window is made. A line break is one character. `peek` looks ahead without consuming,
    `take` consumes, and `take_run` consumes a run of ordinary characters from the current row in one step. Every character consumed is
    charged to the scan's budget, and entering a row costs one more unit. `stop` is a column on row 0 where the value ends (the next key name
    on that line): the cursor reads nothing from there on. `at_file_end` says whether the window reaches the end of the file."""

    def __init__(self, head: str, lines: list[str], index: int, end: int, at_file_end: bool, col: int = 0,
                 budget: _LineBudget | None = None, stop: int | None = None) -> None:
        self._head = head
        self._lines = lines
        self._index = index
        self._rows = 1 + max(0, end - index - 1)
        self._row = 0
        self._col = col
        self._stop = stop
        self.at_file_end = at_file_end
        self._budget = budget

    def _line(self, row: int) -> str:
        return self._head if row == 0 else self._lines[self._index + row]

    def _end(self, row: int) -> int:
        """The column where the readable text of a row ends: the stop on row 0 when there is one, else the end of the row."""
        return self._stop if row == 0 and self._stop is not None else len(self._line(row))

    def _blocked(self, row: int, col: int) -> bool:
        return row >= self._rows or (row == 0 and self._stop is not None and col >= self._stop)

    def _char(self, row: int, col: int) -> str:
        if self._blocked(row, col):
            return ""
        return self._line(row)[col] if col < len(self._line(row)) else "\n"

    def _next(self, row: int, col: int) -> tuple[int, int]:
        if col < self._end(row):
            return row, col + 1
        return row + 1, 0

    def at_stop(self) -> bool:
        return self._row == 0 and self._stop is not None and self._col >= self._stop

    def peek(self, offset: int = 0) -> str:
        row, col = self._row, self._col
        for _ in range(offset):
            if self._blocked(row, col):
                return ""
            row, col = self._next(row, col)
        return self._char(row, col)

    def upcoming(self, limit: int = 40) -> str:
        """The text of the cursor's row from the cursor, at most `limit` characters, read without consuming: a look-ahead for a first word."""
        if self._blocked(self._row, self._col):
            return ""
        return self._line(self._row)[self._col:min(self._end(self._row), self._col + limit)]

    def take(self) -> str:
        char = self.peek()
        if char:
            row = self._row
            self._row, self._col = self._next(self._row, self._col)
            if self._budget is not None:
                self._budget.charge(2 if self._row != row else 1)
        return char

    def take_run(self, pattern: re.Pattern) -> str:
        if self._blocked(self._row, self._col):
            return ""
        match = pattern.match(self._line(self._row), self._col, self._end(self._row))
        if match is None:
            return ""
        self._col = match.end()
        if self._budget is not None:
            self._budget.charge(len(match.group()))
        return match.group()

    def skip_line(self) -> None:
        if self._blocked(self._row, self._col):
            return
        end = self._end(self._row)
        if self._budget is not None and end > self._col:
            self._budget.charge(end - self._col)
        self._col = end


def _window(first: str, start: int, lines: list[str], index: int, budget: _LineBudget, stop: int | None) -> _Cursor:
    """A cursor over the key line from `start` (`first`, the raw line, whose comments the reader skips) and the following lines, LIST_WINDOW_LINES at most,
    stopping at column `stop` of the key line when there is one. Nothing is copied: the following lines are read in place."""
    end = min(len(lines), index + 1 + LIST_WINDOW_LINES)
    return _Cursor(first, lines, index, end, at_file_end=end == len(lines), col=start, budget=budget, stop=stop)


def _gave_up(loc: str, what: str) -> str:
    """The reason for a value the reader could not close inside the window. It is always reported, never silent."""
    return f"{loc}: {what} within {LIST_WINDOW_LINES} lines (value not checked)"


def _take_string(cur: _Cursor) -> str | None:
    """The string literal at the cursor, quotes included. An ordinary string ends at a line break, as JavaScript requires; a template
    literal may span lines. None when the window ends inside it."""
    quote = cur.take()
    text = [quote]
    while True:
        char = cur.peek()
        if char == "":  # a string cut by the next key name on its line ends there; it is judged as the text it has
            return "".join(text) if cur.at_stop() else None
        if char == "\n" and quote != "`":
            return "".join(text)
        cur.take()
        text.append(char)
        if char == "\\":
            escaped = cur.take()
            if escaped == "":
                return "".join(text) if cur.at_stop() else None
            text.append(escaped)
        elif char == quote:
            return "".join(text)


def _skip_block(cur: _Cursor) -> bool:
    """Consume a block comment whose `/*` is next. False when the window ends before it closes."""
    cur.take()
    cur.take()
    while True:
        if cur.take_run(_BLOCK_RUN):
            continue
        char = cur.take()
        if char == "":
            return False
        if char == "*" and cur.peek() == "/":
            cur.take()
            return True


def _skip_trivia(cur: _Cursor) -> bool:
    """Skip whitespace, line comments and complete block comments. False when a block comment is still open at the window's end."""
    while True:
        char = cur.peek()
        if char and char in " \t\r\f\v\n":
            cur.take()
        elif char == "/" and cur.peek(1) == "/":
            cur.skip_line()
        elif char == "/" and cur.peek(1) == "*":
            if not _skip_block(cur):
                return False
        else:
            return True


def _read_code(cur: _Cursor, annotated: bool) -> tuple[str, str]:
    """Read one expression in a single pass across the window's lines, or, when `annotated`, a TypeScript annotation up to its `=`. Returns
    (status, code): `end` with the code read, at a terminator or at a line break where the expression goes no further; `assign` when an `=`
    closes an annotation; or `open` when the window ends first. Comments are dropped and string literals are read whole. A line break ends
    the expression only at depth zero, when the line does not end with an operator and the next line does not start with one."""
    code: list[str] = []
    depth = 0
    last = ""
    arrow_end = False  # the last token is an arrow `=>`: its `>` continues the expression over a line break; a bare `>` or a `/>` ends it
    while True:
        char = cur.peek()
        if char == "":
            if cur.at_stop():  # the next key name on the line starts here: the expression ends
                return "end", "".join(code)
            if cur.at_file_end and depth == 0 and last not in _OPEN_AT_END:
                return "end", "".join(code)
            return "open", ""
        run = cur.take_run(_CODE_RUN)
        if run:
            code.append(run)
            last = run[-1]
        elif char in " \t\r\f\v":
            cur.take()
            code.append(" ")
        elif char == "\n":
            cur.take()
            if depth > 0 or last in _OPERATOR_END or (last == ">" and arrow_end):
                code.append(" ")
                continue
            if not _skip_trivia(cur):
                return "open", ""
            following = cur.peek()
            if not annotated and last == ">" and not arrow_end and following != "" and _starts_angle(code):  # a bare `>` ends an angle-bracket value, unless the next line continues it
                text = "".join(code).strip()
                if following in _VALUE_START:  # a quote, backtick, `(` or `<` operand
                    if _CAST.fullmatch(text):
                        code.append(" ")  # the cast continues over the break: the literal, call or next cast on the next line is judged with the value
                        continue
                    return "ambiguous", ""  # another angle-bracket value: its shape is not read, so it is reported, never silently ended
                word = _WORD.match(cur.upcoming())
                if following not in ";})]" and not (word is not None and word.group() in _STATEMENT_WORDS):  # any other operand, or a contextual word, continues the value: reported
                    return "ambiguous", ""
            if following == "":
                return ("end", "".join(code)) if cur.at_file_end else ("open", "")
            if following in _CONTINUE_START or (annotated and following == "="):
                code.append(" ")
                continue
            return "end", "".join(code)
        elif char in _QUOTES:
            literal = _take_string(cur)
            if literal is None:
                return "open", ""
            code.append(literal)
            last = literal[-1]
        elif char == "/" and cur.peek(1) == "/":
            cur.skip_line()
        elif char == "/" and cur.peek(1) == "*":
            if not _skip_block(cur):
                return "open", ""
            code.append(" ")
        elif depth == 0 and char in ";,)}]":
            return "end", "".join(code)
        elif annotated and char == "=":
            cur.take()
            following = cur.peek()
            if following in ("=", ">"):
                cur.take()
                code.append("=" + following)
                last = following
                arrow_end = following == ">"
            elif depth == 0:
                return "assign", ""
            else:
                code.append("=")
                last = "="
        else:
            cur.take()
            if char in "([{" or (annotated and char == "<"):
                depth += 1
            elif char in ")]}" or (annotated and char == ">" and depth > 0):
                depth -= 1
            arrow_end = char == ">" and bool(code) and code[-1] == "="  # an arrow's `>` sits directly after its `=`
            code.append(char)
            last = char


def _read_list(cur: _Cursor) -> list[str] | None:
    """The element texts of the list whose `[` is next, split at its own top-level commas. Comments are dropped, string literals are read
    whole and nested brackets stay inside their element. None when the window ends before the list closes."""
    cur.take()  # the opening bracket
    items: list[str] = []
    piece: list[str] = []
    depth = 0
    while True:
        run = cur.take_run(_LIST_RUN)
        if run:
            piece.append(run)
            continue
        char = cur.peek()
        if char == "":
            return None
        if char in _QUOTES:
            literal = _take_string(cur)
            if literal is None:
                return None
            piece.append(literal)
        elif char == "/" and cur.peek(1) == "/":
            cur.skip_line()
        elif char == "/" and cur.peek(1) == "*":
            if not _skip_block(cur):
                return None
            piece.append(" ")
        else:
            cur.take()
            if char in "([{":
                depth += 1
            elif char in ")}":
                depth = max(0, depth - 1)
            elif char == "]" and depth == 0:
                items.append("".join(piece))
                return [item for item in items if item.strip()]
            elif char == "]":
                depth -= 1
            elif char == "," and depth == 0:
                items.append("".join(piece))
                piece = []
                continue
            piece.append(char)


def _strip_type_assertion(text: str) -> str:
    """`text` without a trailing `as T`, `as const` or `satisfies T`, in one linear pass: the run of characters a type may hold is walked
    back from the end once, and the keyword is searched only inside that run, so no pattern backtracks."""
    start = len(text)
    while start and (text[start - 1].isalnum() or text[start - 1] in _TYPE_EXTRA):
        start -= 1
    match = _ASSERTION_KEYWORD.search(text, start)
    if match is None or match.end() >= len(text):
        return text
    return text[:match.start()].rstrip()


_MARKUP = re.compile(r"<[A-Za-z/>]")  # markup on the line: a tag, a closing tag or a fragment (`<>`)
_QUOTED_VALUE = re.compile(r"""\s*(["'`])([^"'`\\]*)\1""")  # the quoted literal after a key name's separator; its content is group 2


def _key_shaped_value_follows(line: str, position: int, kinds: frozenset[str]) -> bool:
    """Whether the text at `position`, just after a key name's separator, is a quoted literal of key length under one of `kinds`. Only the
    shape is read; the value is never echoed."""
    literal = _QUOTED_VALUE.match(line, position)
    return literal is not None and any(_key_length_ok(kind, literal.group(2)) for kind in kinds)


def _key_occurrences(line: str, strings: list[tuple[int, int]]) -> tuple[list[tuple[int, str, int, str | None]], bool]:
    """(occurrences, suspect). The occurrences are the key-named ones on one line in order of position: (name start, name, value start,
    separator). Only key names count, so only they end a value, and a key name inside a string or template literal is not an occurrence. A
    name followed by its separator on this line carries it. A name that ends the line carries None: its separator may start a following
    line (`secret` then `= "0x…"`). Each value ends where the next occurrence starts, so one pass over the line reads each character at most
    once. The literal spans are sorted once per line and a position is looked up by bisection, so the cost is linear in the line's matches.
    `suspect` is True when a key name inside a literal is followed by its separator and a quoted key-shaped value, and the line holds
    markup (`<` then a letter, `/` or `>`): a stray quote in prose or JSX text can have paired with the code's opening quote, so the literal
    hide a real assignment. The literal stays masked; the caller reports it."""
    spans = sorted(strings)
    starts = [start for start, _ in spans]

    def literal_at(position: int, own: int | None = None) -> tuple[int, int] | None:
        """The literal of the line that contains `position`, other than the one that starts at `own` (a quoted key is the literal it names,
        so its own span does not mask it), or None."""
        at = bisect.bisect_right(starts, position) - 1
        if at >= 0 and position < spans[at][1] and spans[at][0] != own:
            return spans[at]
        return None

    found: list[tuple[int, str, int, str | None]] = []
    suspect = False
    markup = _MARKUP.search(line) is not None  # a property of the line, so it is found once here, not once per occurrence
    for match in _KEY_ASSIGNMENT.finditer(line):
        quoted = match.group(1) is not None
        name = match.group(1) or match.group(2)
        start = match.start(1) if quoted else match.start(2)
        kinds = _name_kinds(name)
        if not kinds:
            continue
        literal = literal_at(start, match.start() if quoted else None)
        if literal is not None:  # a stray quote in prose or JSX text can pair with the code's opening quote: markup on the line makes it suspect
            suspect = suspect or (markup and _key_shaped_value_follows(line, match.end(), kinds))
        else:
            found.append((start, name, match.end(), match.group(3)))
    end = _KEY_NAME_END.search(line)
    if end is not None and _name_kinds(end.group(1)) and literal_at(end.start(1)) is None:
        found.append((end.start(1), end.group(1), len(line), None))
    return sorted(found, key=lambda occurrence: occurrence[0]), suspect


def _key_material_findings(line: str, raw: str, strings: list[tuple[int, int]], loc: str, reasons: list[str], lines: list[str], index: int,
                           budget: _LineBudget, findings: list[dict]) -> None:
    """DAPP-KEY-MATERIAL for each key-named assignment whose value holds a complete string literal of key length (see `_judge` and
    `_key_length_ok`). The line is read once for all its occurrences (`_key_occurrences`); a value reads through the following lines,
    LIST_WINDOW_LINES at most, and stops where the next key name on its line starts. A separator may start a following line. A
    key-named list is read to its closing bracket and each element gets the same rule. Anything the window does not close gives a reason
    naming the file and line, never the value. Every character read and every window line entered is charged to the scan's budget."""
    occurrences, suspect = _key_occurrences(line, strings)
    if suspect:  # a key assignment may sit inside a literal that a stray quote opened: the line is not analysed, and no value is shown
        reasons.append(f"{loc}: possible key assignment inside a quoted span at line {index + 1}: not analysed")
    for position, (_, name, value, sep) in enumerate(occurrences):
        kinds = _name_kinds(name)
        if not kinds:
            continue
        if budget.spent:  # an earlier key line used the scan's last budget: this key line is not analysed
            raise _BudgetSpent()
        stop = occurrences[position + 1][0] if position + 1 < len(occurrences) else None
        cur = _window(raw, value, lines, index, budget, stop)  # the raw line: the reader reads its comments, the same length as the code
        if sep is None:  # the separator may start a following line of the window
            if not _skip_trivia(cur):
                reasons.append(_gave_up(loc, "key-named assignment's value follows a block comment that is not closed"))
                continue
            following = cur.peek()
            if following == "" and not cur.at_file_end:
                reasons.append(_gave_up(loc, "key-named name's separator is not found"))
                continue
            if following not in (":", "=") or cur.peek(1) in ("=", ">"):
                continue
            cur.take()
            sep = following
        if not _skip_trivia(cur):
            reasons.append(_gave_up(loc, "key-named assignment's value follows a block comment that is not closed"))
            continue
        if cur.at_stop():  # the value is the next key name's on this line
            continue
        if sep == ":" and cur.peek() not in _VALUE_OPENERS:
            status, code = _read_code(cur, annotated=True)
            if status == "open":
                reasons.append(_gave_up(loc, "key-named assignment's type annotation is not closed"))
                continue
            if status == "end":  # no `=` follows: an interface member or an object property, judged as the expression it is
                _judge(code, kinds, loc, findings, reasons, _SCALAR_REASON)
                continue
            if not _skip_trivia(cur):
                reasons.append(_gave_up(loc, "key-named assignment's value follows a block comment that is not closed"))
                continue
            if cur.at_stop():
                continue
        if cur.peek() == "":
            reasons.append(_gave_up(loc, "key-named assignment's value is not found"))
            continue
        if cur.peek() == "[":
            items = _read_list(cur)
            if items is None:
                reasons.append(_gave_up(loc, "key-named list is not closed"))
                continue
            _check_list(items, kinds, loc, findings, reasons, 0, budget)
            continue
        status, code = _read_code(cur, annotated=False)
        if status == "ambiguous":  # an angle-bracket value that a bare `>` ends before a literal: the literal may belong to it, so it is not analysed
            reasons.append(f"{loc}: key-named assignment's angle-bracket value continues into a literal on the next line: not analysed")
            continue
        if status != "end":
            reasons.append(_gave_up(loc, "key-named assignment's value is not closed"))
            continue
        _judge(code, kinds, loc, findings, reasons, _SCALAR_REASON)


def _scan_code(rel: str, text: str, reasons: list[str], budget: _LineBudget) -> list[dict]:
    findings: list[dict] = []
    lines = text.splitlines()
    first_send = 0
    has_chain = False
    has_simulation = False
    state: tuple[tuple, str] = ((), "")  # what the next line starts inside: the open constructs and the previous token (see _scan_line)
    markup = rel.lower().endswith(_MARKUP_SUFFIXES)  # `<!-- -->` is a comment in markup files only
    spent_reported = False  # the budget reason is given once per file, however many key lines follow
    for number, raw in enumerate(lines, start=1):
        line, strings, state = _scan_line(raw, state, markup)
        if not line.strip():
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
        try:
            _key_material_findings(line, raw, strings, loc, reasons, lines, number - 1, budget, findings)  # every assignment on the line counts, not one per line
        except _BudgetSpent:  # the scan's window-line budget ran out: this key line and the rest of the file are not analysed
            if not spent_reported:
                reasons.append(f"scan-line-budget-spent:{rel}")
                spent_reported = True
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


@functools.lru_cache(maxsize=4096)  # a key repeated across many aliases is looked up once; the cache is bounded
def _key_kinds(raw: str) -> frozenset[str]:
    """Length-rule kinds of one raw JSON key, never shown. The key is judged as a whole token, exactly as a quoted source key is (see
    `_KEY_ASSIGNMENT`): `privateKey.dev` and `private-key` are key names, `privateKey.path` is a locator and is not."""
    return _name_kinds(raw)


def _walk_structure(node, path: str, rel: str, findings: _Findings, ancestors: set[int], budget: list[int],
                    kinds: frozenset[str] = frozenset()) -> None:
    """Walk one parsed document. A YAML alias (`*anchor`) is the same object as its anchor, so a shared node is walked under every
    key that reaches it and each path is checked. Cycles are cut on the current path only (`ancestors`): a self-referencing anchor
    (`&x {again: *x}`) stops where it closes its own path. Every value visited, container or scalar, is charged to `budget`, the
    remaining visits of the file, so alias fan-out is bounded however much work each shared value causes.

    `path` is the rendered location shown in findings (secret-looking keys shown by length). `kinds` is the length-rule kinds of the key
    that holds this node, and only that key: a string under a nested object is checked against its own key name, as a source assignment
    would be. A list passes its own key's kinds to its items. Raw keys are never shown."""
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
                    _walk_structure(value, f"{path}.{_path_key(key)}" if path else _path_key(key), rel, findings, ancestors, budget,
                                    _key_kinds(str(key)))
            else:
                for index, value in enumerate(node):
                    _walk_structure(value, f"{path}[{index}]", rel, findings, ancestors, budget, kinds)
        finally:
            ancestors.discard(id(node))
    elif isinstance(node, str):
        if node and not _ENV_REFERENCE.match(node) and any(_key_length_ok(kind, node) for kind in kinds):
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


def _label(path_text: str, root: Path) -> str:
    """A path shown in a reason: relative to the scan root, or the bare name when it lies outside it."""
    try:
        return Path(path_text).relative_to(root).as_posix()
    except ValueError:
        return Path(path_text).name


def _candidate_files(base: Path, root: Path, skipped: list[str]):
    """Deterministic (sorted) walk below `base` that never descends into symlinked directories. A directory that cannot be listed and a
    symlinked directory are recorded in `skipped`: their contents were not analysed."""
    if base.is_file():
        yield base
        return

    def unlistable(error: OSError) -> None:
        where = _label(str(error.filename), root) if error.filename else "unnamed directory"
        skipped.append(f"{where}: skipped:unlistable-directory ({type(error).__name__})")

    for dirpath, dirnames, filenames in os.walk(base, followlinks=False, onerror=unlistable):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in dirnames:
            if os.path.islink(os.path.join(dirpath, name)):
                skipped.append(f"{_label(os.path.join(dirpath, name), root)}: skipped:symlink-directory")
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
    budget = _LineBudget(SCAN_LINE_BUDGET)  # window lines for the whole scan: key-named values read past their line draw on it
    not_read = 0
    for path in _candidate_files(base, root, skipped):
        kind = _kind(path)
        if not kind:
            continue
        if budget.spent:  # the budget ran out in an earlier file: this file is not read
            not_read += 1
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
            # the type is checked on the open descriptor, not on the path: a path swapped for a FIFO after the earlier checks is still
            # refused before a read. O_NONBLOCK makes the open of a FIFO with no writer return at once. Symlinks were resolved above.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):  # named pipes, sockets and devices are never read: a read could block the whole scan
                    skipped_files += 1
                    skipped.append(f"{rel}: skipped:not-a-regular-file")
                    continue
                if info.st_size > MAX_FILE_BYTES:
                    skipped_files += 1
                    skipped.append(f"{rel}: skipped:oversized")
                    continue
                data = handle.read()
        except OSError:
            skipped_files += 1
            skipped.append(f"{rel}: skipped:unreadable")
            continue
        except RuntimeError:  # resolve() refuses a symlink loop: the input cannot be analysed, the other files still are
            skipped_files += 1
            skipped.append(f"{rel}: skipped:symlink-loop")
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
            findings.extend(_scan_code(rel, text, skipped, budget))
        elif kind == "env":
            findings.extend(_scan_env(rel, text))
        else:
            _scan_data(rel, text, path.suffix.lower(), yaml_module, findings, skipped)
    if saw_yaml:
        skipped.append("yaml-config-structure: skipped:tool-missing (PyYAML is not installed)")
    if not_read:
        skipped.append(f"not-read-after-scan-line-budget:{not_read}-files")
        skipped_files += not_read
    return _report(findings, skipped, scanned, skipped_files, truncated)


def _report(findings: _Findings, skipped: list[str], scanned: int, skipped_files: int, truncated: bool) -> dict:
    """The result body of a scan. Every input that was not analysed is a reason, and a reason is a major `scan-incomplete` finding, so the
    status is `fail` (swarm/scan_status.py). A tree scan and an input that could not be opened both report through here."""
    findings.extend({**item, "location": "."} for item in incomplete_findings(skipped, scanned))
    counts = dict(findings.counts)
    failing = sum(counts[name] for name in FAIL_SEVERITIES)
    total = sum(counts.values())
    capped = total > MAX_FINDINGS
    if capped:
        skipped.append(f"findings: output capped at {MAX_FINDINGS} of {total} (counts are complete)")
    listed = findings.listed()
    listed_skipped = skipped
    if len(skipped) > MAX_SKIPPED_LISTED:  # the status already counts every reason; the listing shows the first ones and the exact remainder
        listed_skipped = [*skipped[:MAX_SKIPPED_LISTED], f"… and {len(skipped) - MAX_SKIPPED_LISTED} more not listed"]
    return {
        "findings": listed,
        "counts": counts,
        "files_scanned": scanned,
        "files_skipped": skipped_files,
        "truncated": truncated or capped,
        "skipped": listed_skipped,
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
    try:
        target = (root / args.path).resolve()
    except RuntimeError:  # a symlink loop: resolve() refuses it, so the input is reported as not analysed, not as an internal error
        return _report(_Findings(), [f"{args.path}: skipped:symlink-loop"], 0, 1, False)
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {root}")
    return scan_tree(target, root)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A29", "dapp_wallet_check", run, description=__doc__, add_args=add_args).main())
