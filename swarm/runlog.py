"""Append-only JSONL run log — the local stand-in for the NATS bus / OTel plane.

Every agent script emits its inputs, outputs and verdicts here so the orchestrator
(and humans) can audit the causal chain by correlation_id.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from .paths import swarm_dir


def _log_file(root: str | Path | None, *, create: bool) -> Path:
    return swarm_dir(root, create=create) / "events.jsonl"


def emit(event_type: str, payload: dict, *, source: str, correlation_id: str | None = None,
         task_id: str | None = None, root: str | Path | None = None) -> dict:
    log_file = _log_file(root, create=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": event_type,
        "source": source,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "payload": payload,
    }
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(*, correlation_id: str | None = None, task_id: str | None = None,
                event_type: str | None = None, root: str | Path | None = None) -> list[dict]:
    log_file = _log_file(root, create=False)
    if not log_file.exists():
        return []
    out = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if correlation_id and rec.get("correlation_id") != correlation_id:
            continue
        if task_id and rec.get("task_id") != task_id:
            continue
        if event_type and rec.get("type") != event_type:
            continue
        out.append(rec)
    return out
