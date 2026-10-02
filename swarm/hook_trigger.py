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
    return bool(os.environ.get("AIO_SWARM_AFTER_ORCH") or os.environ.get("SWARM_AFTER_ORCH"))


def compute_trigger_id(session_id: str, transcript_fp: str, corr: str | None = None) -> str:
    """n3 ID scheme: sha256(session|fp|corr)[:16]"""
    key = f"{session_id}|{transcript_fp}|{corr or ''}".encode()
    return hashlib.sha256(key).hexdigest()[:16]


def fire_if_ready(payload: str | dict, *, session_id: str = "unknown", corr: str | None = None, root: str | Path | None = None) -> dict:
    """Idempotent fire. Returns {fired, deduped, id, event}."""
    if not is_opt_in():
        return {"fired": False, "reason": "opt-in-off"}
    event = detect_completion(payload)
    if not event:
        return {"fired": False, "reason": "no-signal"}
    fp = hashlib.sha256(str(payload)[:2048].encode()).hexdigest()[:8]
    tid = compute_trigger_id(session_id, fp, corr)
    store = get_trigger_store(root=root)
    if store.seen_trigger(tid, correlation_id=corr, brief_hash=fp):
        emit_hook_fired("a01_complete", corr=corr, deduped=True, root=root)
        return {"fired": False, "deduped": True, "id": tid}
    # fire side effect (stub: emit + state update; real: spawn gsd or parallel run)
    emit_hook_fired("a01_complete", corr=corr, deduped=False, root=root, event=event)
    ss = get_swarm_state_store(root=root)
    ss.update("FIRED", corr=corr, extra={"last_trigger": tid})
    # TODO in later wave: actual detached kick for gsd-autonomous or parallel swarm
    return {"fired": True, "deduped": False, "id": tid, "event": event}
