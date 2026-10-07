"""Tee of the swarm run log to Agent Substrate (ADR 0001, phase S1).

`runlog.emit()` writes the local JSONL record first and then calls `tee()`. The tee turns the record into one
substrate event and POSTs it to /events. It is strictly additive and fail-open: no SUBSTRATE_URL, SUBSTRATE_DISABLED=1,
an unknown Graph ID, an unmapped type, a timeout or any exception means "send nothing" and the caller carries on.
Design notes (the two tables, session format, binding cache, dedupe): docs/substrate-tee.md.
"""
from __future__ import annotations
import os
import re
import secrets
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Mapping

from . import substrate_client
from .paths import swarm_dir

# --- identity ----------------------------------------------------------------------------------------------
# Swarm agent id -> substrate surface (the closed set in agent-substrate `SURFACES`). The server-side token env var
# is SUBSTRATE_TOKEN_<SURFACE, '-'->'_', upper>. agents.json is cross-checked against this table in the tests.
AGENT_SURFACES: dict[str, str] = {
    "A01": "swarm-a01-orch",
    "A02": "swarm-a02-req",
    "A03": "swarm-a03-arch",
    "A04": "swarm-a04-uxd",
    "A05": "swarm-a05-be",
    "A06": "swarm-a06-fe",
    "A07": "swarm-a07-data",
    "A08": "swarm-a08-qa",
    "A09": "swarm-a09-rev",
    "A10": "swarm-a10-sec",
    "A11": "swarm-a11-devops",
    "A12": "swarm-a12-rel",
    "A13": "swarm-a13-obs",
    "A14": "swarm-a14-maint",
    "A15": "swarm-a15-doc",
}

# --- swarm event type -> substrate EVENT_KINDS --------------------------------------------------------------
# EVENT_KINDS is closed: session.start, prompt, claim, tool.call, file.edit, shell, commit, pr, handoff,
# session.end, note, warning. EVERY type the repo emits via runlog.emit()/Ctx.emit() is listed here (or matched by
# a PREFIX_KINDS family); tests/test_substrate_tee.py parses every emit site and fails on an unmapped type.
# An unmapped type is never tee'd and never silently downgraded to `note`. The swarm type always rides in
# payload.swarm_type, so the mapping loses no information.
TYPE_KINDS: dict[str, str] = {
    # plan / lifecycle bookkeeping -> note
    "plan.updated": "note",              # A01 wrote a plan snapshot (orch_plan)
    "task.transition": "note",           # A01 moved a task between states (orch_status --transition)
    "task.result.raw": "note",           # runner captured a raw agent result before validation
    # a task changing hands -> claim
    "task.claimed": "claim",             # an agent took a task (ingest of a task.result that skipped the claim)
    # gate verdict / findings housekeeping and rejected or escalated work -> warning
    "gate.verdict.unrecorded": "warning",      # a gate ran but its verdict could not be recorded against a task
    "gate.findings.coerced": "warning",        # runner had to coerce a gate's findings into shape
    "gate.findings.synthesized": "warning",    # runner synthesized findings the gate did not produce
    "gate.findings.unattributed": "warning",   # findings named no known gate target
    "task.result.rejected": "warning",         # a task.result failed schema/contract validation
    "escalation.request": "warning",           # rework loops / attempts exhausted: a human is needed
    "security.dev_key": "warning",             # an envelope was signed with the development key
}
# `script.<name>` and `script.<name>.error` are the exit record of one agent script run -> tool.call.
PREFIX_KINDS: tuple[tuple[str, str], ...] = (("script.", "tool.call"),)


def kind_for(swarm_type: str) -> str | None:
    """EVENT_KINDS value for a swarm event type, or None when the type is unmapped."""
    if swarm_type in TYPE_KINDS:
        return TYPE_KINDS[swarm_type]
    for prefix, kind in PREFIX_KINDS:
        if swarm_type.startswith(prefix) and len(swarm_type) > len(prefix):
            return kind
    return None


_AGENT_RE = re.compile(r"^(A\d{2})(?:@|$)")


def agent_of(record: Mapping) -> str | None:
    """The emitting agent id (A01..A15) of a run-log record, or None when it names none.

    Ctx.emit stamps ``source = "<agent id>@<script>"``. The one non-agent source that still has an identity is
    ``swarm.envelope`` (security.dev_key), whose payload carries the signing agent as ``source``.
    """
    src = record.get("source")
    m = _AGENT_RE.match(src) if isinstance(src, str) else None
    if m is None and src == "swarm.envelope":
        inner = (record.get("payload") or {}).get("source")
        m = _AGENT_RE.match(inner) if isinstance(inner, str) else None
    return m.group(1) if m and m.group(1) in AGENT_SURFACES else None


# --- Graph ID ----------------------------------------------------------------------------------------------
GRAPH_ID_RE = re.compile(r"ut-[0-9a-z]+-[0-9a-f]{8}")
_GRAPH_ID_IN_TEXT = re.compile(r"(?<![0-9A-Za-z-])ut-[0-9a-z]+-[0-9a-f]{8}(?![0-9A-Za-z])")
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def is_graph_id(value: object) -> bool:
    return isinstance(value, str) and GRAPH_ID_RE.fullmatch(value) is not None


def mint_graph_id(now_ms: int | None = None) -> str:
    """`ut-<base36 epoch ms>-<8 random hex>`: the same format the ultrathink hook mints."""
    n = int(time.time() * 1000) if now_ms is None else int(now_ms)
    digits = ""
    while True:
        n, r = divmod(n, 36)
        digits = _B36[r] + digits
        if n == 0:
            break
    return f"ut-{digits}-{secrets.token_hex(4)}"


def graph_id_in_text(text: str | None) -> str | None:
    """First Graph ID in a brief/spec (e.g. `<ISSUES graphId="ut-...">` or `Graph ID: ut-...`)."""
    m = _GRAPH_ID_IN_TEXT.search(text or "")
    return m.group(0) if m else None


def resolve_graph_id(correlation_id: str | None = None, *, explicit: str | None = None, env: Mapping[str, str] | None = None,
                     brief_text: str | None = None) -> str:
    """Graph ID to offer for a new run: --graph-id > env SUBSTRATE_GRAPH_ID > one named in the brief > freshly minted.

    This only decides what to *offer*; the authoritative id is whatever `bind_graph` gets back from the server.
    """
    e = os.environ if env is None else env
    if is_graph_id(explicit):
        return explicit  # type: ignore[return-value]
    if is_graph_id((e.get("SUBSTRATE_GRAPH_ID") or "").strip()):
        return e["SUBSTRATE_GRAPH_ID"].strip()
    return graph_id_in_text(brief_text) or mint_graph_id()


# --- durable local state: binding cache + dedupe window ----------------------------------------------------
DEDUPE_WINDOW_S = 24 * 3600
_DB_NAME = "substrate-tee.db"


def _connect(root: str | Path | None) -> sqlite3.Connection:
    con = sqlite3.connect(swarm_dir(root, create=True) / _DB_NAME, timeout=2.0)
    con.execute("CREATE TABLE IF NOT EXISTS bindings (correlation_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL, created REAL NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS seen (surface TEXT NOT NULL, msg_id TEXT NOT NULL, first_seen REAL NOT NULL, "
                "PRIMARY KEY (surface, msg_id))")
    return con


def cached_graph_id(correlation_id: str, root: str | Path | None = None) -> str | None:
    with closing(_connect(root)) as con:
        row = con.execute("SELECT graph_id FROM bindings WHERE correlation_id = ?", (correlation_id,)).fetchone()
    return row[0] if row else None


def remember_graph_id(correlation_id: str, graph_id: str, root: str | Path | None = None) -> None:
    with closing(_connect(root)) as con, con:
        con.execute("INSERT OR REPLACE INTO bindings(correlation_id, graph_id, created) VALUES (?, ?, ?)",
                    (correlation_id, graph_id, time.time()))


def bind_graph(correlation_id: str, graph_id: str, *, root: str | Path | None = None,
               env: Mapping[str, str] | None = None) -> str | None:
    """Bind `correlation_id` to `graph_id` on substrate; return the graph_id that is bound, or None.

    The server's answer always wins: on `conflict` (another run bound this correlation first) the returned id is
    adopted and cached, never the offered one. None means substrate did not answer, nothing is cached.
    """
    got = substrate_client.mcp_call("graph_bind", {"correlation_id": correlation_id, "graph_id": graph_id}, env)
    bound = got.get("graph_id") if got else None
    if not is_graph_id(bound):
        return None
    remember_graph_id(correlation_id, bound, root)
    _unbound.pop(correlation_id, None)
    return bound


_UNBOUND_TTL_S = 60.0
_unbound: dict[str, float] = {}  # correlation_id -> monotonic expiry; in-process only, never persisted


def lookup_graph_id(correlation_id: str, *, root: str | Path | None = None, env: Mapping[str, str] | None = None) -> str | None:
    """Graph ID of a correlation: local cache, else a forward `graph_bind` lookup (cached on success), else None.

    A correlation substrate calls `unbound` (or does not answer for) is remembered in memory for 60 s, so a burst of
    records for it costs one network call. That negative result is never written to sqlite; `bind_graph` clears it.
    """
    hit = cached_graph_id(correlation_id, root)
    if hit:
        return hit
    now = time.monotonic()
    if _unbound.get(correlation_id, 0.0) > now:
        return None
    got = substrate_client.mcp_call("graph_bind", {"correlation_id": correlation_id}, env)
    gid = got.get("graph_id") if got else None
    if got and got.get("status") == "existing" and is_graph_id(gid):
        _unbound.pop(correlation_id, None)
        remember_graph_id(correlation_id, gid, root)
        return gid
    _unbound[correlation_id] = now + _UNBOUND_TTL_S
    return None


def reset() -> None:
    """Forget the in-process negative lookups and memoized repo slugs (tests; a long-lived process)."""
    _unbound.clear()
    _SLUGS.clear()


def _claim(root: str | Path | None, surface: str, msg_id: str) -> bool:
    """Atomically record (surface, msg_id) as seen; False when it already was inside the window. Prunes on write."""
    now = time.time()
    with closing(_connect(root)) as con, con:
        con.execute("DELETE FROM seen WHERE first_seen < ?", (now - DEDUPE_WINDOW_S,))
        return con.execute("INSERT OR IGNORE INTO seen(surface, msg_id, first_seen) VALUES (?, ?, ?)",
                           (surface, msg_id, now)).rowcount == 1


def _release(root: str | Path | None, surface: str, msg_id: str) -> None:
    with closing(_connect(root)) as con, con:
        con.execute("DELETE FROM seen WHERE surface = ? AND msg_id = ?", (surface, msg_id))


_ORIGIN_RE = re.compile(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$")
_SLUGS: dict[str, str] = {}


def repo_slug(root: str | Path) -> str:
    """`owner/name` of the repo at `root` from `git remote get-url origin`; the directory name when there is none.

    Memoized per root for the life of the process. Never raises.
    """
    resolved = Path(root).resolve()
    key = str(resolved)
    got = _SLUGS.get(key)
    if got is None:
        got = resolved.name
        try:
            out = subprocess.run(["git", "-C", key, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=2,
                                 check=False)
            m = _ORIGIN_RE.search(out.stdout.strip()) if out.returncode == 0 else None
            if m:
                got = m.group(1)
        except Exception:  # noqa: BLE001 - no git, a timeout, a bad path: fall back to the directory name
            pass
        _SLUGS[key] = got
    return got


# --- the tee -----------------------------------------------------------------------------------------------
def build_event(record: Mapping, graph_id: str, *, env: Mapping[str, str] | None = None, root: str | Path | None = None) -> dict | None:
    """The /events body for a run-log record, or None when the record cannot be attributed or its type is unmapped."""
    e = os.environ if env is None else env
    swarm_type, agent = record.get("type"), agent_of(record)
    kind = kind_for(swarm_type) if isinstance(swarm_type, str) else None
    msg_id, corr = record.get("msg_id"), record.get("correlation_id")
    if kind is None or agent is None or not msg_id or not corr:
        return None
    replica = (e.get("SWARM_REPLICA") or "").strip() or "r0"
    task_id = record.get("task_id")
    payload = {"correlation_id": corr, "msg_id": msg_id, "swarm_type": swarm_type}
    for key in ("trace_id", "causation_id"):
        if record.get(key):
            payload[key] = record[key]
    status = (record.get("payload") or {}).get("status")
    if isinstance(status, str):
        payload["status"] = status
    body = {
        "kind": kind,
        "summary": f"{swarm_type}" + (f" {task_id}" if task_id else f" {corr}"),
        "surface": AGENT_SURFACES[agent],
        "session_id": f"{agent}@{replica}:{graph_id}",
        "graph_id": graph_id,
        "actor": "agent",
        "payload": payload,
    }
    if task_id:
        body["node_id"] = task_id
    if root is not None:
        body["repo"] = repo_slug(root)
    return body


def tee(record: Mapping, ctx_root: str | Path | None = None, *, env: Mapping[str, str] | None = None) -> bool:
    """Send one run-log record to substrate. True only when substrate accepted a new event. Never raises."""
    try:
        e = os.environ if env is None else env
        if not substrate_client.enabled(e):
            return False
        corr = record.get("correlation_id")
        if not corr or agent_of(record) is None or kind_for(str(record.get("type"))) is None or not record.get("msg_id"):
            return False
        graph_id = lookup_graph_id(corr, root=ctx_root, env=e)
        if graph_id is None:  # unbound run: skip, never invent a Graph ID here
            return False
        body = build_event(record, graph_id, env=e, root=ctx_root)
        if body is None:
            return False
        if not _claim(ctx_root, body["surface"], record["msg_id"]):
            return False
        got = substrate_client.rest_post("/events", body, e)
        if got is not None and 200 <= got[0] < 300:
            return True
        _release(ctx_root, body["surface"], record["msg_id"])  # dropped: a republish may try again
        return False
    except Exception:  # noqa: BLE001 - the tee must never disturb the host program
        return False
