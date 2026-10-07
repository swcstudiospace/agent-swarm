#!/usr/bin/env python3
"""A01 — decompose a project brief into a typed task DAG in the Task Store.

Patterns encode the reference event flows of 04-integration-plan.md §3:
  feature     brief → REQ → (ARCH ∥ UXD) → DATA → (BE ∥ FE) → (QA ∥ REV ∥ SEC gates) → DEVOPS → REL → (OBS ∥ DOC)
  hotfix      incident → MAINT rca → BE patch → gates → REL → OBS
  dependency  SEC scan → MAINT patch.task → BE bumps → gates → REL
  custom      --plan plan.json  { "tasks": [ {id, capability, agent, title, depends_on[], gates[], risk_class?} ] }

Writes a plan snapshot to .swarm/plans/<correlation_id>.json and emits plan.updated.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, check_task_id  # noqa: E402
from swarm.taskstore import TaskStore, GATES_BY_RISK  # noqa: E402
from swarm.manifest import by_capability  # noqa: E402
from swarm.paths import swarm_dir  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402
from swarm import substrate_client, substrate_tee  # noqa: E402

# (suffix, capability, agent, title, depends_on suffixes, gate spec)
# gate spec: None = derive from risk class; [] = no gates; {"gate": "quality", "for": [...]} = this IS a gate task
PATTERNS: dict[str, list[tuple]] = {
    "feature": [
        ("req", "req.spec", "A02", "Requirements spec + acceptance criteria", [], []),
        ("arch", "design.blueprint", "A03", "Architecture blueprint, API contract, ADRs", ["req"], []),
        ("ux", "ux.spec", "A04", "Design tokens + UX spec + a11y requirements", ["req"], []),
        ("data", "data.migration", "A07", "Data model, migrations, data contract", ["arch"], None),
        ("be", "code.backend", "A05", "Backend implementation against contracts", ["arch", "data"], None),
        ("fe", "code.frontend", "A06", "Frontend implementation against tokens/contracts", ["arch", "ux"], None),
        ("qa", "gate.quality", "A08", "Quality gate: tests + verdict", ["be", "fe", "data"], {"gate": "quality", "for": ["be", "fe", "data"]}),
        ("rev", "gate.review", "A09", "Review gate: code review verdict", ["be", "fe", "data"], {"gate": "review", "for": ["be", "fe", "data"]}),
        ("sec", "gate.security", "A10", "Security gate: SAST/secrets/deps verdict", ["be", "fe", "data"], {"gate": "security", "for": ["be", "fe", "data"]}),
        ("build", "build.artifact", "A11", "CI validation + build artifact record", ["qa", "rev", "sec"], []),
        ("rel", "release.plan", "A12", "Release gate + canary plan", ["build"], {"gate": "release", "for": ["be", "fe", "data"]}),
        ("obs", "obs.slo", "A13", "SLOs, alerts, deploy telemetry guardrails", ["rel"], []),
        ("docs", "docs.bundle", "A15", "Docs bundle, API reference, runbook, changelog", ["rel"], []),
    ],
    "hotfix": [
        ("rca", "maint.rca", "A14", "Root-cause analysis + minimal patch task", [], []),
        ("patch", "code.patch", "A05", "Minimal-diff hotfix", ["rca"], None),
        ("qa", "gate.quality", "A08", "Quality gate (priority)", ["patch"], {"gate": "quality", "for": ["patch"]}),
        ("rev", "gate.review", "A09", "Review gate (priority)", ["patch"], {"gate": "review", "for": ["patch"]}),
        ("sec", "gate.security", "A10", "Security gate (priority)", ["patch"], {"gate": "security", "for": ["patch"]}),
        ("rel", "release.promote", "A12", "Guarded promote + unfreeze", ["qa", "rev", "sec"], {"gate": "release", "for": ["patch"]}),
        ("obs", "obs.incident", "A13", "Confirm stabilization, close incident", ["rel"], []),
        ("retro", "maint.debt", "A14", "Retro → memory + debt register", ["obs"], []),
    ],
    "dependency": [
        ("scan", "sec.deps", "A10", "Dependency/CVE scan", [], []),
        ("patch", "maint.deps", "A14", "Patch tasks (24h deadline for KEV)", ["scan"], []),
        ("bump", "code.patch", "A05", "Dependency bumps", ["patch"], None),
        ("qa", "gate.quality", "A08", "Quality gate", ["bump"], {"gate": "quality", "for": ["bump"]}),
        ("rev", "gate.review", "A09", "Review gate", ["bump"], {"gate": "review", "for": ["bump"]}),
        ("sec", "gate.security", "A10", "Security gate", ["bump"], {"gate": "security", "for": ["bump"]}),
        ("rel", "release.plan", "A12", "Scheduled canary promotion", ["qa", "rev", "sec"], {"gate": "release", "for": ["bump"]}),
        ("obs", "obs.slo", "A13", "Regression watch", ["rel"], []),
    ],
}


def _budget(risk: str) -> dict:
    return {"low": {"max_tokens": 150_000, "max_wall_s": 900, "max_cost_usd": 3},
            "medium": {"max_tokens": 300_000, "max_wall_s": 1800, "max_cost_usd": 8},
            "high": {"max_tokens": 600_000, "max_wall_s": 3600, "max_cost_usd": 20}}[risk]


def load_custom(path: Path) -> list[tuple]:
    data = json.loads(path.read_text())
    rows = []
    for t in data["tasks"]:
        agent = t.get("agent")
        if not agent:
            cands = by_capability(t["capability"])
            if not cands:
                raise SwarmError(ErrorCode.E_INPUT, f"no agent offers capability {t['capability']}")
            agent = cands[0]["id"]
        gates = t.get("gates")  # None → derived; [] → none; {"gate":..,"for":[..]} → gate task
        rows.append((t["id"], t["capability"], agent, t.get("title", t["id"]), t.get("depends_on", []), gates,
                     t.get("risk_class"), t.get("acceptance", [])))
    return rows


def _derived_prefix(corr: str, n: int) -> str:
    return "T" + corr.replace("-", "")[:n]


def _existing(store: TaskStore, prefix: str, rows: list[tuple]) -> list[dict]:
    found = []
    for r in rows:
        try:
            found.append(store.get(f"{prefix}-{r[0]}"))
        except SwarmError:
            continue
    return found


def _reusable(existing, rows, pattern, brief, brief_sha, plan_sha, want_corr, args) -> str | None:
    """Correlation id to reuse when the prefix already holds this exact plan, else None."""
    owners = {t["correlation_id"] for t in existing}
    if len(existing) != len(rows) or len(owners) != 1:
        return None
    corr = owners.pop()
    if want_corr and want_corr != corr:
        return None
    for t, r in zip(existing, rows):  # _existing keeps row order, and every row is present here
        n = t["notes_json"]
        same_brief = n.get("brief_sha256") == brief_sha if "brief_sha256" in n else n.get("brief_excerpt") == brief[:2000]
        # Legacy rows without plan_sha256: pattern rows are fixed by the pattern; custom rows fail closed.
        same_plan = n["plan_sha256"] == plan_sha if "plan_sha256" in n else pattern != "custom"
        if n.get("pattern") != pattern or not same_brief or not same_plan:
            return None
        # WR-13: the per-task inputs this run would create (risk class decides the required gates)
        if (t["risk_class"], t["priority"], t["acceptance"]) != (r[6] or args.risk_class, args.priority, r[7] or args.acceptance):
            return None
    return corr


def _reuse_result(store: TaskStore, ctx, corr: str, pattern: str) -> dict:
    # corr comes from the Task Store here, not argv: re-check it before it names a file (WR-05)
    plan_file = swarm_dir(ctx.root, create=True) / "plans" / f"{check_task_id(corr, 'correlation id')}.json"
    if plan_file.exists():
        tasks = json.loads(plan_file.read_text())["tasks"]
    else:
        tasks = [{k: t[k] for k in ("task_id", "capability", "agent_id", "title", "depends_on", "risk_class", "state", "dag_depth")}
                 | {"gates": store.required_gates(t["task_id"]), "notes": t["notes_json"]}
                 for t in store.list(correlation_id=corr)]
    ctx.correlation_id = corr
    return {"status": "ok", "correlation_id": corr, "pattern": pattern, "reused": True, "tasks": tasks,
            "plan_file": str(plan_file) if plan_file.exists() else None,
            "summary": f"reused existing plan for correlation {corr} ({len(tasks)} tasks, no changes)"}


def _bind_graph(args, ctx, corr: str, brief: str, tasks: list[dict]) -> None:
    """ADR 0001 S1: bind this correlation to one Graph ID and register the node set. Fail-open, off without SUBSTRATE_URL.

    The Graph ID never enters the stored notes or the brief hash, so re-planning the same brief still reuses.
    """
    if not substrate_client.enabled():
        return
    try:
        offered = substrate_tee.resolve_graph_id(corr, explicit=args.graph_id, brief_text=brief)
        graph_id = substrate_tee.bind_graph(corr, offered, root=ctx.root)  # the server's winner, even on conflict
        if graph_id:
            substrate_client.mcp_call("graph_register", {
                "graph_id": graph_id, "repo": substrate_tee.repo_slug(ctx.root), "surface": "swarm-a01-orch", "status": "planned",
                "nodes": [{"node_id": t["task_id"]} for t in tasks]})
    except Exception:  # noqa: BLE001 - substrate never changes the plan's outcome
        pass


def run(args, ctx) -> dict:
    if args.graph_id is not None and not substrate_tee.is_graph_id(args.graph_id):
        raise SwarmError(ErrorCode.E_INPUT, f"invalid --graph-id {args.graph_id!r}: expected ut-<base36>-<8 hex>")
    brief = ""
    if args.brief and Path(args.brief).exists():
        brief = Path(args.brief).read_text(encoding="utf-8")
    elif args.brief_text:
        brief = args.brief_text
    if not brief and not args.plan:
        if not ctx.dry_run:
            raise SwarmError(ErrorCode.E_INPUT, "provide --brief FILE, --brief-text, or --plan plan.json")
        brief = "(dry-run placeholder brief)"

    corr = ctx.correlation_id or str(uuid.uuid4())
    if args.plan:
        args.pattern = "custom"
    rows = load_custom(Path(args.plan)) if args.plan else [r + (None, []) for r in PATTERNS[args.pattern]]
    brief_sha = hashlib.sha256(brief.encode("utf-8")).hexdigest()
    plan_sha = hashlib.sha256(json.dumps([list(r) for r in rows], sort_keys=True).encode("utf-8")).hexdigest()
    if ctx.dry_run:
        prefix = args.prefix or _derived_prefix(corr, 4)
        return {"status": "ok", "correlation_id": corr, "pattern": args.pattern, "dry_run": True,
                "tasks": [{"task_id": f"{prefix}-{r[0]}", "capability": r[1], "agent": r[2], "depends_on": r[4]} for r in rows],
                "summary": f"dry-run: would create {len(rows)} tasks"}

    store = TaskStore(root=ctx.root)
    created = []
    depth = {}
    prefix = args.prefix
    try:
        with store.transaction():
            if prefix is None:
                prefix = _derived_prefix(corr, 4)
                # D-05/D-06: the 4-hex prefix is this correlation's own plan when it already holds those
                # tasks, so reuse or reject it below; fall back to 6 hex only for another correlation's tasks.
                if (ex4 := _existing(store, prefix, rows)) and corr not in {t["correlation_id"] for t in ex4}:
                    prefix = _derived_prefix(corr, 6)
            existing = _existing(store, prefix, rows)
            if existing:
                reused = _reusable(existing, rows, args.pattern, brief, brief_sha, plan_sha, ctx.correlation_id, args)
                if reused is None:
                    owners = sorted({t["correlation_id"] for t in existing})
                    hint = "pass a new --prefix" if args.prefix else "pass a new --correlation-id or --prefix for this brief"
                    raise SwarmError(ErrorCode.E_CONTRACT,
                                     f"prefix {prefix} already used by correlation {','.join(owners)} with a different "
                                     f"brief/pattern/plan/risk-class/priority/acceptance; {hint}")
            else:
                reused = None
                for suffix, cap, agent, title, deps, gates, risk_override, acceptance in rows:
                    tid = f"{prefix}-{suffix}"
                    risk = risk_override or args.risk_class
                    depth[suffix] = 1 + max((depth[d] for d in deps), default=-1)
                    notes = {"pattern": args.pattern, "brief_excerpt": brief[:2000], "brief_sha256": brief_sha,
                             "plan_sha256": plan_sha}
                    if isinstance(gates, dict):
                        notes["gate"] = gates["gate"]
                        notes["gate_for"] = [f"{prefix}-{s}" for s in gates["for"]]
                        notes["gates"] = []
                    elif gates is not None:
                        notes["gates"] = gates
                    store.create(task_id=tid, correlation_id=corr, capability=cap, title=title, agent_id=agent,
                                 dag_depth=depth[suffix], depends_on=[f"{prefix}-{d}" for d in deps],
                                 acceptance=acceptance or args.acceptance, budget=_budget(risk), risk_class=risk,
                                 priority=args.priority, notes=notes)
                    store.transition(tid, "VALIDATED", reason="brief validated by A01")
                    store.transition(tid, "PLANNED", reason=f"pattern={args.pattern}")
                    created.append(store.get(tid))
    except sqlite3.IntegrityError as e:
        raise SwarmError(ErrorCode.E_CONTRACT, f"prefix {prefix} already in use; pass a new --prefix") from e

    if reused is not None:
        return _reuse_result(store, ctx, reused, args.pattern)

    plan = {"correlation_id": corr, "pattern": args.pattern, "risk_class": args.risk_class, "brief": brief,
            "tasks": [{k: t[k] for k in ("task_id", "capability", "agent_id", "title", "depends_on", "risk_class", "state", "dag_depth")}
                      | {"gates": store.required_gates(t["task_id"]), "notes": t["notes_json"]} for t in created]}
    sdir = swarm_dir(ctx.root, create=True)
    plans = sdir / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / f"{corr}.json").write_text(json.dumps(plan, indent=2))
    (sdir / "latest_correlation").write_text(corr)
    ctx.correlation_id = corr
    _bind_graph(args, ctx, corr, brief, plan["tasks"])  # before plan.updated, so that event and the exit record are tee'd
    ctx.emit("plan.updated", {"pattern": args.pattern, "task_count": len(created)})
    lines = [f"{t['task_id']:<14} {t['agent_id']:<4} d={t['dag_depth']} ← {','.join(t['depends_on']) or '-'}" for t in created]
    return {"status": "ok", "correlation_id": corr, "pattern": args.pattern, "tasks": plan["tasks"],
            "plan_file": str(plans / f"{corr}.json"),
            "summary": f"created {len(created)} tasks for correlation {corr}\n" + "\n".join(lines)}


def add_args(p):
    p.add_argument("--repo", dest="root", default=argparse.SUPPRESS,
                   help="alias of --root (same flag as swarm_run.py --repo)")
    p.add_argument("--brief", help="path to brief markdown")
    p.add_argument("--brief-text", help="inline brief text")
    p.add_argument("--pattern", choices=list(PATTERNS) + ["custom"], default="feature")
    p.add_argument("--plan", help="custom DAG json (implies --pattern custom)")
    p.add_argument("--risk-class", choices=list(GATES_BY_RISK), default="medium")
    p.add_argument("--priority", choices=["P0", "P1", "P2", "P3"], default="P2")
    p.add_argument("--prefix", default=None, help="task id prefix (default: T + first 4 hex of the correlation id)")
    p.add_argument("--acceptance", action="append", default=[], help="acceptance criterion (repeatable)")
    p.add_argument("--graph-id", default=None, help="substrate Graph ID to bind this correlation to (ut-<base36>-<8 hex>); "
                                                    "default: env SUBSTRATE_GRAPH_ID, a Graph ID in the brief, else minted")


if __name__ == "__main__":
    sys.exit(AgentScript("A01", "orch_plan", run, description=__doc__, add_args=add_args).main())
