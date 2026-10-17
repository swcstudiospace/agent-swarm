"""Subagent lifecycle manager.

Boring, no needless abstraction: centralizes spawn of concurrent subagent sessions
for the runner (swarm_run) and integrates with hook init/trigger. Enhanced for
gsd-autonomous OMP concurrent: per-subagent status streaming to OMP session,
ID registration, OMP gsd emit integrate via observability.
Wired for wave1 gsd-autonomous in OMP and wave1: immediate emit calls for spawn events,
subagent IDs, task slices (partition), real-time status to OMP; drive states;
support concurrent observable updates; add support for consolidated, audit. Verbatim.

Resilience wave3 match: wrap acquire/release/spawn (new_subagent_id) with 5x exp backoff 1s base+jitter;
300s per-sub overdue watchdog (threading.Timer, started when the slot is acquired; it flags the
subagent and never releases it or reports completion); pre spawn val;
isolate errors with try; wire to gsd states.

- unique IDs: subagent-<task>-<shortuuid>-<ts>
- timestamp logging: via runlog.emit on spawn/complete (with duration)
- bounded concurrency: hard cap at 4 (configurable default), BoundedSemaphore
- acquire/release slots around execution; streams status
- even partition hook for workload slices at launch (for OMP gsd-autonomous wave1)
- prior init/trigger + OMP: trigger can call for detached; supports OMP session visibility
- immediate emits: spawn, subagent_id, slices, status, consolidated, audit
- states: integrate LOADING, INITIALIZING, RUNNING, SPAWNING, PARTITIONING, etc via state_store or emits
- workspace: state and status writes go to the root of the task they report

Acceptance: concurrent spawns succeed with unique logged IDs, per-unit status/IDs visible,
never >4 active. SPE-13819 n3.3-5. wave1 gsd-autonomous consolidated audit.
Resilience: 5x backoff+ jit, 300s overdue watchdog, pre-val, err-isolate with try, gsd wire.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .runlog import emit
from .observability import stream_subagent_status, emit_gsd_omp, even_partition, CONTROLLER_AGENT
import random


def _exp_backoff(attempt: int, base: float = 1.0) -> float:
    """5x exp backoff with 1s base + jitter. For wrap acquire/release/spawn."""
    sleep = min(base * (2 ** attempt), 60.0)
    jitter = random.uniform(0, 0.5)
    return sleep + jitter


def _pre_spawn_validate(task_id: str) -> bool:
    """Pre-spawn validation. Called before spawn/id gen."""
    try:
        if not task_id or not isinstance(task_id, str) or len(task_id.strip()) == 0:
            raise ValueError("pre_spawn_val: invalid or empty task_id")
        return True
    except Exception:
        # isolate
        raise


DEFAULT_OVERDUE_S = 300.0


def _resolve_overdue(overdue_after_s: float | None) -> float:
    """Overdue threshold in seconds: DEFAULT_OVERDUE_S when None; otherwise must be finite and positive."""
    if overdue_after_s is None:
        return DEFAULT_OVERDUE_S
    value = float(overdue_after_s)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"overdue_after_s must be a finite positive number of seconds, got {overdue_after_s!r}")
    return value


class _SubTimeout:
    """Overdue watchdog: calls on_overdue(sid) once after after_s seconds if the subagent is still running. Cancel on release."""
    def __init__(self, sid: str, on_overdue, after_s: float):
        self.sid = sid
        self.on_overdue = on_overdue
        self.after_s = after_s
        self.timer: threading.Timer | None = None
    def start(self) -> None:
        try:
            self.timer = threading.Timer(self.after_s, self._on_timeout)
            self.timer.daemon = True
            self.timer.start()
        except Exception:
            pass  # isolate
    def _on_timeout(self) -> None:
        try:
            self.on_overdue(self.sid)
        except Exception:
            pass  # isolate errors
    def cancel(self) -> None:
        try:
            if self.timer:
                self.timer.cancel()
                self.timer = None
        except Exception:
            pass  # isolate


def _drive_gsd_state(state: str, *, root: str | Path | None = None, **extra) -> None:
    """Drive states LOADING etc via state store calls or emits. For wave1 gsd-autonomous OMP concurrent.
    Verbatim integration of all states. Immediate real-time to OMP. Support consolidated, audit.
    State is written to the workspace at `root` (current directory when None). Errors isolated with try."""
    try:
        from swarm.state_store import get_swarm_state_store
        ss = get_swarm_state_store(root=root)
        ss.append_gsd_line(state)
        ss.update(state, extra=extra or None)
    except Exception:
        # fallback immediate emit
        emit("gsd.state", {"state": state, **extra}, source=f"{CONTROLLER_AGENT}@subagent_lifecycle", root=root)
    # immediate real-time status emit to OMP
    try:
        emit_gsd_omp("state", state=state, root=root, **extra)
    except Exception:
        pass  # isolate

class SubagentLifecycleManager:
    """Lifecycle for subagent spawns in run/executor + OMP gsd-autonomous.

    Enforces max_concurrent <=4 always (configurable default 4).
    Used by swarm_run's ThreadPool batch and hook_trigger detached spawns.
    Enhanced: streams per-subagent status+ID to OMP session for visibility;
    integrates emit_gsd_omp; supports even partition at launch. SPE-13819.
    wave1 gsd-autonomous in OMP and wave1: immediate emit calls for spawn events, subagent IDs,
    task slices (partition), real-time status to OMP; drive states; support concurrent observable updates;
    add support for consolidated, audit. Verbatim.

    Resilience adds (match python side): wrap acquire/release/spawn(new_subagent_id) w/ 5x exp backoff 1s+jit;
    per-sub overdue watchdog (300s by default; overdue_after_s overrides; flags only, release happens when the task ends); pre-spawn-val; isolate w/ try; wire gsd states.

    root: default workspace for status writes; new_subagent_id(root=...) overrides it per task.
    """
    HARD_MAX = 4

    def __init__(self, max_concurrent: int = 4, *, root: str | Path | None = None, overdue_after_s: float | None = None) -> None:
        overdue = _resolve_overdue(overdue_after_s)
        try:
            self.root = Path(root) if root is not None else None
            self.overdue_after_s = overdue
            _drive_gsd_state("LOADING", lifecycle="init", wave="wave1", consolidated=False, root=self.root)
            self.max_concurrent = min(self.HARD_MAX, max(1, int(max_concurrent or 1)))
            self._sem = threading.BoundedSemaphore(self.max_concurrent)
            self._active: dict[str, dict[str, Any]] = {}
            self._lock = threading.Lock()
            self._timeouts: dict[str, _SubTimeout] = {}
            _drive_gsd_state("INITIALIZING", max=self.max_concurrent, wave="wave1", root=self.root)
            # immediate emit for concurrent observable updates + OMP session
            emit_gsd_omp("init.concurrent", max=self.max_concurrent, wave1="gsd-autonomous", audit="ready", root=self.root)
            stream_subagent_status("lifecycle", "initialized", phase="wave1", root=self.root)
        except Exception:
            # isolate init errors
            self._timeouts = {}
            raise
    def new_subagent_id(self, task_id: str, *, root: str | Path | None = None) -> str:
        """Return unique ID for a task whose status goes to workspace `root` (manager root when None).
        The overdue watchdog starts at acquire, not here, so tasks queued behind the semaphore are not flagged."""
        _pre_spawn_validate(task_id)
        task_root = self.root if root is None else Path(root)
        for attempt in range(5):
            try:
                _drive_gsd_state("SPAWNING", task_id=task_id, wave="wave1", root=task_root)
                ts = time.time()
                short = uuid.uuid4().hex[:12]
                sid = f"subagent-{task_id}-{short}-{int(ts * 1000)}"
                with self._lock:
                    self._active[sid] = {
                        "task_id": task_id,
                        "spawn_ts": ts,
                        "mono_start": time.monotonic(),
                        "root": task_root,
                        "holds_slot": False,
                        "overdue": False,
                    }
                emit(
                    "subagent.spawn.immediate",
                    {
                        "subagent_id": sid,
                        "task_id": task_id,
                        "spawn_ts": ts,
                        "max_concurrent": self.max_concurrent,
                        "wave": "wave1",
                    },
                    source=f"{CONTROLLER_AGENT}@subagent_lifecycle",
                    task_id=task_id,
                    root=task_root,
                )
                emit(
                    "spawn.event",
                    {"subagent_id": sid, "task_id": task_id, "slice": task_id},
                    source=f"{CONTROLLER_AGENT}@subagent_lifecycle",
                    root=task_root,
                )
                stream_subagent_status(sid, "spawned", task_id=task_id, phase="launch", wave="wave1", root=task_root)
                emit_gsd_omp("spawn", subagent_id=sid, task_id=task_id, max=self.max_concurrent, wave1="gsd-autonomous", root=task_root)
                _drive_gsd_state("RUNNING", subagent_id=sid, task_id=task_id, wave="wave1", root=task_root)
                emit_gsd_omp("consolidated", subagent_id=sid, task_id=task_id, audit="spawn", root=task_root)
                return sid
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(_exp_backoff(attempt))
        raise RuntimeError("spawn backoff exhausted")

    def _root_of(self, sid: str) -> Path | None:
        """Workspace root recorded for sid at spawn; manager root if sid is untracked."""
        with self._lock:
            info = self._active.get(sid)
        return info["root"] if info is not None else self.root

    def _mark_slot_held(self, sid: str) -> None:
        """Record that sid now holds a semaphore slot and start its overdue watchdog.
        Caller has just acquired the semaphore; if sid is untracked, nothing would ever free that slot, so it is given back."""
        with self._lock:
            info = self._active.get(sid)
            if info is None:
                tmo = None
            else:
                info["acquired_ts"] = time.time()
                info["holds_slot"] = True
                tmo = _SubTimeout(sid, self._on_overdue, self.overdue_after_s)
                self._timeouts[sid] = tmo
        if tmo is None:
            self._sem.release()
            return
        tmo.start()

    def _on_overdue(self, sid: str) -> None:
        """Watchdog expiry: flag sid as still running past the threshold. It does not free the slot or report
        completion; release() does that once the task has actually ended."""
        with self._lock:
            info = self._active.get(sid)
            if info is None:
                return  # already released; not overdue
            info["overdue"] = True
            # Streamed under the lock so a concurrent release() cannot publish "complete" before this "overdue".
            stream_subagent_status(sid, "overdue", task_id=info.get("task_id"), phase="run", wave="wave1", root=info["root"])

    def acquire(self, sid: str) -> None:
        """Acquire wrapped with 5x exp backoff 1s+jit + try isolate. Execution begins once the slot is held."""
        root = self._root_of(sid)
        for attempt in range(5):
            try:
                _drive_gsd_state("ACQUIRED", subagent_id=sid, wave="wave1", root=root)
                if not self._sem.acquire(timeout=30.0):
                    time.sleep(_exp_backoff(attempt))
                    continue
                self._mark_slot_held(sid)
                stream_subagent_status(sid, "acquired", phase="run", wave="wave1", root=root)
                emit_gsd_omp("acquired.immediate", subagent_id=sid, wave1="gsd-autonomous", root=root)
                emit_gsd_omp("consolidated", subagent_id=sid, audit="acquire", root=root)
                return
            except Exception:
                if attempt == 4:
                    self._sem.acquire()
                    self._mark_slot_held(sid)
                    stream_subagent_status(sid, "acquired", phase="run", wave="wave1", root=root)
                    return
                time.sleep(_exp_backoff(attempt))

    def release(self, sid: str) -> dict[str, Any] | None:
        """Release sid after its task has ended: cancel the watchdog, free the slot once, report completion.
        Idempotent: a repeat call for the same sid frees nothing and reports nothing, returning None."""
        with self._lock:
            info = self._active.pop(sid, None)
            tmo = self._timeouts.pop(sid, None)
        if tmo is not None:
            tmo.cancel()
        if info is None:
            return None
        if info.get("holds_slot"):
            self._sem.release()
        self._report_complete(sid, info)
        return info

    def _report_complete(self, sid: str, info: dict[str, Any]) -> None:
        """Emit completion for a released subagent. Retried with 5x backoff; errors isolated."""
        root = info.get("root")
        task_id = info.get("task_id")
        dur = time.monotonic() - info.get("mono_start", time.monotonic())
        for attempt in range(5):
            try:
                _drive_gsd_state("COMPLETE", subagent_id=sid, wave="wave1", root=root)
                emit(
                    "subagent.complete.immediate",
                    {
                        "subagent_id": sid,
                        "task_id": task_id,
                        "duration_s": round(dur, 3),
                        "complete_ts": time.time(),
                        "wave": "wave1",
                        "slice": task_id,
                    },
                    source=f"{CONTROLLER_AGENT}@subagent_lifecycle",
                    task_id=task_id,
                    root=root,
                )
                stream_subagent_status(sid, "complete", task_id=task_id, phase="done", wave="wave1", root=root)
                emit_gsd_omp("complete.immediate", subagent_id=sid, task_id=task_id, duration=round(dur, 3), wave1="gsd-autonomous", root=root)
                emit_gsd_omp("consolidated", subagent_id=sid, task_id=task_id, audit="release", root=root)
                return
            except Exception:
                if attempt == 4:
                    return
                time.sleep(_exp_backoff(attempt))

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._active.keys())

    def current_concurrency(self) -> int:
        return self.active_count()

    def even_partition(self, workload: list, n: int | None = None) -> list[list]:
        _drive_gsd_state("PARTITIONING", workload_len=len(workload), wave="wave1", root=self.root)
        n = n or self.max_concurrent
        slices = even_partition(workload, n)
        emit_gsd_omp("partition.slice.immediate", slices=len(slices), n=n, wave1="gsd-autonomous", subagent_slices=slices, root=self.root)
        stream_subagent_status("partition", "sliced", phase="wave1", slices=len(slices), root=self.root)
        emit_gsd_omp("consolidated", audit="partition", slices=len(slices), root=self.root)
        return slices

    def register_omp(self, session_ref: str | None = None) -> dict:
        _drive_gsd_state("REGISTERED", session=session_ref, wave="wave1", root=self.root)
        with self._lock:
            active = dict(self._active)
        emit_gsd_omp("omp.register.immediate", subagent_ids=list(active.keys()), session=session_ref, wave1="gsd-autonomous", root=self.root)
        for sid, info in active.items():
            stream_subagent_status(sid, "registered", phase="omp", wave="wave1", root=info["root"])
            emit_gsd_omp("status", subagent_id=sid, status="omp-registered", root=info["root"])
        emit_gsd_omp("consolidated", audit="register", session=session_ref, root=self.root)
        return {"max": self.max_concurrent, "active": list(active.keys()), "count": len(active)}

    def stream_all_status(self, status: str = "active") -> None:
        _drive_gsd_state("STREAMING", status=status, wave="wave1", root=self.root)
        with self._lock:
            for sid, info in list(self._active.items()):
                root = info["root"]
                stream_subagent_status(sid, status, task_id=info.get("task_id"), phase="omp", wave="wave1", root=root)
                emit_gsd_omp(status, subagent_id=sid, task_id=info.get("task_id"), wave1="gsd-autonomous", root=root)
                emit_gsd_omp("consolidated", subagent_id=sid, audit="stream", status=status, root=root)

# Singleton-ish for hook integration without passing everywhere (boring).
# Init on first use; runner overrides with its arg.
_global_mgr: SubagentLifecycleManager | None = None


def get_manager(
    max_concurrent: int | None = None,
    *,
    root: str | Path | None = None,
    overdue_after_s: float | None = None,
) -> SubagentLifecycleManager:
    """Get (or init) the lifecycle mgr. Default 4 for gsd-autonomous OMP concurrent (even partition default).
    OMP command registers for status streams. wire wave1 gsd-autonomous immediate emits.
    root: default workspace for status writes; None keeps the existing manager's root.
    overdue_after_s: overdue threshold in seconds; None keeps the existing manager's threshold (300 by default)."""
    global _global_mgr
    if _global_mgr is None:
        _drive_gsd_state("MANAGER_INIT", max=max_concurrent or 4, wave="wave1", root=root)
        _global_mgr = SubagentLifecycleManager(max_concurrent or 4, root=root, overdue_after_s=overdue_after_s)
    else:
        # rebind only if caller wants different (runner does)
        want_max = _global_mgr.max_concurrent if max_concurrent is None else max_concurrent
        want_root = _global_mgr.root if root is None else Path(root)
        want_overdue = _global_mgr.overdue_after_s if overdue_after_s is None else _resolve_overdue(overdue_after_s)
        if want_max != _global_mgr.max_concurrent or want_root != _global_mgr.root or want_overdue != _global_mgr.overdue_after_s:
            _drive_gsd_state("MANAGER_REBIND", max=want_max, wave="wave1", root=want_root)
            _global_mgr = SubagentLifecycleManager(want_max, root=want_root, overdue_after_s=want_overdue)
    emit_gsd_omp("manager.get", wave1="gsd-autonomous", root=_global_mgr.root)
    emit_gsd_omp("consolidated", audit="get_manager", root=_global_mgr.root)
    return _global_mgr


def reset_manager() -> None:
    """Test hook only."""
    global _global_mgr
    _global_mgr = None
