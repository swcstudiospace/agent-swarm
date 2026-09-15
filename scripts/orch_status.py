#!/usr/bin/env python3
"""A01 — swarm status, task transitions and task.status ingestion.

  python3 scripts/orch_status.py                              # table for latest correlation
  python3 scripts/orch_status.py --correlation-id <id> --json
  python3 scripts/orch_status.py --transition T-be IN_PROGRESS --reason "lease granted"
  python3 scripts/orch_status.py --ingest status.json         # task.status payload from an agent
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

AGENT_REPORTABLE = {"IN_PROGRESS", "IN_REVIEW", "FAILED", "BLOCKED"}  # states agents may report


def latest_correlation() -> str | None:
    f = SWARM_DIR / "latest_correlation"
    return f.read_text().strip() if f.exists() else None


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
        payload = json.loads(Path(args.ingest).read_text())
        tid, state = payload["task_id"], payload["state"]
        if state not in AGENT_REPORTABLE:
            ctx.emit("task.status.rejected", {"task_id": tid, "state": state, "reason": "agents may not report this state"})
            raise SwarmError(ErrorCode.E_CONTRACT, f"agents may not report state {state}", task_id=tid)
        try:
            t = store.transition(tid, state, actor=payload.get("agent_id", "agent"), reason=payload.get("notes", "task.status"))
        except SwarmError as e:
            ctx.emit("task.status.rejected", {"task_id": tid, "state": state, "reason": str(e)})
            raise
        for a in payload.get("artifacts", []):
            store.add_artifact(tid, kind=a.get("kind", "artifact"), uri=a.get("uri", ""), version=str(a.get("version", "1")),
                               digest=a.get("digest", ""), producer=payload.get("agent_id", ""))
        return {"status": "ok", "task": t, "summary": f"ingested task.status for {tid} → {t['state']}"}

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
    p.add_argument("--ingest", help="path to a task.status payload JSON from an agent")
    p.add_argument("--history", metavar="TASK_ID")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "orch_status", run, description=__doc__, add_args=add_args).main())
