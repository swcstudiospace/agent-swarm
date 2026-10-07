"""Append-only JSONL run log — the local stand-in for the NATS bus / OTel plane.

Every agent script emits its inputs, outputs and verdicts here so the orchestrator
(and humans) can audit the causal chain by correlation_id.
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path

from .paths import swarm_dir


def _log_file(root: str | Path | None, *, create: bool) -> Path:
    return swarm_dir(root, create=create) / "events.jsonl"


def redact_leases(value):
    """A copy of `value` without substrate lease ids. A lease id is the holder's fencing token (LEASE-02): the Task Store
    mirrors it in notes.lease for A01's own release, and no run-log record, nor its tee, carries it. A task row carries it
    twice, in `notes_json` and in the raw `notes` JSON text."""
    if isinstance(value, dict):
        return {k: redact_leases(v) for k, v in value.items() if k != "lease_id"}
    if isinstance(value, (list, tuple)):
        return [redact_leases(v) for v in value]
    if isinstance(value, str) and '"lease_id"' in value:
        try:
            return json.dumps(redact_leases(json.loads(value)))
        except ValueError:
            return value
    return value


def emit(event_type: str, payload: dict, *, source: str, correlation_id: str | None = None,
         task_id: str | None = None, root: str | Path | None = None, tee: bool = True) -> dict:
    """Append one record to the run log; `tee=False` (dry runs) keeps it local and never sends it to substrate. Lease ids
    are dropped from the payload (`redact_leases`); the caller's own payload object is left as it is."""
    log_file = _log_file(root, create=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": event_type,
        "source": source,
        "correlation_id": correlation_id,
        "task_id": task_id,
        "payload": redact_leases(payload),
        "msg_id": uuid.uuid4().hex,
    }
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    if not tee:
        return record
    try:  # substrate tee (ADR 0001 S1): after the local write, fail-open, a no-op without SUBSTRATE_URL
        from .substrate_tee import tee as tee_record
        tee_record(record, root)
    except Exception:  # noqa: BLE001 - the local log is the source of truth; the tee never disturbs it
        pass
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
