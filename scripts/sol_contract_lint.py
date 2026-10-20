#!/usr/bin/env python3
"""A26 - lint Solidity sources in a repository for unpinned pragmas, tx.origin, selfdestruct, delegatecall, unchecked low-level calls and missing events (heuristic; optional slither/forge are reported as skipped when absent).

Scans *.sol files under --root/--path (never symlinks, at most 2000 files, files above 1 MiB and binaries skipped,
Foundry test/script files *.t.sol / *.s.sol and dependency directories skipped) with a comment-aware Solidity reader
and reports findings {id, severity, summary, location}. Read-only. Stdlib only; slither and forge are only looked up on
PATH (never required, never invoked): `skipped:tool-missing` when absent.

It is also the shared Solidity analysis engine of the A26 tools (swarm/tools/a26_tools.py loads it through
`support.script`): the source reader, NatSpec reader, guard and event analysis and the pragma range / known-bug rules
live here so the lint and the tools apply identical rules. Heuristic, not a compiler: it reads text, resolves no imports.
Public source for the pragma table: https://docs.soliditylang.org/en/latest/bugs.html (bugs_by_version.json).
`status` is `fail` when any major or blocker finding exists (exit code 1). `--dry-run` prints deterministic output and
reads nothing.
"""
from __future__ import annotations

import bisect
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402

MAX_FILE_BYTES = 1 << 20  # 1 MiB
MAX_FILES = 2000
MAX_FINDINGS = 1000
SKIP_DIRS = frozenset({".git", "node_modules", "lib", "out", "cache", "artifacts", "broadcast", "typechain", "typechain-types",
                       "coverage", "build", "dist", "__pycache__", ".swarm", ".venv", "venv"})
SEVERITIES = ("info", "minor", "major", "blocker")
SEV_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}

# --------------------------------------------------------------------------------------------------------------------
# Source reader: comments and string contents blanked (same length, same newlines) so offsets map to the raw text.
# --------------------------------------------------------------------------------------------------------------------


def strip_source(text: str) -> str:
    """Blank comments and string/hex/unicode literal contents; keep quotes, newlines and the exact length."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        ch = text[i]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join(c if c == "\n" else " " for c in text[i:j]))
            i = j
        elif ch in "\"'":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == ch or text[j] == "\n":
                    break
                j += 1
            j = min(j, n)
            body = text[i + 1:j]
            closed = j < n and text[j] == ch
            out.append(ch + "".join(c if c == "\n" else " " for c in body) + (ch if closed else ""))
            i = j + 1 if closed else j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_NEWLINES: tuple[str, list[int]] = ("", [])


def line_of(text: str, pos: int) -> int:
    """1-based line of offset `pos`; the newline offsets of the text last asked about are indexed once (a file with many findings
    must not rescan itself for each one)."""
    global _NEWLINES
    cached = _NEWLINES
    if cached[0] is not text:
        cached = _NEWLINES = (text, [i for i, c in enumerate(text) if c == "\n"])
    return bisect.bisect_left(cached[1], pos) + 1


_PAIRS = {"(": ")", "{": "}", "[": "]"}


def match_close(clean: str, open_idx: int) -> int:
    """Index of the bracket closing clean[open_idx], or -1 when unbalanced."""
    opener = clean[open_idx]
    closer = _PAIRS[opener]
    depth = 0
    for i in range(open_idx, len(clean)):
        c = clean[i]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` outside (), [] and {}; empty input gives []."""
    parts, depth, cur = [], 0, []
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------------------------------------------------

_CONTRACT = re.compile(r"\b(abstract\s+)?(contract|interface|library)\s+([A-Za-z_$][\w$]*)([^{;]*)\{")
_VISIBILITY = frozenset({"public", "external", "internal", "private"})
_MUTABILITY = frozenset({"pure", "view", "payable"})
_LOCATIONS = frozenset({"memory", "calldata", "storage"})
_VAR_ATTRS = frozenset({"public", "private", "internal", "constant", "immutable", "transient"})
_IDENT = r"[A-Za-z_$][\w$]*"
_SKIP_MEMBERS = re.compile(r"(struct|enum|using|type|import|pragma)\b")


def _norm_type(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _parse_params(text: str) -> list[tuple[str, str]]:
    """[(type, name)] of a parameter list; the name is "" for unnamed parameters."""
    params: list[tuple[str, str]] = []
    for part in split_top(text):
        tokens = [t for t in part.split() if t not in _LOCATIONS and t != "indexed"]
        if not tokens:
            continue
        type_tokens = [tokens[0]]
        rest = tokens[1:]
        if rest and rest[0] == "payable":
            type_tokens.append("payable")
            rest = rest[1:]
        params.append((" ".join(type_tokens), rest[-1] if rest else ""))
    return params


def _doc_before(lines: list[str], idx: int, col: int) -> str:
    """NatSpec text directly above the declaration on 0-based line idx ("" when none)."""
    if lines[idx][:col].strip():
        return ""
    i = idx - 1
    collected: list[str] = []
    while i >= 0 and lines[i].strip().startswith("///"):
        collected.append(lines[i].strip()[3:].strip())
        i -= 1
    if collected:
        return "\n".join(reversed(collected))
    if i >= 0 and lines[i].strip().endswith("*/"):
        j = i
        while j >= 0 and "/*" not in lines[j]:
            j -= 1
        if j >= 0 and lines[j].strip().startswith("/**") and not lines[j].strip().startswith("/**/"):
            block = lines[j:i + 1]
            cleaned = []
            for line in block:
                s = line.strip()
                s = s[3:] if s.startswith("/**") else s
                s = s[:-2] if s.endswith("*/") else s
                s = s.strip()
                s = s[1:].strip() if s.startswith("*") else s
                cleaned.append(s)
            return "\n".join(cleaned)
    return ""


def parse_natspec(doc: str) -> dict:
    """{'notice','inheritdoc','dev': bool, 'params': [names], 'returns': int} from NatSpec text."""
    notice = inherit = dev = False
    params: list[str] = []
    returns = 0
    for line in doc.split("\n"):
        s = line.strip()
        if not s.startswith("@"):
            continue
        tag, _, rest = s[1:].partition(" ")
        rest = rest.strip()
        if tag == "notice":
            notice = True
        elif tag == "inheritdoc":
            inherit = True
        elif tag == "dev":
            dev = True
        elif tag == "param":
            params.append(rest.split(None, 1)[0] if rest else "")
        elif tag == "return":
            returns += 1
    return {"notice": notice, "inheritdoc": inherit, "dev": dev, "params": params, "returns": returns}


def _parse_function_header(kind: str, name: str, header: str, open_paren: int) -> dict:
    close = match_close(header, open_paren)
    close = len(header) - 1 if close < 0 else close
    params = _parse_params(header[open_paren + 1:close])
    tail = header[close + 1:]
    visibility, mutability = "", "nonpayable"
    returns: list[tuple[str, str]] = []
    modifiers: list[tuple[str, str]] = []
    virtual = override = False
    i = 0
    while i < len(tail):
        m = re.compile(_IDENT).match(tail, i)
        if not m:
            i += 1
            continue
        ident = m.group(0)
        j = m.end()
        k = j
        while k < len(tail) and tail[k].isspace():
            k += 1
        args = ""
        if k < len(tail) and tail[k] == "(":
            end = match_close(tail, k)
            end = len(tail) - 1 if end < 0 else end
            args = tail[k + 1:end].strip()
            j = end + 1
        if ident in _VISIBILITY:
            visibility = ident
        elif ident in _MUTABILITY:
            mutability = ident
        elif ident == "virtual":
            virtual = True
        elif ident == "override":
            override = True
        elif ident == "returns":
            returns = _parse_params(args)
        else:
            modifiers.append((ident, args))
        i = j
    if kind in ("fallback", "receive"):
        visibility = visibility or "external"
    return {"params": params, "returns": returns, "visibility": visibility, "mutability": mutability,
            "modifiers": modifiers, "virtual": virtual, "override": override}


def _members(clean: str, lo: int, hi: int):
    """Yield (header_start, header, body_range | None) for each member of a contract body at nesting depth 0."""
    i = lo
    while i < hi:
        j, paren = i, 0
        while j < hi:
            c = clean[j]
            if c == "(":
                paren += 1
            elif c == ")":
                paren -= 1
            elif paren <= 0 and c in "{;":
                break
            j += 1
        if j >= hi:
            return
        header = clean[i:j]
        stripped = header.lstrip()
        start = i + (len(header) - len(stripped))
        if clean[j] == ";":
            yield start, stripped, None
            i = j + 1
        else:
            close = match_close(clean, j)
            close = hi - 1 if close < 0 or close >= hi else close
            yield start, stripped, (j + 1, close)
            i = close + 1


def parse_source(raw: str) -> list[dict]:
    """Contracts of a Solidity source: {name, kind, abstract, bases, line, functions, events, errors, state_vars}.

    A function is {kind, name, signature, params, returns, visibility, mutability, modifiers, virtual, override, body,
    has_body, line, doc}; `body` is the comment-free body text. Heuristic: no imports are resolved."""
    clean = strip_source(raw)
    lines = raw.split("\n")
    contracts: list[dict] = []
    pos = 0
    while True:
        m = _CONTRACT.search(clean, pos)
        if not m:
            break
        open_idx = m.end() - 1
        close = match_close(clean, open_idx)
        close = len(clean) - 1 if close < 0 else close
        bases: list[str] = []
        is_part = re.search(r"\bis\b(.*)", m.group(4), re.S)
        if is_part:
            for base in split_top(is_part.group(1)):
                bname = re.sub(r"\(.*", "", base, flags=re.S).strip().split(".")[-1]
                if bname:
                    bases.append(bname)
        contract = {"name": m.group(3), "kind": m.group(2), "abstract": bool(m.group(1)), "bases": bases,
                    "line": line_of(clean, m.start()), "functions": [], "events": [], "errors": [], "state_vars": []}
        for start, header, body_range in _members(clean, open_idx + 1, close):
            line_no = line_of(clean, start)
            idx = line_no - 1
            col = start - (clean.rfind("\n", 0, start) + 1)
            doc = parse_natspec(_doc_before(lines, idx, col)) if header else None
            fm = re.match(r"function\s+(" + _IDENT + r")\s*\(", header)
            sm = re.match(r"(constructor|fallback|receive)\s*\(", header)
            mm = re.match(r"modifier\s+(" + _IDENT + r")", header)
            em = re.match(r"event\s+(" + _IDENT + r")\s*\(", header)
            rm = re.match(r"error\s+(" + _IDENT + r")\s*\(", header)
            if fm or sm:
                kind, name = ("function", fm.group(1)) if fm else (sm.group(1), sm.group(1))
                open_paren = (fm or sm).end() - 1
                parsed = _parse_function_header(kind, name, header, open_paren)
                body = clean[body_range[0]:body_range[1]] if body_range else ""
                if contract["kind"] == "interface" and not parsed["visibility"]:
                    parsed["visibility"] = "external"
                if kind == "function" and not parsed["visibility"]:
                    parsed["visibility"] = "public"
                parsed.update({"kind": kind, "name": name, "body": body, "has_body": body_range is not None,
                               "body_span": body_range or (0, 0), "line": line_no, "doc": doc,
                               "signature": f"{name}({','.join(_norm_type(t) for t, _ in parsed['params'])})"})
                contract["functions"].append(parsed)
            elif mm:
                contract["functions"].append({"kind": "modifier", "name": mm.group(1), "params": [], "returns": [],
                                              "visibility": "", "mutability": "nonpayable", "modifiers": [],
                                              "virtual": False, "override": False,
                                              "body": clean[body_range[0]:body_range[1]] if body_range else "",
                                              "has_body": body_range is not None, "body_span": body_range or (0, 0),
                                              "line": line_no, "doc": doc, "signature": mm.group(1)})
            elif em or rm:
                match = em or rm
                params = _parse_params(header[match.end() - 1 + 1:header.rfind(")")]) if ")" in header else []
                bucket = contract["events"] if em else contract["errors"]
                bucket.append({"name": match.group(1), "params": params, "line": line_no, "doc": doc,
                               "signature": f"{match.group(1)}({','.join(_norm_type(t) for t, _ in params)})"})
            elif body_range is None and header and not _SKIP_MEMBERS.match(header):
                assign = re.search(r"(?<![=!<>])=(?![=>])", header)
                decl, init = (header[:assign.start()], header[assign.end():]) if assign else (header, "")
                decl = re.sub(r"\boverride\s*\([^)]*\)", " ", decl)
                tokens = [t for t in decl.split() if t != "override"]
                if len(tokens) >= 2:
                    name = tokens[-1]
                    type_tokens = [t for t in tokens[:-1] if t not in _VAR_ATTRS]
                    contract["state_vars"].append({"name": name, "type": _norm_type(" ".join(type_tokens)),
                                                   "constant": "constant" in tokens, "immutable": "immutable" in tokens,
                                                   "initial_value": bool(init.strip()), "line": line_no})
        contracts.append(contract)
        pos = close + 1
    return contracts


# --------------------------------------------------------------------------------------------------------------------
# Events and access guards
# --------------------------------------------------------------------------------------------------------------------

_CALL = re.compile(r"(?<![\w.$])(" + _IDENT + r")\s*\(")
_NOT_CALLS = frozenset({"if", "for", "while", "require", "assert", "revert", "return", "emit", "new", "address", "payable",
                        "keccak256", "abi", "type", "uint", "uint256", "int", "int256", "bytes", "bytes32", "bool", "string",
                        "unchecked", "assembly", "mapping", "delete", "else", "do", "try", "catch", "ecrecover", "sha256",
                        "ripemd160", "addmod", "mulmod", "gasleft", "blockhash", "selfdestruct", "super", "this"})
_NON_ACCESS_ONLY = frozenset({"onlyProxy", "onlyInitializing", "onlyDelegateCall", "onlyDelegated"})
_ACCESS_NAMES = frozenset({"auth", "requiresAuth", "restricted", "authorized", "isOwner"})
_ROLE_WORDS = r"(?:admin|owner|operator|minter|authori[sz]ed|whitelist|allow|role|governor|guardian|manager|keeper)"


def _callable_index(contracts: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for contract in contracts:
        for fn in contract["functions"]:
            if fn["kind"] in ("function", "modifier"):
                index.setdefault(fn["name"], []).append(fn)
    return index


def _calls_of(fn: dict) -> list[str]:
    """Names `fn` calls without a receiver (internal calls and free functions), first-seen order; cached on the function."""
    cached = fn.get("_calls")
    if cached is None:
        cached = []
        for m in _CALL.finditer(fn["body"]):
            name = m.group(1)
            if name not in _NOT_CALLS and name not in cached:
                cached.append(name)
        fn["_calls"] = cached
    return cached


def _reachable(fn: dict, index: dict[str, list[dict]]) -> list[dict]:
    """`fn` plus every function of this source it calls, followed transitively (cycles visited once)."""
    seen = {id(fn)}
    order = [fn]
    for current in order:  # the list grows while it is walked: a breadth-first search
        for name in _calls_of(current):
            for target in index.get(name, []):
                if target["kind"] == "function" and id(target) not in seen:
                    seen.add(id(target))
                    order.append(target)
    return order


def emission_status(contracts: list[dict]) -> dict[int, str]:
    """id(function) -> 'emits' | 'delegated' | 'missing' for every state-changing public/external function with a body.

    'emits': the body, a modifier named initializer/reinitializer, or an internal function it calls (same source,
    followed transitively) contains `emit`. 'delegated': it emits nothing visible but calls an underscore-prefixed
    internal function or `super.` that this source does not define (inherited code may emit). 'missing': neither."""
    index = _callable_index(contracts)
    result: dict[int, str] = {}
    for contract in contracts:
        if contract["kind"] != "contract":
            continue
        for fn in contract["functions"]:
            if not (fn["kind"] == "function" and fn["visibility"] in ("public", "external") and fn["mutability"] in ("nonpayable", "payable")
                    and fn["has_body"] and fn["body"].strip()):
                continue
            reach = _reachable(fn, index)
            emits = any(re.search(r"\bemit\s+" + _IDENT, f["body"]) or any(m in ("initializer", "reinitializer") for m, _ in f["modifiers"])
                        for f in reach)
            delegated = any(re.search(r"\bsuper\s*\.", f["body"]) or any(n.startswith("_") and n not in index for n in _calls_of(f))
                            for f in reach)
            result[id(fn)] = "emits" if emits else "delegated" if delegated else "missing"
    return result


_SENDER = r"(?:msg\.sender|_msgSender\s*\(\s*\))"
_NAME = r"[A-Za-z_$][\w$]{0,63}"
# the other side of a sender comparison: a (member) name, optionally called with no arguments, or address(this); bounded so that
# a pathological run of word characters cannot make the scan quadratic
_RHS = r"(address\s*\(\s*this\s*\)|[A-Za-z_$][\w$]{0,63}(?:\.[A-Za-z_$][\w$]{0,63}){0,4}(?:\s*\(\s*\))?)(?![\w$.(])"
_LHS = r"(?<![\w$.])" + _RHS.replace("(?![\\w$.(])", "")
_AND_PREFIX = r"(?:[^;{|]{0,160}?&&\s*)?"  # `require(amount > 0 && msg.sender == owner)`: earlier conjuncts do not matter
_REQ = r"\b(?:require|assert)\s*\(\s*" + _AND_PREFIX
_IF_REVERT = r"\)\s*\{?\s*(?:revert|return)\b"
_NOT_A_PRINCIPAL = frozenset({"tx.origin", "address(0)"})


def _inline_guards(body: str) -> set[str]:
    """Sender checks that stop an unauthorised caller. `require`/`assert` need `msg.sender == X`, `X == msg.sender`,
    hasRole(X, msg.sender) or a role-named allow-list lookup; `if` needs the negated form followed by revert/return.
    A comparison with address(0) or tx.origin, `!=` inside require, and an `if` that merely branches are not guards."""
    found: set[str] = set()
    if re.search(r"\b_checkOwner\s*\(\s*\)", body):
        found.add("owner")
    for m in re.finditer(r"\b_checkRole\s*\(\s*([^,)]{1,120})", body):
        found.add(m.group(1).strip())
    for lead, end in ((_REQ, ""), (r"\bif\s*\(\s*!\s*", r"\s*" + _IF_REVERT)):
        for m in re.finditer(lead + r"hasRole\s*\(\s*([^,]{1,120}),\s*" + _SENDER + r"\s*\)" + end, body):
            found.add(m.group(1).strip())
        for m in re.finditer(lead + r"(" + _NAME + r")\s*\[\s*" + _SENDER + r"\s*\]" + end, body):
            if re.search(_ROLE_WORDS, m.group(1), re.I):
                found.add(f"{m.group(1)}[msg.sender]")
        for m in re.finditer(lead + r"(" + _NAME + r")\s*\(\s*" + _SENDER + r"\s*\)" + end, body):
            if m.group(1) != "hasRole" and re.search(_ROLE_WORDS, m.group(1), re.I):
                found.add(f"{m.group(1)}(msg.sender)")
    for pattern in (_REQ + _SENDER + r"\s*==\s*" + _RHS, _REQ + _LHS + r"\s*==\s*" + _SENDER,
                    r"\bif\s*\(\s*" + _SENDER + r"\s*!=\s*" + _RHS + r"\s*" + _IF_REVERT,
                    r"\bif\s*\(\s*" + _LHS + r"\s*!=\s*" + _SENDER + r"\s*" + _IF_REVERT):
        for m in re.finditer(pattern, body):
            who = re.sub(r"\s+", "", m.group(1))
            if who not in _NOT_A_PRINCIPAL:
                found.add(f"msg.sender=={who}")
    return found


_SENDER_AWARE = re.compile(r"msg\.sender|_msgSender\s*\(|\b_?check\w*\s*\(|\b_only\w*\s*\(|\bhasRole\b|\bisOwner\b|\bowner\s*\(|\bauth\w*\s*\(")


def _is_access_modifier(name: str, defined: list[dict]) -> bool:
    """A modifier named like an access check (only*, auth, restricted, ...) is access control, unless this source defines it
    and its body never looks at the caller (onlyWhileOpen, onlyAfter(time)); a modifier defined here that checks the sender is
    access control whatever it is called."""
    named = name in _ACCESS_NAMES or (name.startswith("only") and name not in _NON_ACCESS_ONLY)
    if not defined:
        return named
    if any(_inline_guards(t["body"]) for t in defined):
        return True
    return named and any(_SENDER_AWARE.search(t["body"]) for t in defined)


def _direct_guards(fn: dict, index: dict[str, list[dict]]) -> set[str]:
    cached = fn.get("_guards")
    if cached is not None:
        return cached
    found = _inline_guards(fn["body"])
    for name, args in fn["modifiers"]:
        if _is_access_modifier(name, [t for t in index.get(name, []) if t["kind"] == "modifier"]):
            found.add(args.strip() if name == "onlyRole" and args.strip() else name)
    fn["_guards"] = found
    return found


def guards(fn: dict, index: dict[str, list[dict]]) -> list[str]:
    """Sorted access-control labels protecting `fn`: access modifiers (onlyRole(X) -> X, onlyOwner, auth, restricted, or a
    modifier defined in this source that checks the sender), inline checks (_checkOwner, _checkRole(X), hasRole(X,
    msg.sender), msg.sender == X, allow-list[msg.sender]) and the guards of same-source functions it calls. tx.origin never
    counts; initializer / nonReentrant / whenNotPaused / onlyProxy are not access control. [] means unprotected."""
    found: set[str] = set()
    for f in _reachable(fn, index):
        found |= _direct_guards(f, index)
    return sorted(found)


# --------------------------------------------------------------------------------------------------------------------
# Pragma ranges and the known-bug table
# --------------------------------------------------------------------------------------------------------------------

Version = tuple[int, int, int]
INF: Version = (10**9, 0, 0)
# Solidity 0.8.x releases (https://docs.soliditylang.org/en/latest/bugs.html, bugs_by_version.json).
RELEASES: tuple[Version, ...] = tuple((0, 8, patch) for patch in range(0, 38))
# Compiler bugs rated medium or higher in bugs.json that apply to >= 0.8.0 builds, same source. A bug affects introduced <= v < fixed.
# Rows the project rates "low/medium" or lower (SOL-2026-4, SOL-2026-5, SOL-2026-6, SOL-2026-7, SOL-2025-1, ...) are not listed.
# needs: "" always, "optimizer" only with the optimizer on, "via_ir" only with the IR pipeline, "layout_at" only when the source
# uses the `layout at` contract specifier. SOL-2026-1 also needs evmVersion >= cancun (the default of every listed release):
# the evm version is not an input, so it is judged as if it applied.
KNOWN_BUGS: tuple[dict, ...] = (
    {"uid": "SOL-2021-1", "name": "KeccakCaching", "introduced": (0, 0, 0), "fixed": (0, 8, 3), "severity": "medium", "needs": "optimizer"},
    {"uid": "SOL-2022-4", "name": "InlineAssemblyMemorySideEffects", "introduced": (0, 8, 13), "fixed": (0, 8, 15), "severity": "medium", "needs": "optimizer"},
    {"uid": "SOL-2022-6", "name": "AbiReencodingHeadOverflowWithStaticArrayCleanup", "introduced": (0, 5, 8), "fixed": (0, 8, 16), "severity": "medium", "needs": ""},
    {"uid": "SOL-2022-7", "name": "StorageWriteRemovalBeforeConditionalTermination", "introduced": (0, 8, 13), "fixed": (0, 8, 17), "severity": "medium/high", "needs": "optimizer"},
    {"uid": "SOL-2026-1", "name": "TransientStorageClearingHelperCollision", "introduced": (0, 8, 28), "fixed": (0, 8, 34), "severity": "high", "needs": "via_ir"},
    {"uid": "SOL-2026-2", "name": "UnsoundSpillInMutualRecursion", "introduced": (0, 7, 2), "fixed": (0, 8, 36), "severity": "medium", "needs": "via_ir"},
    # a `layout at` specifier too close to the end of storage made the compiler reverse linearizedBaseContracts in place: a miscompilation
    {"uid": "SOL-2026-3", "name": "InheritanceOrderReversalOnStorageEndWarning", "introduced": (0, 8, 29), "fixed": (0, 8, 36), "severity": "medium", "needs": "layout_at"},
)


def quoted(text: str) -> str:
    """`text` repr-quoted when it is a short version-like string, else only its length: a malformed value may be anything."""
    return repr(text) if re.fullmatch(r"[0-9xX\s.*^~<>=|-]{1,64}", text) else f"<{len(text)} characters>"


# Secret-shaped substrings (cloud and VCS tokens, JWTs, 40+ hex digits, user:password in a URL, long mixed alphanumeric runs, a
# quoted 12+ word phrase) are never echoed; the A26 tools apply the same rule (tests keep the two copies identical).
SECRET_SHAPES = re.compile(
    r"(?:AKIA|ASIA)[0-9A-Z]{12,}|\bgh[pousr]_[A-Za-z0-9]{16,}|\bgithub_pat_\w{16,}|\bxox[abprs]-[\w-]{8,}|\bsk-[A-Za-z0-9_-]{16,}"
    r"|\bAIza[\w-]{20,}|\beyJ[\w-]{8,}\.[\w-]{8,}|(?:0x)?[0-9a-fA-F]{40,}|://[^\s/?#]*@"
    r"|(?=[A-Za-z0-9+/=_-]*\d)[A-Za-z0-9+/=_-]{32,}|(['\"])(?:[a-z]{3,8} ){11,}[a-z]{3,8}\1")


def redact(text: str) -> str:
    """Message text with secret-shaped substrings replaced by their length, any single token over 80 characters clipped and
    the whole capped at 360 characters, so no credential and no oversized identifier reaches a finding."""
    text = SECRET_SHAPES.sub(lambda m: f"<redacted {len(m.group(0))} chars>", text)
    text = re.sub(r"\S{81,}", lambda m: m.group(0)[:72] + "...", text)
    return text if len(text) <= 360 else text[:357] + "..."


_VER = re.compile(r"(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?$")


def parse_version(text: str) -> Version:
    """Strict X.Y.Z; ValueError otherwise."""
    m = re.fullmatch(r"(\d{1,6})\.(\d{1,6})\.(\d{1,6})", text.strip())
    if not m:
        raise ValueError(f"not a X.Y.Z version: {quoted(text)}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _partial(text: str) -> tuple[Version, Version]:
    """Interval [lo, hi) of a possibly partial version: 0.8.28 -> [0.8.28, 0.8.29), 0.8 / 0.8.x -> [0.8.0, 0.9.0)."""
    t = text.strip()
    if t in ("*", "x", "X", ""):
        return (0, 0, 0), INF
    m = _VER.match(t)
    if not m:
        raise ValueError(f"bad version {quoted(text)}")
    major = int(m.group(1))
    minor, patch = m.group(2), m.group(3)
    if minor is None or minor in "xX*":
        return (major, 0, 0), (major + 1, 0, 0)
    if patch is None or patch in "xX*":
        return (major, int(minor), 0), (major, int(minor) + 1, 0)
    return (major, int(minor), int(patch)), (major, int(minor), int(patch) + 1)


def _prefix_upper(body: str, levels: int) -> Version:
    """Exclusive upper bound of the first `levels` components of `body`; a wildcard component ends the prefix (0.x is 0)."""
    numbers: list[int] = []
    for part in body.split(".")[:levels]:
        if part in ("x", "X", "*"):
            break
        numbers.append(int(part))
    if not numbers:
        return INF
    numbers[-1] += 1
    major, minor, patch = (numbers + [0, 0, 0])[:3]
    return major, minor, patch


def _comparator(token: str) -> tuple[Version, Version]:
    """Interval of one comparator, as the Solidity compiler reads it (liblangutil/SemVerHandler.cpp): `~V` keeps the first two
    components (one when V has one), `^V` keeps the first two when the major is 0 and V has at least two components, else the
    major only; so ^0.8.20 is >=0.8.20 <0.9.0, ^0.0.3 is >=0.0.3 <0.1.0 (not <0.0.4 as in npm) and ^0 is <1.0.0."""
    for op in (">=", "<=", "^", "~", ">", "<", "="):
        if token.startswith(op):
            body = token[len(op):]
            lo, hi = _partial(body)
            if op == ">=":
                return lo, INF
            if op == "<=":
                return (0, 0, 0), hi
            if op == ">":
                return hi, INF
            if op == "<":
                return (0, 0, 0), lo
            if op == "=":
                return lo, hi
            parts = body.split(".")
            if op == "~":
                return lo, _prefix_upper(body, 2 if len(parts) >= 2 else 1)
            return lo, _prefix_upper(body, 2 if parts[0] == "0" and len(parts) != 1 else 1)
    return _partial(token)


def parse_range(expr: str) -> list[tuple[Version, Version]]:
    """OR-groups of a `pragma solidity` expression as non-empty [lo, hi) intervals; ValueError on bad syntax."""
    text = expr.strip()
    if not text or len(text) > 64:
        raise ValueError("empty or over-long version expression")
    groups: list[tuple[Version, Version]] = []
    for part in text.split("||"):
        part = re.sub(r"(\s*)(\d[\w.*]*)\s+-\s+(\d[\w.*]*)", lambda m: f">={m.group(2)} <={m.group(3)}", part.strip())
        tokens = re.findall(r"(?:[<>]=?|[=^~])?\s*[\dxX*][\w.*]*", part)
        if not tokens or re.sub(r"(?:[<>]=?|[=^~])?\s*[\dxX*][\w.*]*", "", part).strip():
            raise ValueError(f"cannot parse version expression {quoted(expr)}")
        lo, hi = (0, 0, 0), INF
        for token in tokens:
            t_lo, t_hi = _comparator(re.sub(r"\s+", "", token))
            lo, hi = max(lo, t_lo), min(hi, t_hi)
        if lo < hi:
            groups.append((lo, hi))
    return groups


def admitted_releases(groups: list[tuple[Version, Version]]) -> list[Version]:
    return [v for v in RELEASES if any(lo <= v < hi for lo, hi in groups)]


def _fmt(v: Version) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def pragma_findings(expr: str, *, min_version: Version, floating_severity: str, via_ir: bool, optimizer: bool, layout_at: bool = False) -> dict:
    """Policy review of one pragma expression.

    Returns {'pinned': bool, 'floating': bool, 'admits_below_min': bool, 'lowest': 'X.Y.Z' | '', 'buggy': [bug names],
    'findings': [{id, severity, summary}]}. Pinned = exactly one version admitted (one interval of width one patch).
    Known bugs are judged against the 0.8.x release list only and only for the features the build uses (optimizer, via_ir,
    `layout at`); a pinned buggy version is major, a range that merely admits a buggy version is minor."""
    groups = parse_range(expr)
    pinned = len(groups) >= 1 and all(hi == (lo[0], lo[1], lo[2] + 1) for lo, hi in groups) and len({lo for lo, _ in groups}) == 1
    findings: list[dict] = []
    if not groups:
        return {"pinned": False, "floating": False, "admits_below_min": False, "lowest": "", "buggy": [],
                "findings": [{"id": "PRAGMA-EMPTY-RANGE", "severity": "major", "summary": f"`{expr}` admits no version"}]}
    lowest = min(lo for lo, _ in groups)
    admits_below = any(lo < min_version for lo, _ in groups)
    if not pinned:
        findings.append({"id": "PRAGMA-NOT-PINNED", "severity": floating_severity,
                         "summary": f"`{expr}` is a range, not one exact compiler version: builds are not reproducible across compilers"})
    if admits_below:
        findings.append({"id": "PRAGMA-BELOW-MIN", "severity": "major",
                         "summary": f"`{expr}` admits versions below the policy minimum {_fmt(min_version)} (lowest bound {_fmt(lowest)})"})
    releases = admitted_releases(groups)
    enabled = {"": True, "optimizer": optimizer, "via_ir": via_ir, "layout_at": layout_at}
    hit: list[dict] = []
    for bug in KNOWN_BUGS:
        if not enabled[bug["needs"]]:
            continue
        affected = [v for v in releases if bug["introduced"] <= v < bug["fixed"]]
        if affected:
            hit.append({**bug, "affected": affected})
    if hit:
        listed = "; ".join(f"{bug['name']} ({bug['uid']}, {bug['severity']}, fixed in {_fmt(bug['fixed'])})" for bug in hit[:4])
        more = f" and {len(hit) - 4} more" if len(hit) > 4 else ""
        findings.append({"id": "PRAGMA-KNOWN-BUG", "severity": "major" if pinned else "minor",
                         "summary": f"`{expr}` admits versions affected by {len(hit)} compiler bug(s): {listed}{more}"})
    if not releases and not admits_below:
        findings.append({"id": "PRAGMA-UNKNOWN-VERSION", "severity": "info",
                         "summary": f"`{expr}` admits no known 0.8.x release; compiler bugs could not be checked"})
    return {"pinned": pinned, "floating": not pinned, "admits_below_min": admits_below, "lowest": _fmt(lowest),
            "buggy": sorted(bug["name"] for bug in hit), "findings": findings}


# --------------------------------------------------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------------------------------------------------

_PRAGMA = re.compile(r"\bpragma\s+solidity\s+([^;]{1,200});")
_LOW_LEVEL = re.compile(r"\.(call|delegatecall|staticcall|send)\s*(?:\{[^}]{0,400}\})?\s*\(")
_LAYOUT_AT = re.compile(r"\blayout\s+at\b")


def _statement_prefix(clean: str, pos: int) -> str:
    start = max(clean.rfind(";", 0, pos), clean.rfind("{", 0, pos), clean.rfind("}", 0, pos)) + 1
    return clean[start:pos]


def _enclosing_function(contracts: list[dict], pos: int) -> dict | None:
    """The function or modifier whose body contains offset `pos` of the stripped source, else None."""
    for contract in contracts:
        for fn in contract["functions"]:
            if fn["has_body"] and fn["body_span"][0] <= pos < fn["body_span"][1]:
                return fn
    return None


def lint_source(rel: str, raw: str) -> list[dict]:
    """Findings {id, severity, summary, location} for one Solidity file."""
    clean = strip_source(raw)
    out: list[dict] = []

    def add(rule: str, severity: str, summary: str, pos: int) -> None:
        out.append({"id": rule, "severity": severity, "summary": redact(summary), "location": f"{rel}:{line_of(clean, pos)}"})

    pragmas = list(_PRAGMA.finditer(clean))
    uses_layout_at = bool(_LAYOUT_AT.search(clean))
    if not pragmas and re.search(_CONTRACT, clean):
        add("SOL-PRAGMA-MISSING", "minor", "no `pragma solidity` directive: the compiler version is unconstrained", 0)
    for m in pragmas:
        try:
            review = pragma_findings(m.group(1), min_version=(0, 8, 0), floating_severity="minor", via_ir=False, optimizer=True,
                                     layout_at=uses_layout_at)
        except ValueError as exc:
            add("SOL-PRAGMA-INVALID", "minor", f"unparseable pragma: {exc}", m.start())
            continue
        for f in review["findings"]:
            if f["id"] in ("PRAGMA-NOT-PINNED", "PRAGMA-KNOWN-BUG"):
                add("SOL-" + f["id"], f["severity"], f["summary"], m.start())
    for m in re.finditer(r"\btx\s*\.\s*origin\b", clean):
        window = clean[max(0, m.start() - 40):m.end() + 40]
        authz = re.search(r"tx\s*\.\s*origin\s*[!=]=|[!=]=\s*tx\s*\.\s*origin", window)
        add("SOL-TX-ORIGIN", "major" if authz else "minor",
            "tx.origin used in an authorization comparison (phishable through any contract the user calls; use msg.sender)"
            if authz else "tx.origin read: never use it for authorization", m.start())
    for m in re.finditer(r"\b(selfdestruct|suicide)\s*\(", clean):
        add("SOL-SELFDESTRUCT", "major", "selfdestruct: deprecated, since EIP-6780 (Cancun) it only deletes code in the creating "
            "transaction, and it breaks proxy implementations", m.start())
    contracts = parse_source(raw)
    index = _callable_index(contracts)
    for m in _LOW_LEVEL.finditer(clean):
        kind = m.group(1)
        fn = _enclosing_function(contracts, m.start())
        if kind == "delegatecall":
            exposed = fn is None or (fn["visibility"] in ("public", "external") and not guards(fn, index))
            add("SOL-DELEGATECALL", "major" if exposed else "minor",
                "delegatecall in a public/external function with no access control: the callee runs with this contract's storage"
                if exposed else "delegatecall: callee code runs with this contract's storage and balance; confirm the target is trusted",
                m.start())
        prefix = _statement_prefix(clean, m.start())
        checked = bool(re.search(r"(?<![=!<>])=(?!=)|\b(?:require|assert|if|return)\b|\(\s*bool\b", prefix))
        if not checked:
            add("SOL-UNCHECKED-LOW-LEVEL-CALL", "major",
                f".{kind}(...) result is discarded: a failed call goes unnoticed (check the returned bool)", m.start())
    status = emission_status(contracts)
    for contract in contracts:
        for fn in contract["functions"]:
            if status.get(id(fn)) == "missing":
                out.append({"id": "SOL-MISSING-EVENT", "severity": "minor",
                            "summary": redact(f"{contract['name']}.{fn['signature']} changes state but emits no event (directly or via same-file callees)"),
                            "location": f"{rel}:{fn['line']}"})
    return out


def _collect(root: Path, start: Path) -> tuple[list[tuple[str, str]], dict]:
    """[(relative path, text)] of the .sol files under start plus skip counters."""
    skipped = {"symlinks": 0, "test_files": 0, "too_large": 0, "binary": 0, "unreadable": 0, "limit_reached": False}
    files: list[tuple[str, str]] = []
    if start.is_file():
        candidates = [start]
    else:
        candidates = []
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
            candidates.extend(Path(dirpath) / name for name in sorted(filenames) if name.endswith(".sol"))
    for path in sorted(candidates, key=lambda p: p.relative_to(root).as_posix()):
        if path.is_symlink():
            skipped["symlinks"] += 1
            continue
        if path.name.endswith((".t.sol", ".s.sol")):
            skipped["test_files"] += 1
            continue
        if len(files) >= MAX_FILES:
            skipped["limit_reached"] = True
            break
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped["too_large"] += 1
                continue
            data = path.read_bytes()
        except OSError:
            skipped["unreadable"] += 1
            continue
        if b"\0" in data:
            skipped["binary"] += 1
            continue
        files.append((path.relative_to(root).as_posix(), data.decode("utf-8", errors="replace")))
    return files, skipped


def _tool_status(binary: str) -> str:
    return "available:not-invoked" if shutil.which(binary) else "skipped:tool-missing"


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "files_scanned": 0,
                "skipped": {"symlinks": 0, "test_files": 0, "too_large": 0, "binary": 0, "unreadable": 0, "limit_reached": False},
                "counts": {name: 0 for name in SEVERITIES}, "findings": [], "findings_total": 0,
                "tools": {"slither": "skipped:dry-run", "forge": "skipped:dry-run"},
                "summary": "dry-run: sol_contract_lint would scan *.sol files under --path within --root"}
    root = ctx.root.resolve()
    target = (root / args.path).resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {root}")
    files, skipped = _collect(root, target)
    findings: list[dict] = []
    for rel, text in files:
        findings.extend(lint_source(rel, text))
    findings.sort(key=lambda f: (f["location"].rsplit(":", 1)[0], int(f["location"].rsplit(":", 1)[1]), -SEV_RANK[f["severity"]], f["id"]))
    counts = {name: sum(1 for f in findings if f["severity"] == name) for name in SEVERITIES}
    failing = counts["major"] + counts["blocker"]
    return {"status": "fail" if failing else "ok", "files_scanned": len(files), "skipped": skipped, "counts": counts,
            "findings": findings[:MAX_FINDINGS], "findings_total": len(findings),
            "tools": {"slither": _tool_status("slither"), "forge": _tool_status("forge")},
            "summary": f"{len(files)} file(s) scanned, {len(findings)} finding(s), {failing} major or worse"}


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A26", "sol_contract_lint", run, description=__doc__, add_args=add_args).main())
