"""SQLite Task Store implementing swarm.task.v1 and the lifecycle state machine.

Only A01 (orchestrator) may call `transition`
other agents call `report_status`,
which is validated and applied by the orchestrator scripts.
"""
from __future__ import annotations
import json
import os
import sqlite3
import time
from enum import Enum
from pathlib import Path

from .errors import SwarmError, ErrorCode


class TaskState(str, Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    PLANNED = "PLANNED"
    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    IN_REVIEW = "IN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    RETRY = "RETRY"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"


S = TaskState
LEGAL_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    S.CREATED: {S.VALIDATED, S.CANCELLED},
    S.VALIDATED: {S.PLANNED, S.BLOCKED, S.CANCELLED},
    S.PLANNED: {S.CLAIMED, S.BLOCKED, S.FAILED, S.CANCELLED},
    S.CLAIMED: {S.IN_PROGRESS, S.FAILED, S.RETRY, S.BLOCKED, S.CANCELLED},
    S.IN_PROGRESS: {S.IN_REVIEW, S.FAILED, S.RETRY, S.BLOCKED, S.CANCELLED},
    S.IN_REVIEW: {S.APPROVED, S.CHANGES_REQUESTED, S.FAILED, S.CANCELLED},
    S.CHANGES_REQUESTED: {S.IN_PROGRESS, S.ESCALATED, S.CANCELLED},
    S.APPROVED: {S.DONE, S.CANCELLED},
    S.BLOCKED: {S.PLANNED, S.CLAIMED, S.CANCELLED, S.ESCALATED},
    S.FAILED: {S.RETRY, S.ESCALATED, S.CANCELLED},
    S.RETRY: {S.PLANNED, S.CLAIMED, S.ESCALATED},
    S.ESCALATED: {S.PLANNED, S.CANCELLED},
    S.DONE: set(),
    S.CANCELLED: set(),
}
MAX_REWORK_LOOPS = 2
SATISFIED_STATES = {S.IN_REVIEW.value, S.APPROVED.value, S.DONE.value}
DEFAULT_MAX_ATTEMPTS = 3
GATES_BY_RISK = {"low": ["review"], "medium": ["review", "quality"],
                 "high": ["review", "quality", "security", "release"]}

DEFAULT_DB = Path(os.environ.get("SWARM_DIR", ".swarm")) / "tasks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, correlation_id TEXT NOT NULL, parent_id TEXT,
  dag_depth INTEGER DEFAULT 0, capability TEXT NOT NULL, agent_id TEXT,
  title TEXT, inputs TEXT DEFAULT '[]', outputs TEXT DEFAULT '[]',
  acceptance TEXT DEFAULT '[]', budget TEXT DEFAULT '{}', risk_class TEXT DEFAULT 'low',
  priority TEXT DEFAULT 'P2', state TEXT NOT NULL, attempt INTEGER DEFAULT 0,
  max_attempts INTEGER DEFAULT 3, rework_loops INTEGER DEFAULT 0,
  depends_on TEXT DEFAULT '[]', assigned_to TEXT, notes TEXT,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, from_state TEXT, to_state TEXT,
  actor TEXT, reason TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, gate TEXT, verdict TEXT,
  agent_id TEXT, findings TEXT, expires_at REAL, ts REAL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, uri TEXT,
  version TEXT, digest TEXT, producer TEXT, ts REAL
);
"""

_JSON_COLS = ("inputs", "outputs", "acceptance", "budget", "depends_on")


class TaskStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    # ---- CRUD -------------------------------------------------------------
    def create(self, *, task_id: str, correlation_id: str, capability: str, title: str = "",
               parent_id: str | None = None, dag_depth: int = 0, inputs=None, acceptance=None,
               budget=None, risk_class: str = "low", priority: str = "P2", depends_on=None,
               agent_id: str | None = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
               notes: dict | None = None) -> dict:
        now = time.time()
        self.conn.execute(
            "INSERT INTO tasks (task_id, correlation_id, parent_id, dag_depth, capability, agent_id, title,"
            " inputs, acceptance, budget, risk_class, priority, state, depends_on, max_attempts,"
            " notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, correlation_id, parent_id, dag_depth, capability, agent_id, title,
             json.dumps(inputs or []), json.dumps(acceptance or []),
             json.dumps(budget or {"max_tokens": 200000, "max_wall_s": 1800, "max_cost_usd": 5}),
             risk_class, priority, S.CREATED.value, json.dumps(depends_on or []), max_attempts,
             json.dumps(notes or {}), now, now))
        self._log(task_id, None, S.CREATED, "system", "created")
        self.conn.commit()
        return self.get(task_id)

    def get(self, task_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise SwarmError(ErrorCode.E_INPUT, f"unknown task {task_id}")
        return self._row(row)

    def list(self, *, correlation_id: str | None = None, state: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM tasks", []
        conds = []
        if correlation_id:
            conds.append("correlation_id=?")
            args.append(correlation_id)
        if state:
            conds.append("state=?")
            args.append(state)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY dag_depth, created_at"
        return [self._row(r) for r in self.conn.execute(q, args)]

    def update(self, task_id: str, **fields) -> dict:
        cols, args = [], []
        for k, v in fields.items():
            cols.append(f"{k}=?")
            args.append(json.dumps(v) if k in _JSON_COLS else v)
        cols.append("updated_at=?")
        args.append(time.time())
        args.append(task_id)
        self.conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE task_id=?", args)
        self.conn.commit()
        return self.get(task_id)

    # ---- state machine (A01 only) ----------------------------------------
    def transition(self, task_id: str, to_state: TaskState | str, *, actor: str = "A01",
                   reason: str = "") -> dict:
        to_state = TaskState(to_state)
        task = self.get(task_id)
        from_state = TaskState(task["state"])
        if to_state not in LEGAL_TRANSITIONS[from_state]:
            raise SwarmError(ErrorCode.E_CONTRACT,
                             f"illegal transition {from_state.value} → {to_state.value} for {task_id}",
                             task_id=task_id)
        extra = {}
        if to_state is S.CHANGES_REQUESTED:
            loops = task["rework_loops"] + 1
            if loops > MAX_REWORK_LOOPS:
                to_state, reason = S.ESCALATED, f"rework loops exhausted ({loops-1}); {reason}"
                if S.ESCALATED not in LEGAL_TRANSITIONS[from_state]:
                    # IN_REVIEW → CHANGES_REQUESTED → ESCALATED in one audited step
                    self._apply(task_id, from_state, S.CHANGES_REQUESTED, actor, "rework cap reached")
                    from_state = S.CHANGES_REQUESTED
            else:
                extra["rework_loops"] = loops
                notes = task["notes_json"]
                notes["verdicts_since"] = time.time() + 0.001
                extra["notes"] = json.dumps(notes)
        if to_state in (S.CLAIMED,) and from_state in (S.PLANNED, S.RETRY, S.BLOCKED):
            attempt = task["attempt"] + 1
            if attempt > task["max_attempts"]:
                raise SwarmError(ErrorCode.E_TIMEOUT, f"max_attempts exceeded for {task_id}", task_id=task_id)
            extra["attempt"] = attempt
        if to_state is S.APPROVED:
            missing = self.missing_gates(task_id)
            if missing:
                raise SwarmError(ErrorCode.E_POLICY,
                                 f"fail-closed: {task_id} lacks passing gates {missing}", task_id=task_id)
        self._apply(task_id, from_state, to_state, actor, reason, **extra)
        return self.get(task_id)

    def _apply(self, task_id, from_state, to_state, actor, reason, **extra):
        self.update(task_id, state=to_state.value, **extra)
        self._log(task_id, from_state, to_state, actor, reason)
        self.conn.commit()

    def _log(self, task_id, from_state, to_state, actor, reason):
        self.conn.execute("INSERT INTO transitions (task_id, from_state, to_state, actor, reason, ts)"
                          " VALUES (?,?,?,?,?,?)",
                          (task_id, from_state.value if from_state else None, to_state.value,
                           actor, reason, time.time()))

    def history(self, task_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM transitions WHERE task_id=? ORDER BY id", (task_id,))]

    # ---- gates ------------------------------------------------------------
    def record_verdict(self, task_id: str, gate: str, verdict: str, agent_id: str,
                       findings=None, expires_s: int = 86400) -> None:
        self.get(task_id)
        self.conn.execute("INSERT INTO verdicts (task_id, gate, verdict, agent_id, findings, expires_at, ts)"
                          " VALUES (?,?,?,?,?,?,?)",
                          (task_id, gate, verdict, agent_id, json.dumps(findings or []),
                           time.time() + expires_s, time.time()))
        self.conn.commit()

    def latest_verdicts(self, task_id: str, *, include_stale: bool = False) -> dict[str, dict]:
        """Latest verdict per gate. Verdicts issued before the task's last rework loop are stale
        (the producer changed the artifact) and are ignored unless include_stale=True."""
        since = 0.0 if include_stale else float(self.get(task_id)["notes_json"].get("verdicts_since", 0))
        out: dict[str, dict] = {}
        for r in self.conn.execute("SELECT * FROM verdicts WHERE task_id=? AND ts>=? ORDER BY id", (task_id, since)):
            d = dict(r)
            d["findings"] = json.loads(d["findings"])
            out[d["gate"]] = d
        return out

    def required_gates(self, task_id: str) -> list[str]:
        """Gates from risk class, unless the plan overrides them via notes.gates (e.g. [] for
        non-code tasks such as requirements or docs, or gate tasks themselves)."""
        task = self.get(task_id)
        override = (task.get("notes_json") or {}).get("gates")
        return list(override) if override is not None else GATES_BY_RISK[task["risk_class"]]

    def missing_gates(self, task_id: str) -> list[str]:
        latest = self.latest_verdicts(task_id)
        now = time.time()
        missing = []
        for g in self.required_gates(task_id):
            v = latest.get(g)
            if not v or v["verdict"] not in ("pass", "waive") or v["expires_at"] < now:
                missing.append(g)
        return missing

    # ---- artifacts --------------------------------------------------------
    def add_artifact(self, task_id: str, *, kind: str, uri: str, version: str = "1",
                     digest: str = "", producer: str = "") -> None:
        self.conn.execute("INSERT INTO artifacts (task_id, kind, uri, version, digest, producer, ts)"
                          " VALUES (?,?,?,?,?,?,?)", (task_id, kind, uri, version, digest, producer, time.time()))
        task = self.get(task_id)
        outputs = task["outputs"] + [{"kind": kind, "uri": uri, "version": version, "digest": digest}]
        self.update(task_id, outputs=outputs)

    # ---- DAG helpers ------------------------------------------------------
    def deps_satisfied(self, task: dict, tasks: list[dict] | None = None) -> bool:
        """A dependency is satisfied once its work is complete (IN_REVIEW/APPROVED/DONE).
        Gates are a separate ledger: gate tasks consume IN_REVIEW work, and a failing gate
        pulls the producer back to CHANGES_REQUESTED, which un-satisfies its dependants."""
        tasks = tasks or self.list(correlation_id=task["correlation_id"])
        ok = {t["task_id"] for t in tasks if t["state"] in SATISFIED_STATES}
        return all(d in ok for d in task["depends_on"])

    def ready(self, correlation_id: str | None = None) -> list[dict]:
        """Schedulable tasks: PLANNED/RETRY (or CREATED/VALIDATED) with all dependencies satisfied."""
        tasks = self.list(correlation_id=correlation_id)
        return [t for t in tasks
                if t["state"] in (S.PLANNED.value, S.RETRY.value, S.CREATED.value, S.VALIDATED.value)
                and self.deps_satisfied(t, tasks)]

    def _row(self, row) -> dict:
        d = dict(row)
        for c in _JSON_COLS:
            d[c] = json.loads(d[c] or ("{}" if c == "budget" else "[]"))
        try:
            d["notes_json"] = json.loads(d["notes"]) if d.get("notes") else {}
            if not isinstance(d["notes_json"], dict):
                d["notes_json"] = {}
        except json.JSONDecodeError:
            d["notes_json"] = {}
        return d

    def set_notes(self, task_id: str, **kv) -> dict:
        notes = self.get(task_id)["notes_json"]
        notes.update(kv)
        return self.update(task_id, notes=json.dumps(notes))
