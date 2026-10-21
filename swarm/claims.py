"""File-claim table: which in-flight run may touch which repo paths.

A claim is a repo-relative glob (`*`, `?`, `[..]` within a segment, `**` across segments) plus a mode. Two claims
conflict when their patterns can match a common path AND at least one of them is exclusive; shared+shared never
conflicts. A trailing `/` means the whole directory (`src/` == `src/**`); an EXCLUSIVE bare name such as `src` (no glob
character in its last segment) reserves that path and everything beneath it, so it conflicts with `src/a.py` too; a
shared bare name is just that one path.
Overlap is decided conservatively: when in doubt (character classes) two patterns are reported as overlapping.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

EXCLUSIVE = "exclusive"
SHARED = "shared"
MODES = (EXCLUSIVE, SHARED)
_DRIVE = re.compile(r"^[A-Za-z]:")
_CLASS = re.compile(r"\[(?:[^\]]|(?<=\[)\])+\]")


def normalize_pattern(pattern: str) -> str:
    """Canonical repo-relative form; raises ValueError for empty, absolute or `..` patterns."""
    if not isinstance(pattern, str):
        raise ValueError(f"claim pattern must be a string, got {type(pattern).__name__}")
    p = pattern.strip().replace("\\", "/")
    if not p:
        raise ValueError("claim pattern is empty")
    if p.startswith("/") or p.startswith("~") or _DRIVE.match(p):
        raise ValueError(f"claim pattern must be repo-relative, got {pattern!r}")
    directory = p.endswith("/")
    segments: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError(f"claim pattern must not contain '..': {pattern!r}")
        if seg == "**" and segments and segments[-1] == "**":
            continue
        segments.append(seg)
    if not segments:
        raise ValueError(f"claim pattern is empty: {pattern!r}")
    if directory and segments[-1] != "**":
        segments.append("**")
    return "/".join(segments)


@dataclass(frozen=True)
class Claim:
    """One claimed pattern; `pattern` is normalized on construction."""

    pattern: str
    mode: str = EXCLUSIVE

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"claim mode must be one of {MODES}, got {self.mode!r}")
        object.__setattr__(self, "pattern", normalize_pattern(self.pattern))


@dataclass(frozen=True)
class ClaimConflict:
    """A requested claim that collides with a claim held by another owner."""

    owner: str
    held: Claim
    requested: Claim


@dataclass(frozen=True)
class ClaimResult:
    ok: bool
    conflicts: tuple[ClaimConflict, ...] = ()


def parse_claims(notes: Any) -> list[Claim]:
    """Claims from `notes["claims"]`: each item is a pattern string (exclusive) or `{"pattern", "mode"}`.

    Missing notes or claims yield []; a malformed item raises ValueError (fail closed rather than run unclaimed).
    """
    if not isinstance(notes, dict):
        return []
    raw = notes.get("claims")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("notes.claims must be a list")
    claims: list[Claim] = []
    for item in raw:
        if isinstance(item, Claim):
            claims.append(item)
        elif isinstance(item, str):
            claims.append(Claim(item))
        elif isinstance(item, dict) and "pattern" in item:
            claims.append(Claim(item["pattern"], item.get("mode", EXCLUSIVE)))
        else:
            raise ValueError(f"unsupported claim entry: {item!r}")
    return claims


def _tokens(segment: str) -> tuple[str, ...]:
    """Segment glob -> tokens: '*' (any run), '?' (any one char; also stands in for a [..] class), or a literal char."""
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "[":
            m = _CLASS.match(segment, i)
            if m:
                out.append("?")
                i = m.end()
                continue
        if ch == "*":
            if not out or out[-1] != "*":
                out.append("*")
        elif ch == "?":
            out.append("?")
        else:
            out.append("L" + ch)
        i += 1
    return tuple(out)


@lru_cache(maxsize=4096)
def _segments_overlap(a: str, b: str) -> bool:
    if a == b:
        return True
    ta, tb = _tokens(a), _tokens(b)

    @lru_cache(maxsize=None)
    def go(i: int, j: int) -> bool:
        if i == len(ta) and j == len(tb):
            return True
        if i < len(ta) and ta[i] == "*":
            if go(i + 1, j) or (j < len(tb) and go(i, j + 1)):
                return True
        if j < len(tb) and tb[j] == "*":
            if go(i, j + 1) or (i < len(ta) and go(i + 1, j)):
                return True
        if i < len(ta) and j < len(tb) and ta[i] != "*" and tb[j] != "*":
            x, y = ta[i], tb[j]
            if x == "?" or y == "?" or x == y:
                return go(i + 1, j + 1)
        return False

    return go(0, 0)


@lru_cache(maxsize=4096)
def _overlap_normalized(a: str, b: str) -> bool:
    sa, sb = tuple(a.split("/")), tuple(b.split("/"))

    @lru_cache(maxsize=None)
    def go(i: int, j: int) -> bool:
        if i == len(sa) and j == len(sb):
            return True
        if i < len(sa) and sa[i] == "**":
            if go(i + 1, j) or (j < len(sb) and go(i, j + 1)):
                return True
        if j < len(sb) and sb[j] == "**":
            if go(i, j + 1) or (i < len(sa) and go(i + 1, j)):
                return True
        if i < len(sa) and j < len(sb) and sa[i] != "**" and sb[j] != "**":
            return _segments_overlap(sa[i], sb[j]) and go(i + 1, j + 1)
        return False

    return go(0, 0)


def overlaps(a: str | Claim, b: str | Claim) -> bool:
    """True when two patterns can match a common repo path. Raises ValueError for absolute, `..` or empty patterns."""
    pa = a.pattern if isinstance(a, Claim) else normalize_pattern(a)
    pb = b.pattern if isinstance(b, Claim) else normalize_pattern(b)
    return _overlap_normalized(pa, pb)


_GLOB_CHARS = re.compile(r"[*?\[]")


def _footprint(c: Claim) -> tuple[str, ...]:
    """Patterns an EXCLUSIVE claim really reserves: a bare name (`swarm/tools`, last segment without glob characters) is
    the name AND everything under it, so claiming a directory without the trailing slash cannot silently allow
    overlapping edits. Shared claims and glob-ended patterns stand for themselves."""
    if c.mode == EXCLUSIVE and not _GLOB_CHARS.search(c.pattern.rsplit("/", 1)[-1]):
        return (c.pattern, f"{c.pattern}/**")
    return (c.pattern,)


def _conflicts(x: Claim, y: Claim) -> bool:
    if x.mode != EXCLUSIVE and y.mode != EXCLUSIVE:
        return False
    return any(_overlap_normalized(a, b) for a in _footprint(x) for b in _footprint(y))


class ClaimTable:
    """Thread-safe registry of claims per owner (a run id). `acquire` is all-or-nothing."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._held: dict[str, list[Claim]] = {}

    def acquire(self, owner: str, claims: list[Claim] | tuple[Claim, ...]) -> ClaimResult:
        """Take every claim for `owner`, or none if any conflicts with another owner's claim.

        An owner never conflicts with itself, and re-acquiring a claim it already holds is a no-op.
        """
        requested = list(claims)
        with self._lock:
            conflicts = [
                ClaimConflict(other, held, want)
                for want in requested
                for other, held_claims in self._held.items()
                if other != owner
                for held in held_claims
                if _conflicts(want, held)
            ]
            if conflicts:
                return ClaimResult(False, tuple(conflicts))
            if requested:
                mine = self._held.setdefault(owner, [])
                for want in requested:
                    if want not in mine:
                        mine.append(want)
            return ClaimResult(True, ())

    def release(self, owner: str) -> int:
        """Drop everything `owner` holds; idempotent. Returns how many claims were released."""
        with self._lock:
            return len(self._held.pop(owner, ()))

    def held(self) -> dict[str, tuple[Claim, ...]]:
        """Snapshot of owner -> claims; later table changes do not alter it."""
        with self._lock:
            return {owner: tuple(claims) for owner, claims in self._held.items()}
