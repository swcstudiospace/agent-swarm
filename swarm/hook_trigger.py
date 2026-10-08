"""Hook trigger core (n3/n2). Opt-in, ID hash, dedup, fire context or detached, thin.
"""

from __future__ import annotations
import hashlib
import os
from pathlib import Path

from .signal_detector import detect_completion
from .state_store import get_trigger_store, get_swarm_state_store
from .observability import emit_hook_fired


def is_opt_in() -> bool:
    v = os.environ.get("AIO_SWARM_AFTER_ORCH") or os.environ.get("SWARM_AFTER_ORCH")
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def compute_trigger_id(session_id: str, corr: str | None, event_type: str) -> str:
    """Stable ID (greptile: avoid payload jitter)."""
    key = f"{session_id}|{corr or ''}|{event_type}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def fire_if_ready(payload: str | dict, *, session_id: str = "unknown", corr: str | None = None, root: str | Path | None = None) -> dict:
    """Idempotent fire. Returns {fired, deduped, id, event}."""
    if not is_opt_in():
        return {"fired": False, "reason": "opt-in-off"}
    event = detect_completion(payload)
    if not event:
        return {"fired": False, "reason": "no-signal"}
    event_type = event.get("type", "unknown")
    tid = compute_trigger_id(session_id, corr, event_type)
    store = get_trigger_store(root=root)
    if store.seen_trigger(tid, correlation_id=corr, brief_hash=event_type):
        emit_hook_fired("a01_complete", corr=corr, deduped=True, root=root)
        return {"fired": False, "deduped": True, "id": tid}
    # fire side effect: record + update obs, then invoke existing Swarm entry (autonomous_run) detached for gsd+ultrathink parallel
    emit_hook_fired("a01_complete", corr=corr, deduped=False, root=root, event=event)
    ss = get_swarm_state_store(root=root)
    ss.update("FIRED", corr=corr, extra={"last_trigger": tid})
    try:
        import subprocess
        target_repo = Path(root).resolve() if root else Path.cwd().resolve()
        hook_path = Path(__file__).resolve().parent.parent / "hooks" / "autonomous_run.py"
        env = dict(os.environ)
        env["SWARM_CHILD"] = "0"
        env["AIO_SWARM_AFTER_ORCH"] = "1"
        subprocess.Popen(
            ["python3", str(hook_path), "--cwd", str(target_repo), "--brief", f"implement gsd-autonomous follow-on after a01 {corr or ''}"],
            cwd=str(target_repo),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass  # fail open
    return {"fired": True, "deduped": False, "id": tid, "event": event}
