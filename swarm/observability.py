"""Thin obs facade over runlog + state_store (n5/n2). 6-state, events, projection.
Boring; no mutation of source.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from .paths import swarm_dir
from .runlog import emit, read_events
from .state_store import get_swarm_state_store
def emit_hook_fired(hook: str, *, corr: str | None = None, deduped: bool = False, root: str | Path | None = None, **kw: Any) -> dict:
    return emit("hook-fired", {"hook": hook, "deduped": deduped, **kw}, source="hook_trigger", correlation_id=corr, root=root)


def emit_per_unit(task_id: str, agent: str, phase: str, state: str, *, corr: str | None = None, root: str | Path | None = None) -> dict:
    return emit("per-unit", {"task_id": task_id, "agent": agent, "phase": phase, "state": state}, source="execution", correlation_id=corr, task_id=task_id, root=root)


def emit_batch_end(round_n: int, dispatched: int, counts: dict, *, corr: str | None = None, root: str | Path | None = None) -> dict:
    return emit("batch-end", {"round": round_n, "dispatched": dispatched, "counts": counts}, source="swarm_run", correlation_id=corr, root=root)


def emit_stale(kind: str, reason: str, *, task_id: str | None = None, corr: str | None = None, root: str | Path | None = None) -> dict:
    return emit("stale", {"kind": kind, "reason": reason}, source="watchdog", correlation_id=corr, task_id=task_id, root=root)


def build_swarm_state(*, corr: str | None = None, root: str | Path | None = None) -> dict:
    """Build 6-state projection (n5)."""
    store = get_swarm_state_store(root=root)
    done = 0
    total = 0
    state = "RUNNING"
    try:
        from .taskstore import TaskStore
        ts = TaskStore(root=root)
        tasks = ts.list_tasks(correlation_id=corr) if corr else ts.list_tasks()
        total = len(tasks)
        if total > 0:
            done = sum(1 for t in tasks if t.get("state") in ("DONE", "APPROVED"))
            failures = sum(1 for t in tasks if t.get("state") in ("FAILED", "ESCALATED"))
            if done == total:
                state = "COMPLETE"
            elif failures > 0:
                state = "PARTIAL_FAILURE" if done > 0 else "FAILED"
            else:
                state = "RUNNING"
    except Exception:
        events = read_events(correlation_id=corr, root=root) if corr else []
        done = sum(1 for e in events if isinstance(e, dict) and e.get("type") in ("task.done", "task.approved", "task_done"))
        total = max(1, len(events))
        state = "COMPLETE" if done >= total else "RUNNING"

    progress = {"done": done, "total": total}
    data = store.update(state, corr=corr, progress=progress)
    # gsd append
    store.append_gsd_line(f"{corr or 'no-corr'} {state} {done}/{total}", root=root)
    return data


def write_claude_state(data: dict, *, root: str | Path | None = None) -> Path:
    """Atomic write .claude/swarm-state.json (n5)."""
    cdir = Path(swarm_dir(root, create=True)).parent / ".claude"
    cdir.mkdir(parents=True, exist_ok=True)
    p = cdir / "swarm-state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(p)
    return p


def update_from_events(corr: str | None = None, *, root: str | Path | None = None) -> dict:
    data = build_swarm_state(corr=corr, root=root)
    write_claude_state(data, root=root)
    return data
