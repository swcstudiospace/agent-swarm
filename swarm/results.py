"""The one task.result path shared by the headless runner and `orch_status.py --ingest`.

Both callers do: parse_result → validate_result → apply_result → reconcile.

The ONE intended mode difference:
  * mode="headless": the session is over, so a final state of IN_PROGRESS is a contract failure
    (FAILED "E-CONTRACT: session ended in IN_PROGRESS"); a missing/invalid result also becomes
    FAILED E-CONTRACT and counts as an attempt (retry ladder → ESCALATED at max_attempts).
  * mode="ingest": IN_PROGRESS is a claim/heartbeat; PLANNED/RETRY/BLOCKED tasks are auto-claimed
    (CLAIMED → IN_PROGRESS, event task.claimed) before the reported state is applied. Invalid input
    is rejected with E-CONTRACT and no transition (never consumes an attempt).
Rejections on both paths emit task.result.rejected {task_id, mode, reason}.
"""
from __future__ import annotations
import json
import re
from typing import Callable

from .errors import SwarmError, ErrorCode
from .schema import load_schema, validate
from .taskstore import TaskStore, TaskState as S

JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
SCHEMA_NAME = "task.result.v1"
Emit = Callable[[str, dict], object]


def parse_result(text: str) -> dict | None:
    """Last parseable fenced ```json block, else the whole text as a bare JSON object."""
    for raw in reversed(JSON_BLOCK.findall(text or "")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    try:
        obj = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def validate_result(result, *, task_id: str) -> dict:
    """Schema-check `result` and bind it to the assigned task; raise E-CONTRACT otherwise."""
    if result is None:
        raise SwarmError(ErrorCode.E_CONTRACT, "no JSON result block from agent", task_id=task_id)
    errors = validate(result, load_schema(SCHEMA_NAME))
    if errors:
        raise SwarmError(ErrorCode.E_CONTRACT, f"task.result invalid: {'; '.join(errors)}"[:500], task_id=task_id)
    if result["task_id"] != task_id:
        raise SwarmError(ErrorCode.E_CONTRACT, f"task_id mismatch: result names {result['task_id']!r}, assigned {task_id!r}",
                         task_id=task_id)
    return result


def reject(store: TaskStore, task_id: str, *, reason: str, mode: str, emit: Emit) -> str:
    """Record a rejected result. Headless: FAILED (counts as an attempt). Ingest: event only."""
    emit("task.result.rejected", {"task_id": task_id, "mode": mode, "reason": reason})
    if mode == "headless":
        store.transition(task_id, S.FAILED, reason=reason[:500])
        return S.FAILED.value
    return store.get(task_id)["state"]


def _agent_verdict_feedback(store: TaskStore, task: dict, result: dict) -> None:
    """A gate agent's verdicts{} is advisory text only (D-12): fail entries become feedback on the gate task's own
    gate_for targets. Verdict rows are written solely by the gate scripts (swarm.verdicts)."""
    notes = task["notes_json"]
    gate = notes.get("gate")
    if not gate:
        return
    for target, v in (result.get("verdicts") or {}).items():
        if target not in notes.get("gate_for", []) or not isinstance(v, dict) or v.get("verdict") != "fail":
            continue
        fb = store.get(target)["notes_json"].get("feedback", [])
        fb.append({"gate": gate, "source": "agent", "findings": v.get("findings", [])})
        store.set_notes(target, feedback=fb)


def apply_result(store: TaskStore, task: dict, *, agent_id: str, result: dict, meta: dict, emit: Emit, mode: str) -> str:
    """Apply a validated result to the assigned task only; returns the task's new state."""
    tid = task["task_id"]
    state = result["state"]
    if mode == "headless" and state == S.IN_PROGRESS.value:
        return reject(store, tid, reason="E-CONTRACT: session ended in IN_PROGRESS", mode=mode, emit=emit)
    if mode == "ingest" and task["state"] in (S.PLANNED.value, S.RETRY.value, S.BLOCKED.value):
        store.transition(tid, S.CLAIMED, actor=agent_id, reason="claimed via task.result ingest")
        store.transition(tid, S.IN_PROGRESS, actor=agent_id, reason="lease started")
        emit("task.claimed", {"task_id": tid, "agent": agent_id, "mode": mode})
    current = store.get(tid)["state"]
    if state == S.BLOCKED.value:
        target, reason = S.BLOCKED, str(result.get("needs", "blocked"))[:500]
    elif state == S.FAILED.value:
        target, reason = S.FAILED, json.dumps(result.get("error", {}))[:500]
    elif state == S.IN_REVIEW.value:
        target, reason = S.IN_REVIEW, "agent reported IN_REVIEW"
    else:  # ingest heartbeat: only legal while (now) IN_PROGRESS
        target, reason = None, ""
        if current != S.IN_PROGRESS.value:
            raise SwarmError(ErrorCode.E_CONTRACT, f"illegal transition {current} → IN_PROGRESS for {tid}", task_id=tid)
    if target is not None:
        store.transition(tid, target, actor=agent_id, reason=reason)
    store.set_notes(tid, result=result, meta=meta)
    for o in result.get("outputs", []) or []:
        store.add_artifact(tid, kind=o.get("kind", "artifact"), uri=o.get("uri", ""), version=str(o.get("version", "1")),
                           digest=o.get("digest", ""), producer=agent_id)
    if target is S.IN_REVIEW:
        _agent_verdict_feedback(store, task, result)
    return store.get(tid)["state"]


def reconcile(store: TaskStore, corr: str, emit: Emit) -> list[str]:
    """Apply A01 gate/rework rules to IN_REVIEW tasks; reopen gate tasks after rework."""
    notes_log = []
    for t in store.list(correlation_id=corr, state=S.IN_REVIEW.value):
        tid = t["task_id"]
        latest = store.latest_verdicts(tid, verified_only=True)
        failing = [g for g in store.required_gates(tid) if latest.get(g, {}).get("verdict") == "fail"]
        if failing:
            before = t["rework_loops"]
            nt = store.transition(tid, S.CHANGES_REQUESTED, reason=f"gates failed: {failing}")
            if nt["state"] == S.ESCALATED.value:
                emit("escalation.request", {"task_id": tid, "reason_code": "E-CONTRACT", "evidence": failing,
                                            "options": ["human review", "cancel", "waive gate (L3)"]})
                notes_log.append(f"{tid}: ESCALATED after {before} rework loops")
            else:
                store.transition(tid, S.IN_PROGRESS, reason="rework loop")
                notes_log.append(f"{tid}: CHANGES_REQUESTED → rework #{nt['rework_loops']} ({failing})")
                # reopen gate tasks that target this task so they re-run after rework
                for g in store.list(correlation_id=corr):
                    if tid in g["notes_json"].get("gate_for", []) and g["state"] in (S.DONE.value, S.IN_REVIEW.value, S.APPROVED.value):
                        new_id = f"{g['task_id']}.r{nt['rework_loops']}"
                        try:
                            store.get(new_id)
                        except SwarmError:
                            store.create(task_id=new_id, correlation_id=corr, capability=g["capability"], title=g["title"] + " (rerun)",
                                         agent_id=g["agent_id"], dag_depth=g["dag_depth"], depends_on=g["depends_on"],
                                         acceptance=g["acceptance"], budget=g["budget"], risk_class=g["risk_class"],
                                         priority=g["priority"], notes={k: v for k, v in g["notes_json"].items() if k not in ("result", "meta")})
                            store.transition(new_id, S.VALIDATED)
                            store.transition(new_id, S.PLANNED, reason="gate rerun")
                            # downstream of the old gate must now wait for the rerun too
                            for d in store.list(correlation_id=corr):
                                if g["task_id"] in d["depends_on"] and new_id not in d["depends_on"] and d["state"] not in (S.DONE.value,):
                                    store.update(d["task_id"], depends_on=d["depends_on"] + [new_id])
            continue
        if not store.missing_gates(tid):
            store.transition(tid, S.APPROVED, reason="all required gates pass")
            store.transition(tid, S.DONE, reason="approved")
            notes_log.append(f"{tid}: DONE")
    return notes_log
