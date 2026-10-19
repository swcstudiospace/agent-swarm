#!/usr/bin/env python3
"""A01 — live view of every swarm run: state, elapsed time, last activity (the data behind the omp third bar).

  python3 scripts/swarm_live.py                       # compact table for the latest correlation
  python3 scripts/swarm_live.py --json                # the swarm.live.v1 document
  python3 scripts/swarm_live.py --watch 2             # redraw every 2 s until interrupted
  python3 scripts/swarm_live.py --correlation-id <id> --root /path/to/repo

Reads `<swarm_dir>/live.json` when a scheduler wrote it within three heartbeats, otherwise derives the same view from the
Task Store and the run log (so an in-session run shows up too). Read-only; --dry-run prints a canned sample.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.clock import SYSTEM_CLOCK, format_elapsed  # noqa: E402
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm import telemetry  # noqa: E402

_ACTIVE = ("running", "handing_off", "starting", "queued", "waiting")
_MARK = {"running": "●", "handing_off": "⇄", "starting": "○", "queued": "○", "waiting": "◌", "completed": "✓",
         "failed": "✕", "cancelled": "⊘", "timed_out": "⏱"}


def _sample() -> dict:
    """Deterministic canned document for --dry-run (no files read or written)."""
    def row(rid, agent, state, el, since, act, calls, stalled=False):
        return {"run_id": rid, "task_id": rid.rsplit(".a", 1)[0], "agent": agent, "state": state, "attempt": 1,
                "elapsed_s": el, "state_elapsed_s": el, "since_activity_s": since, "stalled": stalled, "activity": act,
                "tool_calls": calls, "last_tool": "edit" if act else None, "parent": None, "handover_in": None,
                "handover_out": None, "error": None}
    rows = [row("T0000-be.a1", "a05-backend", "running", 72.0, 3.2, "edit swarm/x.py", 12),
            row("T0000-qa.a1", "a08-qa", "queued", 5.0, None, None, 0),
            row("T0000-req.a1", "a02-requirements", "completed", 40.0, None, None, 3)]
    return telemetry.build_snapshot(rows, correlation_id="dry-run", seq=1, updated_wall=1_760_000_000.0, elapsed_s=72.0,
                                    max_parallel=4, effective_parallel=4, degraded=None)


def _current(root: Path, correlation_id: str | None) -> tuple[dict, str]:
    """(snapshot, source): a fresh live.json (elapsed extrapolated by its age) or one derived from the Task Store."""
    now = SYSTEM_CLOCK.wall()
    doc = telemetry.read_live(root)
    if doc is not None and (correlation_id is None or doc.get("correlation_id") == correlation_id):
        age = max(0.0, now - float(doc["updated_wall"]))
        if age <= 3 * telemetry.heartbeat_default():
            for r in doc["runs"]:
                if r["state"] in _ACTIVE:  # consumer-side extrapolation; wall-based because the producer is another process
                    r["elapsed_s"] += age
                    r["state_elapsed_s"] += age
                    if r.get("since_activity_s") is not None:
                        r["since_activity_s"] += age
            doc["swarm"]["elapsed_s"] += age if doc["swarm"]["state"] == "running" else 0.0
            return doc, "live.json"
    return telemetry.derive_snapshot(root, clock=SYSTEM_CLOCK, correlation_id=correlation_id), "task-store"


def _table(doc: dict) -> str:
    s = doc["swarm"]
    head = (f"swarm {s['state']} {format_elapsed(s['elapsed_s'])} | {s['running']} run | {s['queued']} queued | "
            f"{s['waiting']} wait | {s['completed']} done | {s['failed']} fail | {s['timed_out']} timeout | "
            f"{s['stalled']} stalled | slots {s['running']}/{s['effective_parallel']}")
    lines = [head, f"{'':2}{'AGENT':<20}{'STATE':<12}{'ELAPSED':<9}{'IDLE':<8}ACTIVITY"]
    for r in doc["runs"]:
        state = "STALL" if r["stalled"] else r["state"]
        idle = format_elapsed(r["since_activity_s"]) if r.get("since_activity_s") is not None else "-"
        lines.append(f"{_MARK.get(r['state'], '?'):<2}{r['agent'][:19]:<20}{state:<12}"
                     f"{format_elapsed(r['elapsed_s']):<9}{idle:<8}{r.get('activity') or r.get('error') or ''}")
    if not doc["runs"]:
        lines.append("(no runs)")
    if s.get("degraded"):
        lines.append(f"degraded: {s['degraded'].get('reason')}")
    return "\n".join(lines)


def add_args(p):
    p.add_argument("--repo", dest="root", default=argparse.SUPPRESS, help="alias of --root")
    p.add_argument("--watch", type=float, metavar="SECONDS", help="redraw every SECONDS until interrupted")
    p.add_argument("--ticks", type=int, metavar="N", help="with --watch, stop after N frames")


def run(args, ctx) -> dict:
    if args.watch is not None and not args.watch > 0:
        raise SwarmError(ErrorCode.E_INPUT, f"--watch must be a number of seconds > 0, got {args.watch!r}")
    if args.ticks is not None and args.ticks < 1:
        raise SwarmError(ErrorCode.E_INPUT, f"--ticks must be >= 1, got {args.ticks!r}")
    if ctx.dry_run:
        doc, source = _sample(), "dry-run"
    else:
        doc, source = _current(ctx.root, ctx.correlation_id)
    if args.watch is not None and not ctx.dry_run:
        frames = 0
        try:
            while True:
                doc, source = _current(ctx.root, ctx.correlation_id)
                print(json.dumps(doc, separators=(",", ":")) if args.json else "\x1b[2J\x1b[H" + _table(doc), flush=True)
                frames += 1
                if args.ticks is not None and frames >= args.ticks:
                    break
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass
        return {"status": "ok", "source": source, "frames": frames, "summary": f"watched {frames} frame(s)"}
    return {"status": "ok", "source": source, "live": doc, "summary": _table(doc)}


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "swarm_live", run, description=__doc__, add_args=add_args).main())
