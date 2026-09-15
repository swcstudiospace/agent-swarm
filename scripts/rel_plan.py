#!/usr/bin/env python3
"""A12 — release gate + progressive-delivery plan.

For --task-id / --task-ids / --correlation-id, reads each task's latest gate verdicts and required
gates from the Task Store, applies swarm.gates.conjunction (most-restrictive wins, expired verdicts
count as missing), honours a swarm-wide freeze (.swarm/release.freeze, set with --freeze "reason",
lifted with --unfreeze), and emits a signed `gate.verdict` with gate="release" that passes only when
every other required gate passes and no freeze is active. Also writes a canary release.plan
(5→25→50→100 %) with guardrails and rollback triggers to .swarm/releases/<release_id>.plan.json.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript  # noqa: E402
from swarm.gates import make_verdict, make_finding, conjunction  # noqa: E402
from swarm.taskstore import TaskStore, GATES_BY_RISK  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402

RISK_ORDER = ["low", "medium", "high"]
GUARDRAILS = {"low": {"error_rate_max": 0.01, "p95_ms_max": 500, "slo_burn_max": 6.0, "soak_min": 5},
              "medium": {"error_rate_max": 0.005, "p95_ms_max": 320, "slo_burn_max": 2.0, "soak_min": 15},
              "high": {"error_rate_max": 0.002, "p95_ms_max": 250, "slo_burn_max": 1.0, "soak_min": 30}}


def freeze_file() -> Path:
    return SWARM_DIR / "release.freeze"


def read_freeze() -> dict | None:
    f = freeze_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {"reason": "unreadable freeze file", "frozen_at": None, "by": "unknown"}


def build_plan(release_id: str, risk: str, tasks: list[str], gates: list[str], artifacts: list[str]) -> dict:
    g = GUARDRAILS[risk]
    return {"kind": "release.plan", "release_id": release_id, "strategy": "canary", "steps_pct": [5, 25, 50, 100],
            "guardrails": g, "gates_required": gates, "risk_class": risk, "auto_rollback": True,
            "approval": "L4:human-four-eyes" if risk == "high" else "L2:auto",
            "rollback_triggers": [f"error_rate > {g['error_rate_max']} over 5m at any step",
                                  f"p95_latency_ms > {g['p95_ms_max']} over 5m at any step",
                                  f"slo_burn_rate > {g['slo_burn_max']}x (1h window)",
                                  "deploy.telemetry verdict == rollback-suggested", "incident.alert sev<=2 naming this release",
                                  "monitoring.degraded broadcast (fail-closed: halt at current step)"],
            "tasks": tasks, "artifacts": artifacts, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def run(args, ctx) -> dict:
    SWARM_DIR.mkdir(parents=True, exist_ok=True)
    if args.freeze and not ctx.dry_run:
        rec = {"reason": args.freeze, "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "by": "A12@local", "correlation_id": ctx.correlation_id}
        freeze_file().write_text(json.dumps(rec, indent=2))
    if args.unfreeze and not ctx.dry_run and freeze_file().exists():
        freeze_file().unlink()
    frozen = read_freeze() if not ctx.dry_run else None

    if ctx.dry_run:
        plan = build_plan("REL-dry", "medium", ["T-dry"], ["review", "quality"], [])
        env = make_verdict(gate="release", task_id="T-dry", agent_id="A12@dry", findings=[],
                           runs={"review": "pass", "quality": "pass", "freeze": "none"}, correlation_id=ctx.correlation_id)
        return {"status": "ok", "verdict": "pass", "frozen": False, "plan": plan, "findings": [], "envelope": env,
                "dry_run": True, "summary": "dry-run: canned release gate PASS with canary plan"}

    ids = [t for t in (args.task_ids or "").split(",") if t]
    if ctx.task_id:
        ids.insert(0, ctx.task_id)
    store = TaskStore()
    if ctx.correlation_id and not ids:
        ids = [t["task_id"] for t in store.list(correlation_id=ctx.correlation_id)]
    findings, per_task, artifacts, risk, now = [], {}, [], "low", time.time()
    if not ids:
        findings.append(make_finding("RF-000", "major", "input", "no task identified (pass --task-id/--task-ids/--correlation-id)",
                                     owner_suggestion="A01"))
    for tid in ids:
        try:
            task = store.get(tid)
        except SwarmError as e:
            if e.code is not ErrorCode.E_INPUT:
                raise
            per_task[tid] = {"verdicts": {}, "required": [], "overall": "fail", "problems": ["task:unknown"]}
            findings.append(make_finding(f"RF-{len(findings)+1:03d}", "major", "input", f"unknown task {tid} in Task Store",
                                         owner_suggestion="A01"))
            continue
        if RISK_ORDER.index(task["risk_class"]) > RISK_ORDER.index(risk):
            risk = task["risk_class"]
        required = [g for g in store.required_gates(tid) if g != "release"]
        latest = store.latest_verdicts(tid)
        verdicts = {g: ("fail" if v["expires_at"] < now else v["verdict"]) for g, v in latest.items() if g != "release"}
        overall, problems = conjunction(verdicts, required)
        expired = [g for g, v in latest.items() if v["expires_at"] < now and g != "release"]
        per_task[tid] = {"state": task["state"], "risk_class": task["risk_class"], "required": required,
                         "verdicts": verdicts, "overall": overall, "problems": problems, "expired": expired}
        artifacts += [o.get("uri") for o in task["outputs"] if o.get("kind") == "build.artifact"]
        for pr in problems:
            gate, why = pr.split(":", 1)
            sev = "major"
            findings.append(make_finding(f"RF-{len(findings)+1:03d}", sev, "gate", f"{tid}: {gate} gate {why}"
                                         + (" (expired)" if gate in expired else ""), owner_suggestion={"review": "A09", "quality": "A08", "security": "A10"}.get(gate, "A01")))
        if not artifacts and task["risk_class"] != "low":
            findings.append(make_finding(f"RF-{len(findings)+1:03d}", "minor", "provenance",
                                         f"{tid}: no build.artifact registered — A11 provenance required before promote",
                                         owner_suggestion="A11"))
    if frozen:
        findings.append(make_finding(f"RF-{len(findings)+1:03d}", "blocker", "freeze",
                                     f"deploy freeze active: {frozen.get('reason')} (since {frozen.get('frozen_at')})",
                                     evidence=str(freeze_file()), owner_suggestion="A13"))

    primary = ids[0] if ids else "T-unassigned"
    release_id = args.release_id or f"REL-{primary}"
    gates = sorted({g for t in per_task.values() for g in t["required"]}) or [g for g in GATES_BY_RISK[risk] if g != "release"]
    plan = build_plan(release_id, risk, ids, gates, [a for a in artifacts if a])
    runs = {g: ("pass" if all(t["verdicts"].get(g) in ("pass", "waive") for t in per_task.values()) else "fail") for g in gates}
    runs["freeze"] = "active" if frozen else "none"
    env = make_verdict(gate="release", task_id=primary, agent_id="A12@local", findings=findings, runs=runs,
                       correlation_id=ctx.correlation_id, extra={"release_id": release_id, "frozen": bool(frozen)})
    verdict = env["payload"]["verdict"]
    vdir, rdir = SWARM_DIR / "verdicts", SWARM_DIR / "releases"
    vdir.mkdir(parents=True, exist_ok=True)
    rdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{primary}.release.json").write_text(json.dumps(env, indent=2))
    plan["gate_verdict"] = verdict
    (rdir / f"{release_id}.plan.json").write_text(json.dumps(plan, indent=2))
    for tid in ids:
        try:
            store.record_verdict(tid, "release", verdict, "A12", findings)
        except SwarmError as e:
            if e.code is not ErrorCode.E_INPUT:
                raise
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict, "frozen": bool(frozen), "freeze": frozen,
            "release_id": release_id, "tasks": per_task, "plan": plan, "findings": findings, "envelope": env,
            "summary": f"release gate {verdict.upper()} for {release_id} ({len(ids)} task(s), risk={risk}"
                       f"{', FROZEN' if frozen else ''}) — canary {plan['steps_pct']}"}


def add_args(p):
    p.add_argument("--task-ids", help="comma-separated additional task ids to gate together")
    p.add_argument("--release-id", help="release identifier (default REL-<task_id>)")
    p.add_argument("--freeze", metavar="REASON", help="write .swarm/release.freeze with this reason before evaluating")
    p.add_argument("--unfreeze", action="store_true", help="remove an active freeze (declaring authority only)")


if __name__ == "__main__":
    sys.exit(AgentScript("A12", "rel_plan", run, description=__doc__, add_args=add_args).main())
