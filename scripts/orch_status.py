#!/usr/bin/env python3
"""A01 — swarm status, task transitions and task.status ingestion.

  python3 scripts/orch_status.py                              # table for latest correlation
  python3 scripts/orch_status.py --correlation-id <id> --json
  python3 scripts/orch_status.py --transition T-be IN_PROGRESS --reason "lease granted"
  python3 scripts/orch_status.py --ingest result.json         # task.result payload from an agent
  python3 scripts/orch_status.py --history T-be
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript  # noqa: E402
from swarm.taskstore import TaskStore  # noqa: E402
from swarm.runlog import SWARM_DIR, read_events  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402
from swarm.results import parse_result, validate_result, apply_result, reconcile, reject  # noqa: E402


def latest_correlation() -> str | None:
    f = SWARM_DIR / "latest_correlation"
    return f.read_text().strip() if f.exists() else None


def ingest(store: TaskStore, path: Path, ctx) -> dict:
    """task.result file → shared swarm.results path (same as the headless runner)."""
    result = parse_result(path.read_text(encoding="utf-8"))
    tid = ctx.task_id or (result or {}).get("task_id")
    if not isinstance(tid, str):
        ctx.emit("task.result.rejected", {"task_id": None, "mode": "ingest", "reason": "no task_id in result"})
        raise SwarmError(ErrorCode.E_CONTRACT, "task.result has no task_id (use --task-id or include task_id)")
    try:
        task = store.get(tid)
    except SwarmError as e:
        raise SwarmError(ErrorCode.E_INPUT, f"unknown task {tid}", task_id=tid) from e
    try:
        result = validate_result(result, task_id=tid)
        state = apply_result(store, task, agent_id=result.get("agent_id") or task["agent_id"] or "agent", result=result,
                             meta={"ingest": str(path)}, emit=ctx.emit, mode="ingest")
    except SwarmError as e:
        if e.code is ErrorCode.E_CONTRACT:
            reject(store, tid, reason=str(e), mode="ingest", emit=ctx.emit)
        raise
    log = reconcile(store, task["correlation_id"], ctx.emit)
    t = store.get(tid)
    return {"status": "ok", "task": t, "reconcile": log, "summary": f"ingested task.result for {tid} → {state} (now {t['state']})"}


def run(args, ctx) -> dict:
    store = TaskStore()
    corr = ctx.correlation_id or latest_correlation()

    if args.history:
        return {"status": "ok", "task_id": args.history, "history": store.history(args.history),
                "verdicts": store.latest_verdicts(args.history), "summary": f"history for {args.history}"}

    if args.transition:
        tid, state = args.transition
        if ctx.dry_run:
            return {"status": "ok", "summary": f"dry-run: would transition {tid} → {state}"}
        t = store.transition(tid, state, actor="A01", reason=args.reason or "manual")
        ctx.emit("task.transition", {"task_id": tid, "state": t["state"], "reason": args.reason})
        return {"status": "ok", "task": t, "summary": f"{tid} → {t['state']}"}

    if args.ingest:
        return ingest(store, Path(args.ingest), ctx)

    tasks = store.list(correlation_id=corr)
    if not tasks:
        return {"status": "ok", "correlation_id": corr, "tasks": [], "summary": "no tasks (run orch_plan.py first)"}
    ready = {t["task_id"] for t in store.ready(corr)}
    rows, counts = [], {}
    for t in tasks:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
        verdicts = {g: v["verdict"] for g, v in store.latest_verdicts(t["task_id"]).items()}
        rows.append({"task_id": t["task_id"], "agent": t["agent_id"], "state": t["state"], "depth": t["dag_depth"],
                     "attempt": t["attempt"], "rework": t["rework_loops"], "ready": t["task_id"] in ready,
                     "gates_required": store.required_gates(t["task_id"]), "verdicts": verdicts,
                     "missing_gates": store.missing_gates(t["task_id"]) if t["state"] == "IN_REVIEW" else [],
                     "depends_on": t["depends_on"], "outputs": len(t["outputs"])})
    escalated = [r["task_id"] for r in rows if r["state"] == "ESCALATED"]
    done = all(r["state"] in ("DONE", "CANCELLED") for r in rows)
    lines = [f"{'TASK':<14}{'AGENT':<7}{'STATE':<19}{'ATT':<4}{'RW':<3}{'READY':<6}{'GATES'}"]
    for r in rows:
        gates = ",".join(f"{g}={r['verdicts'].get(g, '·')}" for g in r["gates_required"]) or "-"
        lines.append(f"{r['task_id']:<14}{r['agent'] or '?':<7}{r['state']:<19}{r['attempt']:<4}{r['rework']:<3}"
                     f"{'yes' if r['ready'] else '':<6}{gates}")
    lines.append(f"\ncounts: {counts}   escalated: {escalated or 'none'}   complete: {done}")
    return {"status": "ok", "correlation_id": corr, "tasks": rows, "counts": counts, "escalated": escalated,
            "complete": done, "events": len(read_events(correlation_id=corr)), "summary": "\n".join(lines)}


def add_args(p):
    p.add_argument("--transition", nargs=2, metavar=("TASK_ID", "STATE"), help="A01-only legal transition")
    p.add_argument("--reason", default="")
    p.add_argument("--ingest", help="path to a task.result JSON (swarm/schemas/task.result.v1.json) from an agent")
    p.add_argument("--history", metavar="TASK_ID")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "orch_status", run, description=__doc__, add_args=add_args).main())
