"""State/Idempotency Store for hook triggers, swarm run state (PENDING/FIRED etc per n5/n7).
Boring extraction + extension from taskstore + runlog facts. Single-writer tx, atomic check-set.
"""

from __future__ import annotations
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import swarm_dir


def _db_path(root: str | Path | None) -> Path:
    return swarm_dir(root, create=True) / "tasks.db"


@contextmanager
def _tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class TriggerStore:
    """Dedup store for hook trigger firings (n3/n7). Atomic check-set per G-atomic-check-set."""

    def __init__(self, path: str | Path | None = None, *, root: str | Path | None = None):
        self.path = Path(path) if path else _db_path(root)
        self._init_schema()

    def _init_schema(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trigger_firings (
                    id TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    correlation_id TEXT,
                    brief_hash TEXT,
                    fired INTEGER DEFAULT 1
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def seen_trigger(self, trigger_id: str, *, correlation_id: str | None = None, brief_hash: str | None = None) -> bool:
        """Atomic check + set. Returns True if already seen (no fire)."""
        conn = sqlite3.connect(self.path)
        try:
            with _tx(conn):
                cur = conn.execute("SELECT 1 FROM trigger_firings WHERE id = ?", (trigger_id,))
                if cur.fetchone():
                    return True
                conn.execute(
                    "INSERT INTO trigger_firings (id, ts, correlation_id, brief_hash) VALUES (?, ?, ?, ?)",
                    (trigger_id, time.time(), correlation_id, brief_hash),
                )
            return False
        finally:
            conn.close()

    def record(self, trigger_id: str, **kw: Any) -> None:
        self.seen_trigger(trigger_id, **kw)


class SwarmStateStore:
    """Light obs projection store for 6-state (n5) + stale guard (n7). Primary .claude for advertised, .swarm mirror."""

    STATES = ("PENDING", "FIRED", "RUNNING", "PARTIAL_FAILURE", "FAILED", "COMPLETE")

    def __init__(self, path: str | Path | None = None, *, root: str | Path | None = None):
        self.root = Path(root) if root else Path(".")
        self.claude_path = self.root / ".claude" / "swarm-state.json"
        self.swarm_path = swarm_dir(root, create=True) / "swarm-state.json"
        self.claude_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.claude_path.exists():
            self._write(self.claude_path, {"version": "1.0", "swarm_state": "PENDING", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "progress": {}})
        if not self.swarm_path.exists():
            self._write(self.swarm_path, {"version": "1.0", "swarm_state": "PENDING", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "progress": {}})

    def _write(self, p: Path, data: dict) -> None:
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(p)

    def update(self, state: str, *, corr: str | None = None, progress: dict | None = None, extra: dict | None = None) -> dict:
        if state not in self.STATES:
            state = "RUNNING"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        data = {
            "version": "1.0",
            "correlation_id": corr,
            "swarm_state": state,
            "updated_at": now,
            "progress": progress or {},
            **(extra or {}),
        }
        self._write(self.claude_path, data)  # advertised
        self._write(self.swarm_path, data)  # mirror
        return data

    def get(self) -> dict:
        if self.claude_path.exists():
            return json.loads(self.claude_path.read_text())
        return {}

    def append_gsd_line(self, line: str, *, root: str | Path | None = None) -> None:
        # fix: use application root, not inside .swarm
        base = Path(root) if root else self.root
        state_md = base / ".planning" / "STATE.md"
        if state_md.exists():
            with state_md.open("a") as f:
                f.write(f"swarm: {line}\n")

    def check_pending_timeout(self, max_age: float = 3600) -> None:
        data = self.get()
        if data.get("swarm_state") == "PENDING":
            ts = data.get("updated_at")
            if ts:
                # simple age check; emit stale if old
                # (stub; real would parse and compare)
                pass


# compat thin
def get_trigger_store(root: str | Path | None = None) -> TriggerStore:
    return TriggerStore(root=root)


def get_swarm_state_store(root: str | Path | None = None) -> SwarmStateStore:
    return SwarmStateStore(root=root)
