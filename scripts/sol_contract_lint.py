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
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402

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

_DECL_HEAD = re.compile(r"\b(abstract\s+)?(contract|interface|library)\s+([A-Za-z_$][\w$]*)")
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
    # Every declaration head is found in one scan; each header then runs to the first `{`/`;` without crossing the next
    # head. A header padded past any fixed bound (a long comment blanks to equal-length spaces, a long inheritance list)
    # is still parsed, while a run of body-less `contract a` keywords costs linear time instead of one rescan per keyword.
    heads = list(_DECL_HEAD.finditer(clean))
    pos = 0
    for k, m in enumerate(heads):
        if m.start() < pos:
            continue  # inside the body of a contract that is already parsed
        limit = heads[k + 1].start() if k + 1 < len(heads) else len(clean)
        brace = clean.find("{", m.end(), limit)
        semi = clean.find(";", m.end(), limit)
        if brace < 0 or (semi >= 0 and semi < brace):
            continue  # a forward reference or a keyword with no body: nothing to check, never silently part of a check
        open_idx = brace
        header = clean[m.end():open_idx]
        close = match_close(clean, open_idx)
        close = len(clean) - 1 if close < 0 else close
        bases: list[str] = []
        is_part = re.search(r"\bis\b(.*)", header, re.S)
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
            um = re.match(r"function\s*\(", header)  # `function () external payable`: the fallback of solc before 0.6
            mm = re.match(r"modifier\s+(" + _IDENT + r")", header)
            em = re.match(r"event\s+(" + _IDENT + r")\s*\(", header)
            rm = re.match(r"error\s+(" + _IDENT + r")\s*\(", header)
            if fm or sm or um:
                if fm:
                    kind, name = "function", fm.group(1)
                    open_paren = fm.end() - 1
                elif sm:
                    kind, name = sm.group(1), sm.group(1)
                    open_paren = sm.end() - 1
                else:
                    assert um is not None
                    kind, name = "fallback", "fallback"
                    open_paren = um.end() - 1
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
            elif em:
                params = _parse_params(header[em.end() - 1 + 1:header.rfind(")")]) if ")" in header else []
                contract["events"].append({"name": em.group(1), "params": params, "line": line_no, "doc": doc,
                                           "signature": f"{em.group(1)}({','.join(_norm_type(t) for t, _ in params)})"})
            elif rm:
                params = _parse_params(header[rm.end() - 1 + 1:header.rfind(")")]) if ")" in header else []
                contract["errors"].append({"name": rm.group(1), "params": params, "line": line_no, "doc": doc,
                                           "signature": f"{rm.group(1)}({','.join(_norm_type(t) for t, _ in params)})"})
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
    declared = frozenset(m.group(1) for m in _TYPE_DECL.finditer(clean))  # the names of this source's structs, enums and contracts
    for contract in contracts:
        for fn in contract["functions"]:
            fn["types"] = declared
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


ENTRY_KINDS = ("function", "fallback", "receive")  # externally callable entry points: a fallback or receive can write state too


_TYPE_SPELLING = {"uint": "uint256", "int": "int256", "byte": "bytes1"}  # spellings that solc equates in a signature
_TYPE_SPELLING_RE = re.compile(r"\b(uint|int|byte)\b")


def _hiding_key(fn: dict) -> tuple:
    """What a definition hides in the bases it overrides: a function by its name and parameter types (an overload with other types is
    still inherited, and `uint` is `uint256`), a modifier by its name (Solidity has no modifier overloads)."""
    if fn["kind"] == "modifier":
        return ("modifier", fn["name"])
    return ("function", fn["name"], tuple(_TYPE_SPELLING_RE.sub(lambda m: _TYPE_SPELLING[m.group(1)], t) for t, _ in fn["params"]))


def visible_index(contract: dict, contracts: list[dict]) -> dict[str, list[dict]]:
    """Functions and modifiers `contract` can call or apply: its own and those of its bases defined in this source, the most
    derived first. A definition hides only the base definitions it overrides (see _hiding_key), so an inherited overload the child
    does not redefine stays callable; a same-named callable of an unrelated contract is never visible: a guard belongs to the
    contract that defines it."""
    by_name: dict[str, dict] = {}
    for c in contracts:
        by_name.setdefault(c["name"], c)
    index: dict[str, list[dict]] = {}
    hidden: set[tuple] = set()
    queue, seen = [contract], {id(contract)}
    for current in queue:  # the list grows while it is walked: a breadth-first walk up the bases
        own = [fn for fn in current["functions"] if fn["kind"] in ("function", "modifier")]
        for fn in own:
            if _hiding_key(fn) not in hidden:
                index.setdefault(fn["name"], []).append(fn)
        hidden.update(_hiding_key(fn) for fn in own)
        for base in current["bases"]:
            parent = by_name.get(base)
            if parent is not None and id(parent) not in seen:
                seen.add(id(parent))
                queue.append(parent)
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


def _holding(universe: list[dict], index: dict[str, list[dict]], own) -> set[int]:
    """ids of the functions of `universe` for which `own` holds, or that make a call at which every function it may resolve to
    (see _targets) holds: one such call is enough. A least fixpoint, so a cycle holds only through a witness; each call site is
    counted down once per target, so the work is linear in the call sites."""
    holds: set[int] = set()
    queue: list[dict] = []
    for f in universe:
        if own(f):
            holds.add(id(f))
            queue.append(f)
    site_fn: list[dict] = []  # per call site: the function that makes it
    left: list[int] = []  # per call site: its targets that do not hold yet
    site_of: dict[int, list[int]] = {}  # id of a function -> the call sites that may target it
    for f in universe:
        for m in _CALL.finditer(f["body"]):
            name = m.group(1)
            if name in _NOT_CALLS:
                continue
            targets = _targets(f, index, name, m.end() - 1)
            if not targets:
                continue
            sid = len(site_fn)
            site_fn.append(f)
            left.append(len(targets))
            for t in targets:
                site_of.setdefault(id(t), []).append(sid)
    while queue:
        t = queue.pop()
        for sid in site_of.get(id(t), ()):
            left[sid] -= 1
            if left[sid] == 0 and id(site_fn[sid]) not in holds:
                holds.add(id(site_fn[sid]))
                queue.append(site_fn[sid])
    return holds


def emission_status(contracts: list[dict]) -> dict[int, str]:
    """id(function) -> 'emits' | 'delegated' | 'missing' for every state-changing entry point with a body: a public or external
    function, a fallback or a receive.

    'emits': the body, a modifier named initializer/reinitializer, or an internal function it calls (the callables its own
    contract can see, followed transitively, a call counting only when every overload of its name emits) contains `emit`.
    'delegated': it emits nothing visible but calls an underscore-prefixed internal function or `super.` that this source does
    not define (inherited code may emit), under the same every-overload rule. 'missing': neither."""
    result: dict[int, str] = {}
    for contract in contracts:
        if contract["kind"] != "contract":
            continue
        index = visible_index(contract, contracts)
        universe: dict[int, dict] = {}
        for fns in index.values():
            for f in fns:
                if f["kind"] == "function":
                    universe.setdefault(id(f), f)
        for fn in contract["functions"]:
            if fn["kind"] in ENTRY_KINDS:
                universe.setdefault(id(fn), fn)
        emitting = _holding(list(universe.values()), index,
                            lambda f: bool(re.search(r"\bemit\s+" + _IDENT, f["body"]))
                            or any(m in ("initializer", "reinitializer") for m, _ in f["modifiers"]))
        delegating = _holding(list(universe.values()), index,
                              lambda f: bool(re.search(r"\bsuper\s*\.", f["body"]))
                              or any(n.startswith("_") and n not in index for n in _calls_of(f)))
        for fn in contract["functions"]:
            if not (fn["kind"] in ENTRY_KINDS and fn["visibility"] in ("public", "external") and fn["mutability"] in ("nonpayable", "payable")
                    and fn["has_body"] and fn["body"].strip()):
                continue
            result[id(fn)] = "emits" if id(fn) in emitting else "delegated" if id(fn) in delegating else "missing"
    return result


_SENDER = r"(?:msg\.sender|_msgSender\s*\(\s*\))"
_NAME = r"[A-Za-z_$][\w$]{0,63}"
# the other side of a sender comparison: a (member) name, optionally called with no arguments, or address(this); bounded so that
# a pathological run of word characters cannot make the scan quadratic
_RHS = r"(address\s*\(\s*this\s*\)|[A-Za-z_$][\w$]{0,63}(?:\.[A-Za-z_$][\w$]{0,63}){0,4}(?:\s*\(\s*\))?)(?![\w$.(])"
_LHS = r"(?<![\w$.])" + _RHS.replace("(?![\\w$.(])", "")
_AND_PREFIX = r"(?:[^;{|]{0,160}?&&\s*)?"  # `require(amount > 0 && msg.sender == owner)`: earlier conjuncts do not matter
_REQ = r"\b(?:require|assert)\s*\(\s*" + _AND_PREFIX
_REQ_END = r"(?=\s*[),&])"  # a sender check is a whole conjunct: a `||` after it leaves the other branch unguarded
_IF_REVERT = r"\)\s*\{?\s*(?:revert|return)\b"
_NOT_A_PRINCIPAL = frozenset({"tx.origin", "address(0)"})
# a sender equality in either order, the principal in group 1 (`msg.sender == X`) or group 2 (`X == msg.sender`)
_SENDER_EQ = r"(?:" + _SENDER + r"\s*==\s*" + _RHS + r"|" + _LHS + r"\s*==\s*" + _SENDER + r")"
_SENDER_EQ_RE = re.compile(_SENDER_EQ)
# a require/assert whose whole condition is two or more sender equalities joined by `||`; nothing else may precede or follow it
_SENDER_OR_CHAIN = re.compile(r"\b(?:require|assert)\s*\(\s*(" + _SENDER_EQ + r"(?:\s*\|\|\s*" + _SENDER_EQ + r")+)(?=\s*[),])")


_CONTROL_OPEN = re.compile(r"\b(?:if|for|while)\s*\(")
_HEAD_AT = re.compile(r"(?:if|for|while)\s*\(|else\b")  # a control head, or `else`, that starts a brace-less statement
_ELSE = re.compile(r"\belse\b")
_WS = re.compile(r"\s*")
_BLOCK_KEYWORD = re.compile(r"\b(?:unchecked|assembly)$")  # a block after these runs as often as the code around it
# an unconditional exit: `_;` is a modifier's placeholder, so a guard after it never precedes the function body
_TERMINATOR = re.compile(r"\b(?:return|revert|throw)\b|(?<![\w$])_\s*;")


class _Profile:
    """Control-flow facts of one body (see _body_profile): cond[i] is 1 when offset i may not run on every call; terminator is
    the offset of the first unconditional return, revert, throw or `_;` (len(body) when none); paren_match pairs each '(' with
    its ')'; or_top[open] is True when the parenthesis opened at `open` has a top-level `||` before its first comma; brace_match
    pairs each '{' with its '}'; for_spans lists (open, close, end) of each for header, where end is the end of the loop."""

    __slots__ = ("cond", "terminator", "paren_match", "or_top", "brace_match", "for_spans")

    def __init__(self, cond, terminator: int, paren_match: dict[int, int], or_top: dict[int, bool], brace_match: dict[int, int],
                 for_spans: list[tuple[int, int, int]]):
        self.cond = cond
        self.terminator = terminator
        self.paren_match = paren_match
        self.or_top = or_top
        self.brace_match = brace_match
        self.for_spans = for_spans


class _Ends:
    """Offset just past the statement that starts at a given offset of a body. A chain of brace-less control heads (`if (c)`,
    `for (...)`, `else`) is followed once and memoised, so nested heads cost one pass in all."""

    def __init__(self, body: str, paren_match: dict[int, int], brace_match: dict[int, int]):
        self.body = body
        self.paren = paren_match
        self.brace = brace_match
        self.semis = [m.start() for m in re.finditer(r";", body)]
        self.memo: dict[int, int] = {}

    def end_from(self, start: int) -> int:
        body, n = self.body, len(self.body)
        chain: list[int] = []
        j = _WS.match(body, start).end()
        while True:
            if j in self.memo:
                result = self.memo[j]
                break
            m = _HEAD_AT.match(body, j)
            if m is None:
                result = self._statement(j)
                break
            chain.append(j)
            if m.group(0) == "else":
                j = _WS.match(body, m.end()).end()
                continue
            close = self.paren.get(m.end() - 1)
            if close is None:
                result = n
                break
            j = _WS.match(body, close + 1).end()
        for head in chain:
            self.memo[head] = result
        return result

    def _statement(self, j: int) -> int:
        n = len(self.body)
        if j >= n:
            return n
        if self.body[j] == "{":
            return self.brace.get(j, n - 1) + 1
        k = bisect.bisect_left(self.semis, j)
        return self.semis[k] + 1 if k < len(self.semis) else n


def _body_profile(body: str) -> _Profile:
    """The control-flow profile of a function or modifier body, computed in one pass over its brackets and control heads.
    A block after an if, else, loop, try or catch, and a brace-less statement under one, may not run on every call; so may
    code after a return, revert or throw that sits in such a block, and the post clause of a for header, which runs after each
    iteration and maybe never (NEW-5). Blocks that always run are bare, unchecked and assembly."""
    n = len(body)
    paren_match: dict[int, int] = {}
    brace_match: dict[int, int] = {}
    or_top: dict[int, bool] = {}
    stack: list[list] = []  # [opener, offset, a top-level || seen, a comma seen] for each open bracket
    for m in re.finditer(r"[(){}\[\]]|\|\||,", body):
        token = m.group()
        if token in ("(", "[", "{"):
            stack.append([token, m.start(), False, False])
        elif token in (")", "]", "}"):
            if not stack:
                continue
            opener, start, has_or, _ = stack.pop()
            if token == ")" and opener == "(":
                paren_match[start] = m.start()
                or_top[start] = has_or
            elif token == "}" and opener == "{":
                brace_match[start] = m.start()
        elif token == "||":
            if stack and stack[-1][0] == "(" and not stack[-1][3]:
                stack[-1][2] = True
        elif stack and stack[-1][0] == "(":  # a comma
            stack[-1][3] = True
    spans: list[tuple[int, int]] = []
    for open_, close in brace_match.items():
        tail = body[max(0, open_ - 200):open_].rstrip()
        if (not tail and open_ < 200) or tail.endswith((";", "{", "}")) or _BLOCK_KEYWORD.search(tail):
            continue  # a bare block: it always runs, as the code around it does
        spans.append((open_, close + 1))
    ends = _Ends(body, paren_match, brace_match)
    for m in _CONTROL_OPEN.finditer(body):
        close = paren_match.get(m.end() - 1)
        if close is None:
            continue
        after = _WS.match(body, close + 1).end()
        if after < n and body[after] != "{":
            spans.append((after, ends.end_from(after)))
    for m in _ELSE.finditer(body):
        after = _WS.match(body, m.end()).end()
        if after < n and body[after] != "{":
            spans.append((after, ends.end_from(after)))
    for_spans: list[tuple[int, int, int]] = []
    for m in re.finditer(r"\bfor\s*\(", body):
        open_ = m.end() - 1
        close = paren_match.get(open_)
        if close is None:
            continue
        semi1 = body.find(";", open_ + 1, close)
        semi2 = body.find(";", semi1 + 1, close) if semi1 >= 0 else -1
        if semi2 >= 0:
            spans.append((semi2 + 1, close))  # the post clause: runs after each iteration, and maybe never
        after = _WS.match(body, close + 1).end()
        for_spans.append((open_, close, ends.end_from(after) if after < n else n))
    diff = [0] * (n + 1)
    for a, b in spans:
        diff[a] += 1
        diff[b] -= 1
    cond = bytearray(n)
    run = 0
    for i in range(n):
        run += diff[i]
        cond[i] = 1 if run > 0 else 0
    terminator = n
    for m in _TERMINATOR.finditer(body):
        if not cond[m.start()]:
            terminator = m.start()
            break
    return _Profile(cond, terminator, paren_match, or_top, brace_match, for_spans)


def _profile_of(fn: dict) -> _Profile:
    """The body profile of a function or modifier, computed once and kept on the dict."""
    profile = fn.get("_profile")
    if profile is None:
        profile = fn["_profile"] = _body_profile(fn["body"])
    return profile


def _guard_sites(body: str, profile: _Profile) -> list[tuple[int, int, str, str]]:
    """(pos, end, label, helper name or "") of every inline sender check in `body`, before the path and helper filters. `pos` is
    where the check's text starts (the path filters use it); `end` is where that text ends."""
    sites: list[tuple[int, int, str, str]] = []

    def single(m: re.Match, label: str, helper: str, require: bool) -> None:
        if require:  # `require(x || msg.sender == a)`: the disjunct that holds lets the call through with no sender check
            open_ = body.find("(", m.start())
            if open_ not in profile.paren_match or profile.or_top.get(open_, False):
                return
        sites.append((m.start(), m.end(), label, helper))

    for m in re.finditer(r"\b_checkOwner\s*\(\s*\)", body):
        sites.append((m.start(), m.end(), "owner", "_checkOwner"))
    for m in re.finditer(r"\b_checkRole\s*\(", body):
        close = profile.paren_match.get(m.end() - 1)
        if close is None:
            continue
        args = split_top(body[m.end():close])
        # `_checkRole(ROLE)` checks msg.sender itself (the OpenZeppelin convention); `_checkRole(ROLE, msg.sender)` checks the caller;
        # `_checkRole(ROLE, user)` with another account is not the caller's check and gets no credit (N-1)
        if len(args) == 1 or (len(args) == 2 and _SENDER_ACCOUNT.fullmatch(args[1]) is not None):
            sites.append((m.start(), close + 1, args[0], "_checkRole"))
    for lead, end, require in ((_REQ, _REQ_END, True), (r"\bif\s*\(\s*!\s*", r"\s*" + _IF_REVERT, False)):
        for m in re.finditer(lead + r"hasRole\s*\(\s*([^,]{1,120}),\s*" + _SENDER + r"\s*\)" + end, body):
            single(m, m.group(1).strip(), "hasRole", require)
        for m in re.finditer(lead + r"(" + _NAME + r")\s*\[\s*" + _SENDER + r"\s*\]" + end, body):
            if re.search(_ROLE_WORDS, m.group(1), re.I):
                single(m, f"{m.group(1)}[msg.sender]", "", require)
        for m in re.finditer(lead + r"(" + _NAME + r")\s*\(\s*" + _SENDER + r"\s*\)" + end, body):
            if m.group(1) != "hasRole" and re.search(_ROLE_WORDS, m.group(1), re.I):
                single(m, f"{m.group(1)}(msg.sender)", m.group(1), require)
    for pattern, require in ((_REQ + _SENDER + r"\s*==\s*" + _RHS + _REQ_END, True),
                             (_REQ + _LHS + r"\s*==\s*" + _SENDER + _REQ_END, True),
                             (r"\bif\s*\(\s*" + _SENDER + r"\s*!=\s*" + _RHS + r"\s*" + _IF_REVERT, False),
                             (r"\bif\s*\(\s*" + _LHS + r"\s*!=\s*" + _SENDER + r"\s*" + _IF_REVERT, False)):
        for m in re.finditer(pattern, body):
            who = re.sub(r"\s+", "", m.group(1))
            if who not in _NOT_A_PRINCIPAL:
                single(m, f"msg.sender=={who}", "", require)
    # `require(msg.sender == A || msg.sender == B)`: every disjunct is a sender check against a principal, so the chain guards each
    # of them. One non-sender term (`msg.sender == owner || open`) leaves its branch open: the chain is then no guard at all.
    for chain in _SENDER_OR_CHAIN.finditer(body):
        whos = [re.sub(r"\s+", "", p.group(1) or p.group(2)) for p in _SENDER_EQ_RE.finditer(chain.group(1))]
        if len(whos) == chain.group(1).count("||") + 1 and not any(who in _NOT_A_PRINCIPAL for who in whos):
            sites.extend((chain.start(), chain.end(), f"msg.sender=={who}", "") for who in whos)
    return sites


def _helper_checks_sender(index: dict[str, list[dict]], name: str, visiting: frozenset) -> bool:
    """Whether the helper `name` is a sender check. A helper this source defines counts only when its own body holds a sender
    check (an empty or no-op helper guards nothing); a helper defined outside this source (inherited) is taken as the library's."""
    if name in visiting:
        return False
    defs = [d for d in index.get(name, []) if d["kind"] == "function"]
    if not defs:
        return True
    for d in defs:  # a call may resolve to any overload of the name: every one of them must check the sender
        cached = d.get("_sender_check")
        if cached is None:
            cached = d["_sender_check"] = bool(_guard_labels(d["body"], index, _profile_of(d), visiting | {name}))
        if not cached:
            return False
    return True


def _guard_labels(body: str, index: dict[str, list[dict]] | None, profile: _Profile,
                   visiting: frozenset = frozenset()) -> list[tuple[int, int, str]]:
    """(pos, end, label) of the sender checks that hold on every path through `body`. A check inside an if, loop or other conditional
    body guards nothing after it; nothing after an unconditional return, revert, throw or `_;` runs. A helper check (_checkOwner(),
    _checkRole(X), hasRole(X, msg.sender), name(msg.sender)) counts only when the source's own helper checks the sender."""
    found: list[tuple[int, int, str]] = []
    for pos, end, label, helper in _guard_sites(body, profile):
        if pos >= profile.terminator or profile.cond[pos]:
            continue
        if helper and index is not None and not _helper_checks_sender(index, helper, visiting):
            continue
        found.append((pos, end, label))
    return found


def _unconditional_calls(fn: dict) -> list[tuple[str, int, int]]:
    """(name, offset, offset of its '(') of each same-source call that `fn` makes at a point that always runs; cached on the
    function. Every call site is kept: overloads of one name may be resolved differently at each site (see _targets)."""
    cached = fn.get("_uncalls")
    if cached is None:
        profile = _profile_of(fn)
        cached = fn["_uncalls"] = [
            (m.group(1), m.start(), m.end() - 1) for m in _CALL.finditer(fn["body"])
            if m.group(1) not in _NOT_CALLS and m.start() < profile.terminator and not profile.cond[m.start()]]
    return cached


_SENDER_AWARE = re.compile(r"msg\.sender|_msgSender\s*\(|\b_?check\w*\s*\(|\b_only\w*\s*\(|\bhasRole\b|\bisOwner\b|\bowner\s*\(|\bauth\w*\s*\(")


_PLACEHOLDER = re.compile(r"(?<![\w$])_\s*;")  # a modifier's placeholder: the function body runs there
_MODIFIER_UNIT = 1 << 24  # offsets of one applied modifier's own text; the modifiers of a chain occupy consecutive units before the body
_POST = 1 << 40  # a write after a modifier's `_;`: it runs after the function body, so any guard before the body covers it
_ASSEMBLY_OPEN = re.compile(r"\bassembly\s*(?:\"[^\"]*\"\s*)?\{")
_GUARD_HELPERS = frozenset({"hasRole", "_checkOwner", "_checkRole", "_msgSender", "isOwner"})
_ELEMENTARY_CALL = re.compile(r"(?:u?int\d*|bytes\d*|address|bool|string|byte|u?fixed\d*)\Z")
_ASSIGN = re.compile(r"(?<![=!<>])(?:<<|>>|[-+*/%&|^:])?=(?![=>])")  # `=`, compound assignments and Yul `:=`; not `==`, `!=`, `<=`, `>=`, `=>`
_INCDEC = re.compile(r"\+\+|--")
_DELETE = re.compile(r"\bdelete\s+([A-Za-z_$][\w$]*)")
_PUSH_POP = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)(?:\s*\.\s*[A-Za-z_$][\w$]*|\s*\[[^\[\]\n]{0,160}\])*\s*\.\s*(?:push|pop)\s*\(")
_SSTORE = re.compile(r"(?<![\w$.])sstore\s*\(")
_LHS_TAIL = re.compile(r"([A-Za-z_$][\w$]*)(?:\s*\[[^\[\]]*\]|\s*\.\s*[A-Za-z_$][\w$]*)*\s*$")  # the root of an assigned place
_IDENT_AT = re.compile(r"[A-Za-z_$][\w$]*")
# a declaration: a type, an optional `payable` and array brackets, an optional `storage`/`memory`/`calldata`, then the name, followed
# by `=`, `,`, `;` or `)`. Any identifier can match the type; `_is_type` keeps only real type names.
_DECL = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)(?:\s+payable)?"
                   r"(?:\s*\[[^\]\n]{0,40}\])*\s+(memory|storage|calldata)?\s*([A-Za-z_$][\w$]*)(?=\s*(?:=(?![=>])|[,;)]))")
_ELEMENTARY_TYPE = re.compile(r"(?:address|bool|string|bytes\d*|u?int\d*|byte|u?fixed\d*)\Z")
_TYPE_DECL = re.compile(r"\b(?:struct|enum|contract|interface|library|type)\s+([A-Za-z_$][\w$]*)")  # the type names a source declares


def _is_type(name: str, declared: frozenset) -> bool:
    """Whether `name` is a type: an elementary type, an upper-case name (an imported contract or library), or a struct, enum,
    contract or other type the source declares (a lower-case name counts only when the source declares it, D-D)."""
    head = name.split(".")[0]
    return bool(_ELEMENTARY_TYPE.match(name)) or head[:1].isupper() or name in declared or head in declared
_YUL_LET = re.compile(r"\blet\s+([A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)")
_NAME_AT = re.compile(r"[A-Za-z_$][\w$]*")


def _placeholder(body: str) -> int:
    """Offset of a modifier's `_;` placeholder; 0 when it has none, so nothing before it counts as a check (fail closed)."""
    m = _PLACEHOLDER.search(body)
    return m.start() if m else 0


def _chain_key(k: int, n: int, off: int) -> int:
    """Position of offset `off` in the text of applied modifier k of n: the modifiers run in order, all before the body."""
    return (k - n) * _MODIFIER_UNIT + off


def _per_index(fn: dict, key: str, index: dict[str, list[dict]], compute):
    """`compute()` kept on the function per index object: the same function reached through another contract may resolve its names
    differently."""
    entries = fn.setdefault("_per_index", {}).setdefault(key, [])
    for held, value in entries:
        if held is index:
            return value
    value = compute()
    entries.append((index, value))
    return value


def _modifier_site(name: str, args: str, index: dict[str, list[dict]]) -> tuple[int, str] | None:
    """(offset in the modifier's text, label) of the access check that applied modifier `name` makes, or None. A check counts only
    when it runs before the modifier's `_;`: the code after it runs after the function body (NEW-4). A modifier this source does not
    define is taken as access control at offset 0 when its name says so."""
    defined = [t for t in index.get(name, []) if t["kind"] == "modifier"]
    named = name in _ACCESS_NAMES or (name.startswith("only") and name not in _NON_ACCESS_ONLY)
    label = args.strip() if name == "onlyRole" and args.strip() else name
    if not defined:
        return (0, label) if named else None
    best: int | None = None
    for t in defined:
        ph = _placeholder(t["body"])
        for pos, _, _ in _guard_labels(t["body"], index, _profile_of(t)):
            if pos < ph and (best is None or pos < best):
                best = pos
        if best is None and named:
            m = _SENDER_AWARE.search(t["body"], 0, ph)
            if m:
                best = m.start()
    return None if best is None else (best, label)


def _own_guards(fn: dict, index: dict[str, list[dict]]) -> list[tuple[int, int, str]]:
    """(pos, end, label) of the checks fn makes itself: its body's unconditional checks, and its applied access modifiers at their
    place in the modifier run (see _chain_key)."""

    def compute() -> list[tuple[int, int, str]]:
        out = list(_guard_labels(fn["body"], index, _profile_of(fn)))
        n = len(fn["modifiers"])
        for k, (name, args) in enumerate(fn["modifiers"]):
            site = _modifier_site(name, args, index)
            if site is not None:
                key = _chain_key(k, n, site[0])
                out.append((key, key, site[1]))
        return out

    return _per_index(fn, "own_guards", index, compute)


def _scope_ends(body: str, offsets: list[int], profile: _Profile) -> list[int]:
    """For each offset, the end of its scope: the `}` that closes the innermost block holding it, the end of the body at top level,
    or, inside a for header, the end of that loop statement."""
    n = len(body)
    events = [(o, 0, o) for o in profile.brace_match]
    events += [(c, 1, o) for o, c in profile.brace_match.items()]
    events += [(d, 2, i) for i, d in enumerate(offsets)]
    events.sort()
    stack: list[int] = []
    holder = [-1] * len(offsets)
    for _, kind, val in events:
        if kind == 0:
            stack.append(val)
        elif kind == 1:
            if stack and stack[-1] == val:
                stack.pop()
        else:
            holder[val] = stack[-1] if stack else -1
    starts = [span[0] for span in profile.for_spans]
    ends: list[int] = []
    for i, d in enumerate(offsets):
        end = profile.brace_match[holder[i]] if holder[i] >= 0 else n
        j = bisect.bisect_right(starts, d) - 1
        if j >= 0 and d < profile.for_spans[j][1]:
            end = profile.for_spans[j][2]
        ends.append(end)
    return ends


def _scoped_locals(fn: dict) -> dict[str, list[tuple[int, int]]]:
    """name -> [(declared offset, end of its scope)] for the locals of fn that are not storage pointers: parameters and named returns
    (the whole body), Solidity declarations and Yul `let`s, each visible to the end of the block that holds it (NEW-2, NEW-3, NEW-9)."""
    cached = fn.get("_scoped")
    if cached is not None:
        return cached
    body = fn["body"]
    n = len(body)
    scoped: dict[str, list[tuple[int, int]]] = {}
    for _, name in list(fn["params"]) + list(fn["returns"]):
        if name:
            scoped.setdefault(name, []).append((-1, n + 1))
    decls: list[tuple[str, int]] = []
    declared = fn.get("types", frozenset())
    for m in _DECL.finditer(body):
        if m.group(2) != "storage" and _is_type(m.group(1), declared):
            decls.append((m.group(3), m.start(3)))
    for m in _YUL_LET.finditer(body):
        for nm in _NAME_AT.finditer(m.group(1)):
            decls.append((nm.group(0), m.start(1) + nm.start()))
    for (name, d), end in zip(decls, _scope_ends(body, [d for _, d in decls], _profile_of(fn))):
        scoped.setdefault(name, []).append((d, end))
    fn["_scoped"] = scoped
    return scoped


def _is_local(scoped: dict[str, list[tuple[int, int]]], name: str, p: int) -> bool:
    """Whether `name` names a local at offset `p`: some declaration of it is before `p` and its scope has not ended."""
    return any(d < p < e for d, e in scoped.get(name, ()))


def _assigned_roots(body: str, end: int) -> list[tuple[str, int | None]] | None:
    """The (root name, offset) of each place that the assignment operator at `end` writes: one for `x[k].f =`, one per bare
    name of a tuple `(a, b) =`; a declaration is no place. None when the target has no recognisable root (fail closed)."""
    window_start = max(0, end - 200)
    window = body[window_start:end].rstrip()
    if window.endswith(")"):
        open_ = window.rfind("(")
        if open_ < 0:
            return None
        roots: list[tuple[str, int | None]] = []
        for part in window[open_ + 1:-1].split(","):
            tokens = part.split()
            if len(tokens) >= 2:  # `bool ok`: a declaration
                continue
            if not tokens:
                continue
            m = _LHS_TAIL.search(tokens[0])
            if m is None:
                return None
            roots.append((m.group(1), None))
        return roots
    m = _LHS_TAIL.search(window)
    if m is None:
        return None
    return [(m.group(1), window_start + m.start(1))]


def _incdec_root(body: str, m: re.Match) -> str | None:
    """The variable that `++` or `--` changes: the name after a prefix operator, else the place before a postfix one."""
    ahead = _IDENT_AT.match(body, _WS.match(body, m.end()).end())
    if ahead:
        return ahead.group(0)
    before = _LHS_TAIL.search(body[max(0, m.start() - 200):m.start()].rstrip())
    return before.group(1) if before else None


def _direct_writes(fn: dict) -> list[int]:
    """Offsets in fn's own text that write state: assignments, compound assignments and Yul `:=`, `++`, `--`, `delete`, `push`,
    `pop` and `sstore`. A place is not state when a visible local of that name holds it; an assignment whose target has no
    recognisable root counts as a write (fail closed)."""
    cached = fn.get("_writes")
    if cached is None:
        body = fn["body"]
        scoped = _scoped_locals(fn)
        found: list[int] = []
        for m in _ASSIGN.finditer(body):
            roots = _assigned_roots(body, m.start())
            if roots is None or any(not _is_local(scoped, name, m.start()) for name, _ in roots):
                found.append(m.start())
        found.extend(m.start() for m in _DELETE.finditer(body) if not _is_local(scoped, m.group(1), m.start()))
        found.extend(m.start() for m in _PUSH_POP.finditer(body) if not _is_local(scoped, m.group(1), m.start()))
        for m in _INCDEC.finditer(body):
            root = _incdec_root(body, m)
            if root is None or not _is_local(scoped, root, m.start()):
                found.append(m.start())
        found.extend(m.start() for m in _SSTORE.finditer(body))
        cached = fn["_writes"] = sorted(found)
    return cached


def _own_writes(fn: dict, index: dict[str, list[dict]]) -> list[int]:
    """Positions of the state writes of fn: its own, and those of its applied modifiers, placed in the modifier run before the body
    (before their `_;`) or after the body (after it)."""

    def compute() -> list[int]:
        writes = list(_direct_writes(fn))
        n = len(fn["modifiers"])
        for k, (name, _) in enumerate(fn["modifiers"]):
            for t in index.get(name, []):
                if t["kind"] != "modifier":
                    continue
                ph = _placeholder(t["body"])
                writes.extend(_chain_key(k, n, w) if w < ph else _POST for w in _direct_writes(t))
        return writes

    return _per_index(fn, "own_writes", index, compute)


_NO_GUARD = (sys.maxsize, sys.maxsize)


def _candidates(index: dict[str, list[dict]], name: str) -> list[dict]:
    """The same-source functions a call of `name` may resolve to: every overload of that name visible to the contract."""
    return [t for t in index.get(name, []) if t["kind"] == "function"]


_IDENT_ONLY = re.compile(r"[A-Za-z_$][\w$]*")


def _canon(text: str) -> str:
    """A type as solc spells it in a signature: `uint` is `uint256`, `int` is `int256`, `byte` is `bytes1`."""
    return _TYPE_SPELLING_RE.sub(lambda m: _TYPE_SPELLING[m.group(1)], text)


def _arg_types(fn: dict, args: list[str]) -> list[str] | None:
    """The declared type of each argument when every one is a parameter of `fn` that no local of `fn` shadows; None otherwise (a
    literal, an expression, a state variable or a local: the type is not known here, so the call is not resolved)."""
    scoped = _scoped_locals(fn)
    declared = {name: t for t, name in fn["params"] if name}
    types: list[str] = []
    for arg in args:
        if not _IDENT_ONLY.fullmatch(arg) or arg not in declared or "payable" in declared[arg]:
            return None
        if any(d >= 0 for d, _ in scoped.get(arg, ())):
            return None  # a local of the same name: its type is not the parameter's
        types.append(_canon(declared[arg]))
    return types


def _targets(fn: dict, index: dict[str, list[dict]], name: str, open_: int) -> list[dict]:
    """The same-source functions the call of `name` whose '(' is at `open_` in fn's body may resolve to. When the argument types
    are known and exactly one overload has exactly those parameter types, that overload is the target (solc prefers an exact
    match); otherwise every overload of the name is a possible target, and a call credits a check or an emit only when all of them
    give it."""
    cands = _candidates(index, name)
    if len(cands) < 2:
        return cands
    body = fn["body"]
    close = match_close(body, open_)
    if close < 0:
        return cands
    types = _arg_types(fn, split_top(body[open_ + 1:close]))
    if types is None:
        return cands
    exact = [t for t in cands if [_canon(p) for p, _ in t["params"]] == types]
    return exact if len(exact) == 1 else cands


def _first(fn: dict, index: dict[str, list[dict]], memo: dict) -> tuple[int, int]:
    """(pos, end) of the first check that every run of `fn` certainly executes: its own (see _own_guards), or an unconditional call
    to a same-source function that certainly executes one, placed at the call. _NO_GUARD when there is none."""
    key = ("first", id(fn))
    if key in memo:
        return memo[key]
    memo[key] = _NO_GUARD  # a cycle contributes no guard of its own
    best = min(((pos, end) for pos, end, _ in _own_guards(fn, index)), default=_NO_GUARD)
    for name, pos, open_ in _unconditional_calls(fn):
        targets = _targets(fn, index, name, open_)
        if pos < best[0] and targets and all(_first(t, index, memo)[0] < sys.maxsize for t in targets):
            best = (pos, pos + 1)
    memo[key] = best
    return best


def _covered(fn: dict, entered: bool, index: dict[str, list[dict]], memo: dict) -> bool:
    """Whether every write reachable from `fn` comes after a check on its path. `entered` says a check certainly ran before the call
    into `fn`. A write is covered when such a check ran before it: on entry, or earlier in `fn` (see _first). A call passes that state
    on: the callee is entered checked when `entered`, or when a check certainly ran before the call."""
    key = ("covered", id(fn), entered)
    if key in memo:
        return memo[key]
    memo[key] = True  # a re-entry into the same state is judged by the frame that is already judging it
    first = _first(fn, index, memo)[0]
    ok = all(entered or first < w for w in _own_writes(fn, index))
    if ok:
        for m in _CALL.finditer(fn["body"]):
            name = m.group(1)
            if name in _NOT_CALLS:
                continue
            entry = entered or first < m.start()
            if any(not _covered(t, entry, index, memo) for t in _targets(fn, index, name, m.end() - 1)):
                ok = False
                break
    memo[key] = ok
    return ok


def _certain(fn: dict, index: dict[str, list[dict]], memo: dict) -> set[str]:
    """Labels of the checks that certainly run on every path through `fn`: its own, and those of the same-source functions it calls
    unconditionally. A check in a conditional call is not certain (NEW-7)."""
    key = ("certain", id(fn))
    if key in memo:
        return memo[key]
    memo[key] = set()
    labels = {label for _, _, label in _own_guards(fn, index)}
    for name, _, open_ in _unconditional_calls(fn):
        targets = _targets(fn, index, name, open_)
        if targets:  # the call resolves to one of these: only a check every one of them makes is certain
            labels |= set.intersection(*(_certain(t, index, memo) for t in targets))
    memo[key] = labels
    return labels


def guards(fn: dict, index: dict[str, list[dict]]) -> list[str]:
    """Sorted access-control labels that certainly protect `fn`, or [] when some write `fn` makes is not preceded by a check on its
    path. Access modifiers (onlyRole(X) -> X, onlyOwner, auth, restricted, or a modifier defined in this source that checks the
    sender before its `_;`) and inline checks (_checkOwner, _checkRole(X), hasRole(X, msg.sender), msg.sender == X, allow-list[
    msg.sender]) count when they run on every path. A check after a write guards nothing for that write (write order). A check in a
    same-source callee counts only through an unconditional call. tx.origin never counts; initializer / nonReentrant /
    whenNotPaused / onlyProxy are not access control."""
    memo: dict = {}
    if not _covered(fn, False, index, memo):
        return []
    return sorted(_certain(fn, index, memo))


def _assembly_spans(body: str, profile: _Profile) -> list[tuple[int, int]]:
    """(open, close) offsets of each `assembly { }` block of a body: its calls are EVM opcodes, not functions of this source."""
    spans: list[tuple[int, int]] = []
    for m in _ASSEMBLY_OPEN.finditer(body):
        open_ = m.end() - 1
        if open_ in profile.brace_match:
            spans.append((open_, profile.brace_match[open_]))
    return spans


_SENDER_ACCOUNT = re.compile(r"msg\.sender|_msgSender\s*\(\s*\)")  # an account argument that is the caller


def _helper_exempt(name: str, body: str, open_: int, pairs: dict[int, int], guard_starts: set[int], p: int) -> bool:
    """Whether the call `name(` at `p` is not unverified: it is the check itself (it starts a recognised guard), or it is a guard
    helper whose account is msg.sender (its last argument) or that takes no argument. `hasRole(ROLE, user)` is judged (D-C)."""
    if p in guard_starts:
        return True
    if name not in _GUARD_HELPERS:
        return False
    close = pairs.get(open_)
    if close is None:
        return False
    parts = split_top(body[open_ + 1:close])
    return not parts or _SENDER_ACCOUNT.fullmatch(parts[-1]) is not None


def _unverified_names(body: str, profile: _Profile, limit: int, index: dict[str, list[dict]], declared: frozenset) -> list[str]:
    """Names of the unqualified calls of `body` that start before `limit` and name no function this source defines, no declared
    struct or contract (a constructor, D-D), no builtin, elementary type or upper-case name (a type conversion), and no call inside
    assembly."""
    spans = _assembly_spans(body, profile)
    pairs = _paren_pairs(body)
    guard_starts = {pos for pos, _, _ in _guard_labels(body, index, profile)}
    names: list[str] = []
    for m in _CALL.finditer(body):
        p = m.start()
        if p >= limit:
            break
        name = m.group(1)
        if name in _NOT_CALLS or _ELEMENTARY_CALL.match(name) or name[0].isupper() or name in declared:
            continue
        if any(t["kind"] == "function" for t in index.get(name, [])):
            continue
        if any(o < p < c for o, c in spans):
            continue
        if _helper_exempt(name, body, m.end() - 1, pairs, guard_starts, p):
            continue
        if name not in names:
            names.append(name)
    return names


def unverified_calls(fn: dict, index: dict[str, list[dict]]) -> list[str]:
    """Names of unqualified calls that run before the first check of `fn` (all calls when no check certainly runs) and that name no
    function this source defines. Such a callee is unverified: the check cannot be said to precede its effects. An applied modifier
    the source defines is judged the same way up to its first check or its `_;`, since its calls run before the body. Once an
    earlier applied modifier has checked the sender, the later modifiers are no longer judged. Not counted: builtins, type
    conversions (upper-case names, elementary types, declared struct and contract names), calls inside assembly, a call on
    msg.sender as a receiver, a named guard helper called with no argument or with msg.sender or _msgSender() as its last
    argument, and a one-argument _checkRole(ROLE), which is exempt as a recognised guard on the OpenZeppelin convention since
    that helper checks msg.sender itself (hasRole(msg.sender, ROLE) is judged)."""
    declared = fn.get("types", frozenset())
    names = _unverified_names(fn["body"], _profile_of(fn), _first(fn, index, {})[1], index, declared)
    checked = False  # an earlier applied modifier checks the sender: the later modifiers and the body run after that guard
    for name, args in fn["modifiers"]:
        if checked:
            break
        for t in index.get(name, []):
            if t["kind"] != "modifier":
                continue
            profile = _profile_of(t)
            ph = _placeholder(t["body"])
            limit = min([ph] + [end for pos, end, _ in _guard_labels(t["body"], index, profile) if pos < ph])
            for found in _unverified_names(t["body"], profile, limit, index, t.get("types", declared)):
                if found not in names:
                    names.append(found)
        if _modifier_site(name, args, index) is not None:
            checked = True
    return names


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
    r"|(?<![A-Za-z0-9+/=_-])(?=[A-Za-z0-9+/=_-]*\d)[A-Za-z0-9+/=_-]{32,}|(['\"])(?:[a-z]{3,8} ){11,}[a-z]{3,8}\1")


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

_PRAGMA_KEYWORD = re.compile(r"\bpragma\s+solidity\s*")
_LOW_LEVEL = re.compile(r"\.(call|delegatecall|staticcall|send)\s*(?:\{[^}]{0,400}\})?\s*\(")
_LAYOUT_AT = re.compile(r"\blayout\s+at\b")


def _pragma_directives(clean: str) -> list[tuple[int, str]]:
    """(offset, text) of each `pragma solidity <text>;` directive in the stripped source, in order. Linear in the source size:
    each directive's terminating ';' is found once and the next search resumes after it, so the scanned spans never overlap.
    A keyword with no ';' after it ends the scan, because no later keyword can have one either: that tail is one unterminated
    pragma, which is not a directive (a text over the length bound of a terminated one is still reported as INVALID)."""
    directives: list[tuple[int, str]] = []
    pos = 0
    while (keyword := _PRAGMA_KEYWORD.search(clean, pos)) is not None:
        end = clean.find(";", keyword.end())
        if end < 0:
            break
        directives.append((keyword.start(), clean[keyword.end():end]))
        pos = end + 1
    return directives


_CONTROL_HEAD = re.compile(r"\s*(?:else\b|(?:if|for|while)\s*\()")


def _paren_pairs(clean: str) -> dict[int, int]:
    """Offset of each '(' mapped to its ')', by one stack pass; a bracket that never closes has no entry."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for m in re.finditer(r"[()]", clean):
        if m.group() == "(":
            stack.append(m.start())
        elif stack:
            pairs[stack.pop()] = m.start()
    return pairs


class _CallSites:
    """The statement context of every low-level call in one source. A call's statement starts after the nearest `{`, `}` or `;`
    that is outside parentheses before it: the semicolons of a for header do not end a statement (NEW-6). Found by binary search
    over the sorted separators, so each test costs O(log n) however long the statement."""

    def __init__(self, clean: str):
        self.clean = clean
        self.paren = _paren_pairs(clean)
        expression = self._expression_braces()  # a brace inside a matched parenthesis is part of an expression, not a block
        self.seps: list[int] = []
        depth = 0
        for m in re.finditer(r"[(){};]", clean):
            c = m.group()
            if m.start() in expression:
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth = max(depth - 1, 0)
            elif c in "{}":
                depth = 0  # a brace ends any open parenthesis, so an unmatched '(' cannot merge the statements after it
                self.seps.append(m.start())
            elif depth == 0:
                self.seps.append(m.start())
        self.keywords = [m.start() for m in re.finditer(r"\b(?:require|assert|if|return)\b", clean)]
        self.assigns = [m.start() for m in re.finditer(r"(?<![=!<>])=(?![=>])", clean)]
        # the block structure: each matched '{' with its '}', and each '{' with the '{' that encloses it
        self.brace_close: dict[int, int] = {}
        self.enclosing: dict[int, int] = {}
        stack: list[int] = []
        for m in re.finditer(r"[{}]", clean):
            if m.start() in expression:
                continue
            if m.group() == "{":
                self.enclosing[m.start()] = stack[-1] if stack else -1
                stack.append(m.start())
            elif stack:
                self.brace_close[stack.pop()] = m.start()
        self.opens = sorted(self.brace_close)
        self.lowlevel = [m.start() for m in _LOW_LEVEL.finditer(clean)]
        self._verdicts: dict[int, bool] | None = None
        self._uses: dict[str, list[int]] | None = None
        # every '(' with the '(' that encloses it (one stack pass, as _paren_pairs), and the check heads: require/assert/if with the
        # offset of their '(', and return; both sorted, so a flag read finds its statement's heads by binary search
        self.opens_all: list[int] = []
        self.parent: dict[int, int] = {}
        paren_stack: list[int] = []
        for m in re.finditer(r"[()]", clean):
            if m.group() == "(":
                self.parent[m.start()] = paren_stack[-1] if paren_stack else -1
                self.opens_all.append(m.start())
                paren_stack.append(m.start())
            elif paren_stack:
                paren_stack.pop()
        self.heads: list[tuple[int, str, int]] = [
            (m.start(), m.group(1) or "return", m.end() - 1 if m.group(1) else -1)
            for m in _HEAD_AT.finditer(clean)]
        self._head_starts = [h[0] for h in self.heads]

    def _expression_braces(self) -> set[int]:
        """The '{' and '}' offsets that sit inside a matched parenthesis: part of an expression such as the struct literal in
        S({a: 1}), so they open no block and end no statement."""
        events = sorted([(o, 1) for o in self.paren] + [(c, -1) for c in self.paren.values()])
        out: set[int] = set()
        depth = k = 0
        for m in re.finditer(r"[{}]", self.clean):
            while k < len(events) and events[k][0] < m.start():
                depth += events[k][1]
                k += 1
            if depth > 0:
                out.add(m.start())
        return out

    def _scope_close(self, pos: int) -> int:
        """Offset of the '}' closing the innermost block that holds `pos`: the scope of a local bound there (the end of the source
        when none holds it)."""
        i = bisect.bisect_left(self.opens, pos) - 1
        o = self.opens[i] if i >= 0 else -1
        while o >= 0 and (o not in self.brace_close or self.brace_close[o] < pos):
            o = self.enclosing[o]
        return self.brace_close[o] if o >= 0 else len(self.clean)

    def _branch_end(self, i: int) -> int:
        """Offset just past the statement or block that starts at `i`."""
        clean = self.clean
        if clean[i:i + 1] == "{":
            return self.brace_close.get(i, len(clean) - 1) + 1
        k = clean.find(";", i)
        return len(clean) if k < 0 else k + 1

    def _branch_exits(self, i: int) -> bool:
        """Whether the statement or block that starts at `i` leaves the call: a revert, return or throw that is one of its top-level
        statements (a brace-less branch is that statement). A statement inside a nested if or block is not reached on every path."""
        clean = self.clean
        j = _WS.match(clean, i).end()
        if clean[j:j + 1] == "{":
            close = self.brace_close.get(j)
            return close is not None and self._block_exits(j + 1, close)
        return _REVERT_OR_RETURN.match(clean, j) is not None

    def _block_exits(self, start: int, end: int) -> bool:
        """Whether one statement directly in the block [start, end) is a revert, return or throw. Nested blocks are jumped over whole:
        the statements inside an if or a loop are not reached on every path of the branch."""
        clean = self.clean
        k = bisect.bisect_left(self.seps, start)
        stmt = start
        while k < len(self.seps) and self.seps[k] < end:
            q = self.seps[k]
            if clean[q] == "{":
                close = self.brace_close.get(q)
                if close is None:
                    return False
                stmt = close + 1
                k = bisect.bisect_left(self.seps, close + 1)
                continue
            if clean[q] == ";":
                head = _WS.match(clean, stmt).end()
                if head < q and _REVERT_OR_RETURN.match(clean, head) is not None:
                    return True
            stmt = q + 1
            k += 1
        return False

    def _is_check_read(self, flag: str, p: int) -> bool:
        """Whether the read of `flag` at `p` checks the call that bound it: a require or assert argument, a return expression, or an
        if whose condition is exactly the flag or its negation and whose failure path reverts or returns (D-B). A read that only
        stores, logs or assigns the flag is not a check."""
        clean = self.clean
        i = bisect.bisect_left(self.seps, p)
        s = self.seps[i - 1] + 1 if i else 0
        for k in range(bisect.bisect_left(self._head_starts, s), len(self.heads)):
            start, kind, open_ = self.heads[k]
            if start >= p:
                break
            if kind == "return":
                return True  # a return expression before the read in this statement
            if open_ >= p:
                continue  # the head's parenthesis opens after the read: the read is not in it
            close = self.paren.get(open_)
            if close is None or close <= p:
                continue  # the read is not in this condition
            if kind != "if":
                return True  # the read is an argument of a require or assert
            cond = clean[open_ + 1:close].strip()
            then_start = _WS.match(clean, close + 1).end()
            escaped = re.escape(flag)
            if re.fullmatch(r"!\s*\(?\s*" + escaped + r"\s*\)?", cond):
                if self._branch_exits(then_start):
                    return True  # `if (!ok) revert ...`: the then-branch is the failure path
            elif re.fullmatch(r"\(?\s*" + escaped + r"\s*\)?", cond):
                else_at = _WS.match(clean, self._branch_end(then_start)).end()
                if re.match(r"else\b", clean[else_at:else_at + 5]) and self._branch_exits(else_at + 4):
                    return True  # `if (ok) ... else revert ...`: the else-branch is the failure path
        return False

    def _is_tuple_target(self, p: int) -> bool:
        """Whether the name at `p` is a slot of a tuple assignment target, `(x, name) = ...`: the nearest '(' around it, in its
        statement, closes before an assignment operator (a comparison does not count)."""
        clean = self.clean
        i = bisect.bisect_left(self.seps, p)
        s = self.seps[i - 1] + 1 if i else 0
        k = bisect.bisect_left(self.opens_all, p) - 1  # the nearest '(' before the name
        o = self.opens_all[k] if k >= 0 else -1
        while o >= s:  # the '(' that enclose the name in its statement, innermost first
            close = self.paren.get(o)
            if close is None:
                return False  # a '(' that never closes encloses the name: no tuple target
            if close > p:
                return _ASSIGN_AFTER.match(clean, close + 1) is not None
            o = self.parent[o]
        return False

    def _first_check(self, flag: str, start: int, limit: int) -> int | None:
        """Offset of the first read of `flag` after `start`, before `limit` (the end of its scope), that checks the call. A read that
        only stores, logs or tests something else is passed over (`emit E(ok); require(ok);` checks the call). None when the name is
        reassigned or redeclared before any check reads it, or when the scope holds no check (D-A)."""
        clean = self.clean
        uses = self.name_uses().get(flag, [])  # sorted offsets of the whole-identifier uses of the flag, indexed once per source
        for i in range(bisect.bisect_left(uses, start), len(uses)):
            p = uses[i]
            if p + len(flag) > limit:
                break
            if _ASSIGN_AFTER.match(clean, p + len(flag)) or self._is_tuple_target(p) or _DECL_BEFORE.search(clean, max(0, p - 64), p):
                return None  # the name is overwritten or redeclared before a check reads it
            if self._is_check_read(flag, p):
                return p
        return None

    def name_uses(self) -> dict[str, list[int]]:
        """Offsets of every identifier token of the source, by name; built on first use, one pass."""
        if self._uses is None:
            self._uses = _identifier_positions(self.clean)
        return self._uses

    def _judged(self, pos: int, nearest_unchecked: int | None) -> bool:
        """Whether the low-level call at `pos` is tested: within its statement before the call, a require, assert, if or return
        appears, or the call sits in the condition of an if, for or while; or its success flag is bound and a check of that flag
        (see _first_check) comes before `nearest_unchecked`, the earliest unchecked low-level call after `pos`. A check after an
        unchecked call does not count for the earlier call. A bare body (`if (c) a.call(...)`) is not a test: the leading control
        heads of the statement are skipped first."""
        clean = self.clean
        i = bisect.bisect_left(self.seps, pos)
        s = self.seps[i - 1] + 1 if i else 0
        while head := _CONTROL_HEAD.match(clean, s, pos):
            if not head.group(0).endswith("("):
                s = head.end()
                continue
            close = self.paren.get(head.end() - 1)
            if close is None or close >= pos:
                return True  # the call is inside the head's condition
            s = close + 1
        k = bisect.bisect_left(self.keywords, s)
        if k < len(self.keywords) and self.keywords[k] < pos:
            return True
        j = bisect.bisect_left(self.assigns, pos) - 1
        if j >= 0 and self.assigns[j] >= s:
            lhs = clean[max(s, self.assigns[j] - _BINDING_CAP):self.assigns[j]]
            limit = self._scope_close(pos)
            for flag in _bound_flags(lhs):
                p = self._first_check(flag, pos + 1, limit)
                if p is not None and (nearest_unchecked is None or nearest_unchecked > p):
                    return True
        return False

    def verdicts(self) -> dict[int, bool]:
        """Whether each low-level call is checked, keyed by its offset. Calls are judged last to first, so a later call's verdict is
        known when an earlier one is judged: a check of a call's flag counts only when no unchecked low-level call lies between the
        call and that check. Independent calls that are each checked by a later test are all checked; a flag checked only after a
        later call that is itself unchecked is not (D-A)."""
        if self._verdicts is None:
            verdict: dict[int, bool] = {}
            nearest: int | None = None  # the earliest unchecked call judged so far, which lies after the calls still to judge
            for pos in reversed(self.lowlevel):
                verdict[pos] = self._judged(pos, nearest)
                if not verdict[pos]:
                    nearest = pos
            self._verdicts = verdict
        return self._verdicts



_ASM_DELEGATECALL = re.compile(r"(?<![\w$.])delegatecall\s*\(")  # inline assembly: `delegatecall(gas(), target, ...)`
_ORIGIN = re.compile(r"\btx\s*\.\s*origin\b")
_OPERAND = r"(?:address\s*\([^()]*\)|0[xX][0-9a-fA-F]*|\d+|[A-Za-z_$][\w$.]*(?:\s*\(\s*\))?)"
_OPERAND_AFTER = re.compile(r"\s*[!=]=\s*(" + _OPERAND + r")")
_OPERAND_BEFORE = re.compile(r"(" + _OPERAND + r")\s*[!=]=\s*$")


def _is_principal(operand: str) -> bool:
    """A named account or role getter: not a literal and not an address(...) cast such as address(0)."""
    return bool(re.match(r"[A-Za-z_$]", operand)) and not operand.startswith("address")


def _compared_with_principal(clean: str, start: int, end: int) -> bool:
    """clean[start:end] is compared with `==` or `!=` to a named principal on either side."""
    after = _OPERAND_AFTER.match(clean, end)
    if after and _is_principal(after.group(1)):
        return True
    window = clean[max(0, start - 96):start].rstrip()
    before = _OPERAND_BEFORE.search(window) if window.endswith(("==", "!=")) else None  # most windows are not comparisons: skip the search
    return bool(before and _is_principal(before.group(1)))


def _alias_scope(function_at, name: str, pos: int) -> tuple[int, int] | None:
    """The source span in which the local `name` that holds tx.origin at `pos` is visible: from its declaration to the end of its
    block. None when `name` is no local of the function around `pos` (a state variable or an unknown name): every use counts."""
    fn = function_at(pos)
    if fn is None:
        return None
    lo = fn["body_span"][0]
    spans = [(d, e) for d, e in _scoped_locals(fn).get(name, ()) if d < pos - lo < e]
    if not spans:
        return None
    d, e = max(spans)  # the innermost declaration that holds `pos`
    return lo + d, lo + e


def _origin_is_authorization(clean: str, origin: re.Match, positions: dict[str, list[int]], compared: dict[str, list[int]],
                             function_at) -> bool:
    """tx.origin compared with a principal, directly or through a local name that holds it (`address o = tx.origin;` then
    `require(o == owner)`). Only the uses of that name inside its scope count: a same-named variable of another function is a
    different variable. `positions` is the one-pass index of identifier offsets; `compared` caches, per name, the sorted offsets of
    its uses that are compared with a principal, so each name's uses are judged once and each scope is one binary search."""
    if _compared_with_principal(clean, origin.start(), origin.end()):
        return True
    assigned = re.search(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*=\s*$", clean[max(0, origin.start() - 64):origin.start()])
    if not assigned:
        return False
    name = assigned.group(1)
    if name not in compared:
        compared[name] = sorted(use for use in positions.get(name, ()) if _compared_with_principal(clean, use, use + len(name)))
    hits = compared[name]
    scope = _alias_scope(function_at, name, origin.start())
    if scope is None:
        return bool(hits)
    i = bisect.bisect_right(hits, scope[0])  # the first compared use after the declaration
    return i < len(hits) and hits[i] < scope[1]


_BINDING_CAP = 256  # characters before the `=` of a call's binding that are read for the bound flag: a longer target is cut short
_ASSIGN_AFTER = re.compile(r"\s*(?:<<|>>|[-+*/%&|^])?=(?![=>])")  # a write to the name: `ok = `, `ok += `; not `ok == `
_DECL_BEFORE = re.compile(r"\b(?:bool|address|string|bytes\d*|u?int\d*|byte|[A-Z]\w*)\s+(?:(?:memory|storage|calldata)\s+)?\Z")
_REVERT_OR_RETURN = re.compile(r"(?:revert|return|throw)\b")
_HEAD_AT = re.compile(r"\b(?:(require|assert|if)\s*\(|(return)\b)")  # a check head: its keyword and '(' (or a return)


def _slots(text: str) -> list[str]:
    """The top-level comma slots of a tuple target, empty slots kept: `(, data)` has no success slot in front."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in text:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def _bound_flags(lhs: str) -> list[str]:
    """The success flags a low-level call's binding names: `bool ok` in `(bool ok, ) =`, the first slot of a tuple otherwise (`ok`
    in `(ok, data) =` for a variable declared earlier: a low-level call returns its success first), or the bare name in `ok =`.
    An empty first slot names no flag."""
    lhs = lhs.strip()
    tuple_form = lhs.startswith("(") and lhs.endswith(")")
    if tuple_form:
        lhs = lhs[1:-1]
    flags: list[str] = []
    for i, part in enumerate(_slots(lhs)):
        part = part.strip()
        tokens = re.findall(_IDENT, part)
        if len(tokens) >= 2 and tokens[0] == "bool":
            flags.append(tokens[-1])
        elif tuple_form and i == 0 and len(tokens) == 1 and re.fullmatch(_IDENT, part):
            flags.append(part)
    if not tuple_form and not flags and re.fullmatch(_IDENT, lhs.strip()):
        flags.append(lhs.strip())
    return flags


def _exposed_ids(contracts: list[dict]) -> set[int]:
    """id() of every function that a public or external entry point without access control reaches, in any contract that
    can see it: its body can run for an arbitrary caller."""
    exposed: set[int] = set()
    for contract in contracts:
        index = visible_index(contract, contracts)
        for entry in contract["functions"]:
            if entry["kind"] in ENTRY_KINDS and entry["visibility"] in ("public", "external") and entry["has_body"] and not guards(entry, index):
                exposed.update(id(fn) for fn in _reachable(entry, index))
    return exposed


_IDENT_TOKEN = re.compile(r"(?<![\w$.])[A-Za-z_$][\w$]*")


def _identifier_positions(clean: str) -> dict[str, list[int]]:
    """Offsets of every identifier token of the stripped source, by name, from one pass."""
    out: dict[str, list[int]] = {}
    for m in _IDENT_TOKEN.finditer(clean):
        out.setdefault(m.group(0), []).append(m.start())
    return out


def _function_lookup(contracts: list[dict]):
    """A function(offset) -> the function or modifier whose body contains the offset, or None. Bodies do not overlap, so one
    binary search over their start offsets answers each query (a file with many call sites must not rescan its functions)."""
    bodies = sorted(((fn["body_span"][0], fn["body_span"][1], fn) for c in contracts for fn in c["functions"] if fn["has_body"]),
                    key=lambda item: item[0])
    starts = [item[0] for item in bodies]

    def lookup(pos: int) -> dict | None:
        i = bisect.bisect_right(starts, pos) - 1
        return bodies[i][2] if i >= 0 and pos < bodies[i][1] else None

    return lookup


def lint_source(rel: str, raw: str) -> list[dict]:
    """Findings {id, severity, summary, location} for one Solidity file. Pragma findings are the pragma_policy findings."""
    clean = strip_source(raw)
    out: list[dict] = []

    def add(rule: str, severity: str, summary: str, pos: int) -> None:
        out.append({"id": rule, "severity": severity, "summary": redact(summary), "location": f"{rel}:{line_of(clean, pos)}"})

    pragmas = _pragma_directives(clean)
    uses_layout_at = bool(_LAYOUT_AT.search(clean))
    if not pragmas and _DECL_HEAD.search(clean):
        add("SOL-PRAGMA-MISSING", "minor", "no `pragma solidity` directive: the compiler version is unconstrained", 0)
    for start, expr in pragmas:
        try:
            review = pragma_findings(expr, min_version=(0, 8, 0), floating_severity="minor", via_ir=False, optimizer=True,
                                     layout_at=uses_layout_at)
        except ValueError as exc:
            add("SOL-PRAGMA-INVALID", "minor", f"unparseable pragma: {exc}", start)
            continue
        for f in review["findings"]:
            add("SOL-" + f["id"], f["severity"], f["summary"], start)
    contracts = parse_source(raw)
    function_at = _function_lookup(contracts)
    origins = list(_ORIGIN.finditer(clean))
    positions = _identifier_positions(clean) if origins else {}
    compared: dict[str, list[int]] = {}  # per name: the offsets of its uses compared with a principal, judged once
    for m in origins:
        authz = _origin_is_authorization(clean, m, positions, compared, function_at)
        add("SOL-TX-ORIGIN", "major" if authz else "minor",
            "tx.origin used in an authorization comparison (phishable through any contract the user calls; use msg.sender)"
            if authz else "tx.origin read: never use it for authorization", m.start())
    for m in re.finditer(r"\b(selfdestruct|suicide)\s*\(", clean):
        add("SOL-SELFDESTRUCT", "major", "selfdestruct: deprecated, since EIP-6780 (Cancun) it only deletes code in the creating "
            "transaction, and it breaks proxy implementations", m.start())
    exposed: set[int] | None = None  # computed on the first delegatecall only: most sources have none and guard analysis is not free

    def delegatecall(pos: int) -> None:
        nonlocal exposed
        if exposed is None:
            exposed = _exposed_ids(contracts)
        fn = function_at(pos)
        public = fn is None or id(fn) in exposed
        add("SOL-DELEGATECALL", "major" if public else "minor",
            "delegatecall in a public/external function with no access control: the callee runs with this contract's storage"
            if public else "delegatecall: callee code runs with this contract's storage and balance; confirm the target is trusted",
            pos)

    sites = _CallSites(clean)
    verdict = sites.verdicts()
    for m in _LOW_LEVEL.finditer(clean):
        if m.group(1) == "delegatecall":
            delegatecall(m.start())
        if not verdict[m.start()]:
            add("SOL-UNCHECKED-LOW-LEVEL-CALL", "major",
                f".{m.group(1)}(...) result is discarded: a failed call goes unnoticed (check the returned bool)", m.start())
    for m in _ASM_DELEGATECALL.finditer(clean):
        delegatecall(m.start())
    status = emission_status(contracts)
    for contract in contracts:
        for fn in contract["functions"]:
            if status.get(id(fn)) == "missing":
                out.append({"id": "SOL-MISSING-EVENT", "severity": "minor",
                            "summary": redact(f"{contract['name']}.{fn['signature']} changes state but emits no event (directly or via same-file callees)"),
                            "location": f"{rel}:{fn['line']}"})
    return out


def _collect(root: Path, start: Path) -> tuple[list[tuple[str, str]], dict, list[str]]:
    """[(relative path, text)] of the .sol files under start, the skip counters, and one reason (a label and a path) per
    in-scope input that was not analysed: a symlink, a file over the size limit, a binary or unreadable file, the file limit.
    Foundry test and script files are out of scope by design and only counted. Dependency and build directories (SKIP_DIRS, such
    as lib/ and out/) are out of scope by design: they are listed by relative path under `excluded_dirs` and never make the
    status fail. Only in-scope input that was not analysed, or no in-scope input at all, fails closed."""
    skipped = {"symlinks": 0, "test_files": 0, "too_large": 0, "binary": 0, "unreadable": 0, "excluded_dirs": [], "limit_reached": False}
    reasons: list[str] = []
    files: list[tuple[str, str]] = []

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    if start.is_file():
        candidates = [start]
    else:
        candidates = []
        for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
            kept = []
            for name in sorted(dirnames):
                child = Path(dirpath) / name
                if child.is_symlink():
                    skipped["symlinks"] += 1
                    reasons.append(f"symlinked directory skipped: {rel(child)}")
                elif name in SKIP_DIRS:
                    skipped["excluded_dirs"].append(rel(child))
                else:
                    kept.append(name)
            dirnames[:] = kept
            candidates.extend(Path(dirpath) / name for name in sorted(filenames) if name.endswith(".sol"))
    for path in sorted(candidates, key=rel):
        if path.is_symlink():
            skipped["symlinks"] += 1
            reasons.append(f"symlink skipped: {rel(path)}")
            continue
        if path.name.endswith((".t.sol", ".s.sol")):
            skipped["test_files"] += 1
            continue
        if len(files) >= MAX_FILES:
            skipped["limit_reached"] = True
            reasons.append(f"file limit of {MAX_FILES} reached, further .sol files not scanned")
            break
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):  # a FIFO or device blocks the read (or never ends): not a source file
                skipped["unreadable"] += 1
                reasons.append(f"not a regular file, skipped: {rel(path)}")
                continue
            if info.st_size > MAX_FILE_BYTES:
                skipped["too_large"] += 1
                reasons.append(f"over {MAX_FILE_BYTES} bytes, skipped: {rel(path)}")
                continue
            data = path.read_bytes()
        except OSError:
            skipped["unreadable"] += 1
            reasons.append(f"unreadable, skipped: {rel(path)}")
            continue
        if b"\0" in data:
            skipped["binary"] += 1
            reasons.append(f"binary, skipped: {rel(path)}")
            continue
        files.append((rel(path), data.decode("utf-8", errors="replace")))
    return files, skipped, reasons


_OPENER = {")": "(", "]": "[", "}": "{"}
# a function at file level with a visibility keyword outside its own parameter list (a function-type parameter such as
# `function() external` has its own), or a modifier, constructor, receive or fallback keyword: no contract body holds it
_TOP_LEVEL_FUNCTION = re.compile(r"\bfunction\b[^;{]*?\b(?:external|public|internal|private)\b")
_TOP_LEVEL_OTHER = re.compile(r"\b(?:modifier|constructor|receive|fallback)\s*\(")


def _outside_parens(text: str) -> str:
    """`text` with every parenthesised span blanked to spaces (length kept): the header words outside any parameter list."""
    out: list[str] = []
    depth = 0
    for c in text:
        if c == "(":
            depth += 1
            out.append(" ")
        elif c == ")":
            depth = max(depth - 1, 0)
            out.append(" ")
        else:
            out.append(" " if depth else c)
    return "".join(out)


def source_problems(raw: str) -> list[str]:
    """Labels of what makes a Solidity source unparseable, never its text: unbalanced brackets (a truncated file always has them)
    and a declaration outside every contract body. Linear in the source size; [] means the source parses."""
    clean = strip_source(raw)
    problems: list[str] = []
    stack: list[str] = []
    balanced = True
    for m in re.finditer(r"[()\[\]{}]", clean):
        c = m.group()
        if c in "([{":
            stack.append(c)
        elif not stack or stack.pop() != _OPENER[c]:
            balanced = False
            break
    if stack:
        balanced = False
    if not balanced:
        problems.append("unbalanced brackets: the source is truncated or malformed")
    top: list[str] = []  # the text outside every brace pair, where only file-level declarations may appear
    depth = last = 0
    for m in re.finditer(r"[{}]", clean):
        if depth == 0:
            top.append(clean[last:m.start()])
        depth = max(depth + (1 if m.group() == "{" else -1), 0)
        last = m.end()
    if depth == 0:
        top.append(clean[last:])
    joined = " ".join(top)
    if _TOP_LEVEL_FUNCTION.search(_outside_parens(joined)) or _TOP_LEVEL_OTHER.search(joined):
        problems.append("a function, modifier, constructor, receive or fallback outside any contract body")
    return problems


def _tool_status(binary: str) -> str:
    return "available:not-invoked" if shutil.which(binary) else "skipped:tool-missing"


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "files_scanned": 0,
                "skipped": {"symlinks": 0, "test_files": 0, "too_large": 0, "binary": 0, "unreadable": 0, "excluded_dirs": [], "limit_reached": False},
                "counts": {name: 0 for name in SEVERITIES}, "findings": [], "findings_total": 0,
                "tools": {"slither": "skipped:dry-run", "forge": "skipped:dry-run"},
                "summary": "dry-run: sol_contract_lint would scan *.sol files under --path within --root"}
    root = ctx.root.resolve()
    target = (root / args.path).resolve()
    if not target.is_relative_to(root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {root}")
    files, skipped, reasons = _collect(root, target)
    findings: list[dict] = []
    for rel, text in files:
        try:
            findings.extend(lint_source(rel, text))
        except RecursionError:  # the call walk of the guard analysis is deeper than the interpreter allows: the file is not analysed
            reasons.append(f"{rel}: call depth exceeds the call-depth limit (the interpreter's recursion limit, "
                           f"{sys.getrecursionlimit()}), the call walk stopped")
            continue
        # an unparseable file (truncated, or a declaration outside every contract) is not fully analysed: it fails closed
        reasons.extend(f"{rel}: {problem}" for problem in source_problems(text))
    # fail closed: an in-scope input that was not analysed, or no in-scope input at all, must not read as ok
    for item in incomplete_findings([redact(reason) for reason in reasons], len(files)):
        findings.append({"id": item["id"], "severity": item["severity"], "summary": item["summary"], "location": ".:0"})
    findings.sort(key=lambda f: (f["location"].rsplit(":", 1)[0], int(f["location"].rsplit(":", 1)[1]), -SEV_RANK[f["severity"]], f["id"]))
    counts = {name: sum(1 for f in findings if f["severity"] == name) for name in SEVERITIES}
    failing = counts["major"] + counts["blocker"]
    return {"status": scan_status(findings), "files_scanned": len(files), "skipped": skipped, "counts": counts,
            "findings": findings[:MAX_FINDINGS], "findings_total": len(findings),
            "tools": {"slither": _tool_status("slither"), "forge": _tool_status("forge")},
            "summary": f"{len(files)} file(s) scanned, {len(findings)} finding(s), {failing} major or worse"}


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A26", "sol_contract_lint", run, description=__doc__ or "", add_args=add_args).main())
