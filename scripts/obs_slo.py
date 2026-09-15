#!/usr/bin/env python3
"""A13 — SLO tooling: define SLO manifests and evaluate burn rate with multi-window alerting.

--define writes an `slo.manifest` (service, sli availability|latency, objective, window) to
.swarm/slo/<service>.json. --evaluate computes the SLI from --good/--total counts or an --events
JSONL file ({ts, ok, latency_ms}), the error-budget remaining, burn rates for 1h/6h/3d windows and
compares them to the standard multi-window thresholds (14.4x page, 6x page, 1x ticket). When a
threshold is exceeded it emits an `incident.alert` payload with severity. With neither flag it lists
the manifests present. Stdlib only
missing manifests degrade to sensible defaults, never crash.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript  # noqa: E402
from swarm.gates import make_finding  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402

WINDOWS = {"1h": 3600, "6h": 6 * 3600, "3d": 3 * 86400}
# (window, burn threshold, severity, action) — Google SRE multi-window multi-burn-rate policy
THRESHOLDS = [("1h", 14.4, 1, "page"), ("6h", 6.0, 2, "page"), ("3d", 1.0, 3, "ticket")]
WINDOW_S = {"7d": 7 * 86400, "28d": 28 * 86400, "30d": 30 * 86400}


def slo_dir() -> Path:
    d = SWARM_DIR / "slo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_manifest(service: str) -> dict | None:
    f = slo_dir() / f"{service}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def define(args) -> dict:
    if not 0 < args.objective < 1:
        raise SwarmError(ErrorCode.E_INPUT, "--objective must be a fraction in (0,1), e.g. 0.999")
    m = {"kind": "slo.manifest", "version": "1.0.0", "service": args.service, "sli": args.sli, "objective": args.objective,
         "window": args.window, "latency_threshold_ms": args.latency_ms if args.sli == "latency" else None,
         "alerting": [{"window": w, "burn_rate": b, "sev": s, "action": a} for w, b, s, a in THRESHOLDS],
         "runbook": args.runbook, "owner": "A13", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = slo_dir() / f"{args.service}.json"
    path.write_text(json.dumps(m, indent=2))
    return {"status": "ok", "manifest": m, "path": str(path),
            "summary": f"slo.manifest written for {args.service}: {args.sli} objective {args.objective:.4%} over {args.window}"}


def read_events(path: Path, latency_ms: float | None) -> list[tuple[float, bool]]:
    if not path.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"events file not found: {path}")
    out = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ok = bool(ev.get("ok", True))
        if latency_ms is not None and ev.get("latency_ms") is not None:
            ok = ok and float(ev["latency_ms"]) <= latency_ms
        ts = ev.get("ts")
        try:
            ts = float(ts) if not isinstance(ts, str) else time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        except (TypeError, ValueError):
            ts = time.time()
        out.append((ts, ok))
    return out


def burn(good: int, total: int, objective: float) -> tuple[float | None, float | None]:
    """returns (sli, burn_rate). burn = observed error ratio / allowed error ratio."""
    if total <= 0:
        return None, None
    sli = good / total
    return sli, (1 - sli) / (1 - objective)


def evaluate(args, ctx) -> dict:
    m = load_manifest(args.service) or {"service": args.service, "sli": args.sli, "objective": args.objective,
                                          "window": args.window, "latency_threshold_ms": args.latency_ms, "runbook": args.runbook}
    objective, lat = float(m["objective"]), (m.get("latency_threshold_ms") if m.get("sli") == "latency" else None)
    per_window, source = {}, "counts"
    if args.events:
        source, evs = "events", read_events(Path(args.events), lat)
        now = args.now or (max(t for t, _ in evs) if evs else time.time())
        for name, secs in WINDOWS.items():
            sel = [ok for t, ok in evs if now - secs <= t <= now]
            sli, br = burn(sum(sel), len(sel), objective)
            per_window[name] = {"good": sum(sel), "total": len(sel), "sli": sli, "burn_rate": br}
        good, total = sum(ok for _, ok in evs), len(evs)
    else:
        if args.total is None:
            raise SwarmError(ErrorCode.E_INPUT, "--evaluate needs --good/--total or --events FILE")
        good, total = int(args.good or 0), int(args.total)
        sli, br = burn(good, total, objective)
        per_window = {w: {"good": good, "total": total, "sli": sli, "burn_rate": br} for w in WINDOWS}
    sli, br_all = burn(good, total, objective)
    budget_remaining = None if br_all is None else round(1 - br_all, 4)
    breaches = [(w, thr, sev, act, per_window[w]["burn_rate"]) for w, thr, sev, act in THRESHOLDS
                if per_window[w]["burn_rate"] is not None and per_window[w]["burn_rate"] >= thr]
    findings, alert = [], None
    if breaches:
        w, thr, sev, act, rate = min(breaches, key=lambda b: b[2])
        alert = {"kind": "incident.alert", "incident_id": f"INC-{int(time.time()) % 100000}", "sev": sev, "action": act,
                 "slo": f"{m['service']}.{m.get('sli', 'availability')}",
                 "evidence": {"burn_rate": f"{rate:.1f}x", "threshold": f"{thr}x", "window": w, "sli": sli,
                              "objective": objective, "error_budget_remaining": budget_remaining, "source": source},
                 "suspect_changes": [s for s in (args.suspect or "").split(",") if s],
                 "playbooks_available": ["rollback:latest-release", "flag-off:latest-feature"], "runbook": m.get("runbook"),
                 "declared_by": "A13@local", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "correlation_id": ctx.correlation_id}
        findings.append(make_finding("OF-001", "critical" if sev <= 2 else "minor", "slo-burn",
                                     f"{alert['slo']} burning {rate:.1f}x (>= {thr}x over {w}) -> sev{sev} {act}",
                                     evidence=json.dumps(alert["evidence"]), owner_suggestion="A12" if sev <= 2 else "A14"))
        if not ctx.dry_run:
            inc = SWARM_DIR / "incidents"
            inc.mkdir(parents=True, exist_ok=True)
            (inc / f"{alert['incident_id']}.json").write_text(json.dumps(alert, indent=2))
    return {"status": "fail" if alert and alert["sev"] <= 2 else "ok", "service": m["service"], "objective": objective,
            "sli": sli, "good": good, "total": total, "error_budget_remaining": budget_remaining, "windows": per_window,
            "thresholds": {w: thr for w, thr, _, _ in THRESHOLDS}, "alert": alert, "findings": findings,
            "manifest_found": load_manifest(args.service) is not None,
            "summary": f"{m['service']}: SLI {sli:.4%} vs {objective:.4%}, burn {br_all:.2f}x, budget {budget_remaining:.1%} left"
                       + (f" — ALERT sev{alert['sev']} ({alert['action']})" if alert else " — within budget") if sli is not None
                       else f"{m['service']}: no data (total=0)"}


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {"status": "ok", "dry_run": True, "service": args.service, "objective": 0.999, "sli": 0.9995,
                "error_budget_remaining": 0.5, "windows": {w: {"burn_rate": 0.5} for w in WINDOWS}, "alert": None,
                "findings": [], "summary": "dry-run: SLI 99.95% vs 99.90%, burn 0.50x, 50% budget left — within budget"}
    if args.define:
        return define(args)
    if args.evaluate:
        return evaluate(args, ctx)
    manifests = sorted(p.stem for p in slo_dir().glob("*.json"))
    return {"status": "ok", "manifests": manifests, "dir": str(slo_dir()),
            "summary": f"{len(manifests)} slo.manifest(s) in {slo_dir()}: {manifests or 'none (use --define)'}"}


def add_args(p):
    p.add_argument("--service", default="default-service")
    p.add_argument("--define", action="store_true", help="write .swarm/slo/<service>.json")
    p.add_argument("--evaluate", action="store_true", help="compute SLI / burn rate and alert")
    p.add_argument("--sli", choices=["availability", "latency"], default="availability")
    p.add_argument("--objective", type=float, default=0.999)
    p.add_argument("--window", choices=list(WINDOW_S), default="28d")
    p.add_argument("--latency-ms", type=float, default=300.0, help="latency SLI threshold")
    p.add_argument("--runbook", default="runbooks/<service>.md")
    p.add_argument("--good", type=int)
    p.add_argument("--total", type=int)
    p.add_argument("--events", help="JSONL file of {ts, ok, latency_ms}")
    p.add_argument("--now", type=float, help="epoch seconds to anchor windows (default: latest event)")
    p.add_argument("--suspect", help="comma list of suspect release ids for the alert")


if __name__ == "__main__":
    sys.exit(AgentScript("A13", "obs_slo", run, description=__doc__, add_args=add_args).main())
