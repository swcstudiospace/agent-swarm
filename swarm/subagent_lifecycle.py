"""Subagent lifecycle manager.

Boring, no needless abstraction: centralizes spawn of concurrent subagent sessions
for the runner (swarm_run) and integrates with hook init/trigger. Enhanced for
gsd-autonomous OMP concurrent: per-subagent status streaming to OMP session,
ID registration, OMP gsd emit integrate via observability.
Wired for wave1 gsd-autonomous in OMP and wave1: immediate emit calls for spawn events,
subagent IDs, task slices (partition), real-time status to OMP; drive states;
support concurrent observable updates; add support for consolidated, audit. Verbatim.

Resilience wave3 match: wrap acquire/release/spawn (new_subagent_id) with 5x exp backoff 1s base+jitter;
300s per sub timeout + cleanup (threading.Timer cancel, release on timeout); pre spawn val;
isolate errors with try; wire to gsd states.

- unique IDs: subagent-<task>-<shortuuid>-<ts>
- timestamp logging: via runlog.emit on spawn/complete (with duration)
- bounded concurrency: hard cap at 4 (configurable default), BoundedSemaphore
- acquire/release slots around execution; streams status
- even partition hook for workload slices at launch (for OMP gsd-autonomous wave1)
- prior init/trigger + OMP: trigger can call for detached; supports OMP session visibility
- immediate emits: spawn, subagent_id, slices, status, consolidated, audit
- states: integrate LOADING, INITIALIZING, RUNNING, SPAWNING, PARTITIONING, etc via state_store or emits

Acceptance: concurrent spawns succeed with unique logged IDs, per-unit status/IDs visible,
never >4 active. SPE-13819 n3.3-5. wave1 gsd-autonomous consolidated audit.
Resilience: 5x backoff+ jit, 300s timeout+cleanup, pre-val, err-isolate with try, gsd wire.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .runlog import emit
from .observability import stream_subagent_status, emit_gsd_omp, even_partition
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


class _SubTimeout:
    """300s per-sub timeout + cleanup: Timer cancel, release on timeout."""
    def __init__(self, sid: str, release_fn):
        self.sid = sid
        self.release_fn = release_fn
        self.timer: threading.Timer | None = None
    def start(self) -> None:
        try:
            self.timer = threading.Timer(300.0, self._on_timeout)
            self.timer.daemon = True
            self.timer.start()
        except Exception:
            pass  # isolate
    def _on_timeout(self) -> None:
        try:
            self.release_fn(self.sid)
        except Exception:
            pass  # isolate errors
    def cancel(self) -> None:
        try:
            if self.timer:
                self.timer.cancel()
                self.timer = None
        except Exception:
            pass  # isolate


def _drive_gsd_state(state: str, **extra) -> None:
    """Drive states LOADING etc via state store calls or emits. For wave1 gsd-autonomous OMP concurrent.
    Verbatim integration of all states. Immediate real-time to OMP. Support consolidated, audit.
    Errors isolated with try."""
    try:
        from swarm.state_store import get_swarm_state_store
        ss = get_swarm_state_store()
        ss.append_gsd_line(state)
        ss.update(state, extra=extra or None)
    except Exception:
        # fallback immediate emit
        emit("gsd.state", {"state": state, **extra}, source="subagent_lifecycle")
    # immediate real-time status emit to OMP
    try:
        emit_gsd_omp("state", state=state, **extra)
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
    per-sub 300s timeout + Timer cleanup, release on timeout; pre-spawn-val; isolate w/ try; wire gsd states.
    """
    HARD_MAX = 4

    def __init__(self, max_concurrent: int = 4) -> None:
        try:
            _drive_gsd_state("LOADING", lifecycle="init", wave="wave1", consolidated=False)
            self.max_concurrent = min(self.HARD_MAX, max(1, int(max_concurrent or 1)))
            self._sem = threading.BoundedSemaphore(self.max_concurrent)
            self._active: dict[str, dict[str, Any]] = {}
            self._lock = threading.Lock()
            self._timeouts: dict[str, _SubTimeout] = {}
            _drive_gsd_state("INITIALIZING", max=self.max_concurrent, wave="wave1")
            # immediate emit for concurrent observable updates + OMP session
            emit_gsd_omp("init.concurrent", max=self.max_concurrent, wave1="gsd-autonomous", audit="ready")
            stream_subagent_status("lifecycle", "initialized", phase="wave1")
        except Exception:
            # isolate init errors
            self._timeouts = {}
            raise
    def new_subagent_id(self, task_id: str) -> str:
        """Return unique ID... (resilience wrapped: 5x backoff, pre val, 300s timeout start)."""
        _pre_spawn_validate(task_id)
        for attempt in range(5):
            try:
                _drive_gsd_state("SPAWNING", task_id=task_id, wave="wave1")
                ts = time.time()
                short = uuid.uuid4().hex[:12]
                sid = f"subagent-{task_id}-{short}-{int(ts * 1000)}"
                with self._lock:
                    self._active[sid] = {
                        "task_id": task_id,
                        "spawn_ts": ts,
                        "mono_start": time.monotonic(),
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
                    source="subagent_lifecycle",
                    task_id=task_id,
                )
                emit(
                    "spawn.event",
                    {"subagent_id": sid, "task_id": task_id, "slice": task_id},
                    source="subagent_lifecycle",
                )
                stream_subagent_status(sid, "spawned", task_id=task_id, phase="launch", wave="wave1")
                emit_gsd_omp("spawn", subagent_id=sid, task_id=task_id, max=self.max_concurrent, wave1="gsd-autonomous")
                _drive_gsd_state("RUNNING", subagent_id=sid, task_id=task_id, wave="wave1")
                emit_gsd_omp("consolidated", subagent_id=sid, task_id=task_id, audit="spawn")
                try:
                    tmo = _SubTimeout(sid, self.release)
                    tmo.start()
                    with self._lock:
                        self._timeouts[sid] = tmo
                except Exception:
                    pass
                return sid
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(_exp_backoff(attempt))
        raise RuntimeError("spawn backoff exhausted")

    def acquire(self, sid: str) -> None:
        """Acquire wrapped with 5x exp backoff 1s+jit + try isolate."""
        for attempt in range(5):
            try:
                _drive_gsd_state("ACQUIRED", subagent_id=sid, wave="wave1")
                if not self._sem.acquire(timeout=30.0):
                    time.sleep(_exp_backoff(attempt))
                    continue
                with self._lock:
                    if sid in self._active:
                        self._active[sid]["acquired_ts"] = time.time()
                stream_subagent_status(sid, "acquired", phase="run", wave="wave1")
                emit_gsd_omp("acquired.immediate", subagent_id=sid, wave1="gsd-autonomous")
                emit_gsd_omp("consolidated", subagent_id=sid, audit="acquire")
                return
            except Exception:
                if attempt == 4:
                    self._sem.acquire()
                    with self._lock:
                        if sid in self._active:
                            self._active[sid]["acquired_ts"] = time.time()
                    stream_subagent_status(sid, "acquired", phase="run", wave="wave1")
                    return
                time.sleep(_exp_backoff(attempt))

    def release(self, sid: str) -> dict[str, Any] | None:
        """Release wrapped w/ 5x backoff, timeout cancel, try isolate."""
        for attempt in range(5):
            try:
                _drive_gsd_state("COMPLETE", subagent_id=sid, wave="wave1")
                tmo = None
                with self._lock:
                    tmo = self._timeouts.pop(sid, None)
                if tmo:
                    try:
                        tmo.cancel()
                    except Exception:
                        pass
                self._sem.release()
                with self._lock:
                    info = self._active.pop(sid, None)
                if info:
                    dur = time.monotonic() - info.get("mono_start", time.monotonic())
                    emit(
                        "subagent.complete.immediate",
                        {
                            "subagent_id": sid,
                            "task_id": info.get("task_id"),
                            "duration_s": round(dur, 3),
                            "complete_ts": time.time(),
                            "wave": "wave1",
                            "slice": info.get("task_id"),
                        },
                        source="subagent_lifecycle",
                        task_id=info.get("task_id"),
                    )
                    stream_subagent_status(sid, "complete", task_id=info.get("task_id"), phase="done", wave="wave1")
                    emit_gsd_omp("complete.immediate", subagent_id=sid, task_id=info.get("task_id"), duration=round(dur, 3), wave1="gsd-autonomous")
                    emit_gsd_omp("consolidated", subagent_id=sid, task_id=info.get("task_id"), audit="release")
                    return info
                return None
            except Exception:
                if attempt == 4:
                    try:
                        self._sem.release()
                    except Exception:
                        pass
                    with self._lock:
                        info = self._active.pop(sid, None)
                        tmo = self._timeouts.pop(sid, None)
                    if tmo:
                        try: tmo.cancel()
                        except: pass
                    return info
                time.sleep(_exp_backoff(attempt))
        return None

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._active.keys())

    def current_concurrency(self) -> int:
        return self.active_count()

    def even_partition(self, workload: list, n: int | None = None) -> list[list]:
        _drive_gsd_state("PARTITIONING", workload_len=len(workload), wave="wave1")
        n = n or self.max_concurrent
        slices = even_partition(workload, n)
        emit_gsd_omp("partition.slice.immediate", slices=len(slices), n=n, wave1="gsd-autonomous", subagent_slices=slices)
        stream_subagent_status("partition", "sliced", phase="wave1", slices=len(slices))
        emit_gsd_omp("consolidated", audit="partition", slices=len(slices))
        return slices

    def register_omp(self, session_ref: str | None = None) -> dict:
        _drive_gsd_state("REGISTERED", session=session_ref, wave="wave1")
        with self._lock:
            active = dict(self._active)
        emit_gsd_omp("omp.register.immediate", subagent_ids=list(active.keys()), session=session_ref, wave1="gsd-autonomous")
        for sid in list(active.keys()):
            stream_subagent_status(sid, "registered", phase="omp", wave="wave1")
            emit_gsd_omp("status", subagent_id=sid, status="omp-registered")
        emit_gsd_omp("consolidated", audit="register", session=session_ref)
        return {"max": self.max_concurrent, "active": list(active.keys()), "count": len(active)}

    def stream_all_status(self, status: str = "active") -> None:
        _drive_gsd_state("STREAMING", status=status, wave="wave1")
        with self._lock:
            for sid, info in list(self._active.items()):
                stream_subagent_status(sid, status, task_id=info.get("task_id"), phase="omp", wave="wave1")
                emit_gsd_omp(status, subagent_id=sid, task_id=info.get("task_id"), wave1="gsd-autonomous")
                emit_gsd_omp("consolidated", subagent_id=sid, audit="stream", status=status)

# Singleton-ish for hook integration without passing everywhere (boring).
# Init on first use; runner overrides with its arg.
_global_mgr: SubagentLifecycleManager | None = None


def get_manager(max_concurrent: int | None = None) -> SubagentLifecycleManager:
    """Get (or init) the lifecycle mgr. Default 4 for gsd-autonomous OMP concurrent (even partition default).
    OMP command registers for status streams. wire wave1 gsd-autonomous immediate emits."""
    global _global_mgr
    if _global_mgr is None:
        _drive_gsd_state("MANAGER_INIT", max=max_concurrent or 4, wave="wave1")
        _global_mgr = SubagentLifecycleManager(max_concurrent or 4)
    elif max_concurrent is not None:
        # rebind only if caller wants different (runner does)
        if max_concurrent != _global_mgr.max_concurrent:
            _drive_gsd_state("MANAGER_REBIND", max=max_concurrent, wave="wave1")
            _global_mgr = SubagentLifecycleManager(max_concurrent)
    emit_gsd_omp("manager.get", wave1="gsd-autonomous")
    emit_gsd_omp("consolidated", audit="get_manager")
    return _global_mgr


def reset_manager() -> None:
    """Test hook only."""
    global _global_mgr
    _global_mgr = None
