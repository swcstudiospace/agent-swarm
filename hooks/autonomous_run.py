#!/usr/bin/env python3
"""Detached AgentSwarm runner used after the all-in-one Prompt Uplift hook.

Fail-open. Dedupes concurrent kicks with a lock file. Always writes a log under
$SWARM_DIR/autonomous.log.

The run step is capped (SWARM_AUTONOMOUS_RUN_CAP_S, default 3600). On expiry the runner is
SIGTERMed so it can end its sessions, and SIGKILLed only after SWARM_AUTONOMOUS_RUN_GRACE_S
(default 15). subprocess.run's timeout SIGKILLs immediately, which orphans those sessions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Auto-run for every plugin-handled prompt. Slash/ctl strings stay negative so
# Skill dispatch and /all-in-one:* are not consumed by the swarm. Empty and
# leading-/ briefs are skipped. "what is" is not a skip: trivia still kicks.
NEGATIVE = ("/uplift", "/think", "explain only", "/all-in-one:")


def classify(prompt: str) -> bool:
    """Thin delegate to swarm.signal_detector for parity (n8 plan). Fail-open."""
    try:
        from swarm.signal_detector import classify as _sd_classify
        return _sd_classify(prompt) == "sdlc"
    except Exception:
        # original fallback
        low = prompt.lower()
        if any(n in low for n in NEGATIVE):
            return False
        trimmed = prompt.strip()
        if not trimmed:
            return False
        if trimmed.startswith("/"):
            return False
        return True


def pattern_for(brief: str) -> str:
    low = brief.lower()
    if "hotfix" in low or "cve" in low or "incident" in low:
        return "hotfix"
    if "dependenc" in low or "bump" in low:
        return "dependency"
    return "feature"


def _env_seconds(name: str, default: float) -> float:
    """A positive number of seconds from the environment, else `default`. Blank, non-numeric and non-positive
    values are the default: a typo must not disable the cap or the grace."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _signal_pid(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def run_capped(cmd: list[str], *, cwd: str, env: dict, timeout: float, grace: float) -> subprocess.CompletedProcess:
    """Run `cmd`, capturing stdout and stderr. `subprocess.run(timeout=)` SIGKILLs on expiry, which orphans the
    runner's sessions: they sit in their own OS session, so the kill never reaches them and their tasks stay
    IN_PROGRESS (T-06-26). Ask the runner to stop with SIGTERM first — its handler ends those sessions — and
    SIGKILL only what ignores that for `grace` seconds. `Popen.communicate` does not kill on timeout; `run` does,
    which is why this does not call `run`."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _signal_pid(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=grace)
        except subprocess.TimeoutExpired:
            _signal_pid(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
    return subprocess.CompletedProcess(cmd, proc.returncode if proc.returncode is not None else 1, out or "", err or "")


def acquire(lock: Path) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if lock.exists() and now - lock.stat().st_mtime < 120:
        return False
    lock.write_text(str(now))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cwd", default=".", help="target application repo")
    ap.add_argument("--brief", required=True, help="the user's original prompt (classification + dedupe key)")
    ap.add_argument("--spec", default="", help="uplifted XML spec file; when readable, A01 plans from it instead of --brief")
    ap.add_argument("--runtime", default="auto", choices=["auto", "claude", "grok", "omp"])
    ap.add_argument("--swarm-root", default=os.environ.get("SWARM_ROOT", str(Path(__file__).resolve().parent.parent)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", action="store_true", help="run in-process (tests); default is already in-process for this script")
    args = ap.parse_args()
    if not classify(args.brief):
        return 0
    root = Path(args.swarm_root).resolve()
    repo = Path(args.cwd).resolve()
    sys.path.insert(0, str(root))
    from swarm.paths import swarm_dir as resolve_swarm_dir
    swarm_dir = resolve_swarm_dir(repo, create=True)  # absolute; exported to both children
    digest = hashlib.sha256(f"{repo}|{args.brief}".encode()).hexdigest()[:16]
    lock = swarm_dir / "kickoffs" / f"{digest}.lock"
    log = swarm_dir / "autonomous.log"
    if not acquire(lock):
        log.write_text((log.read_text() if log.exists() else "") + f"skip duplicate {digest}\n")
        return 0
    env = {**os.environ, "SWARM_DIR": str(swarm_dir), "SWARM_CHILD": "1", "AIO_UPLIFT": "0", "AIO_SWARM": "0"}
    spec = Path(args.spec) if args.spec and Path(args.spec).is_file() else None
    plan = [sys.executable, str(root / "scripts" / "orch_plan.py"), "--pattern", pattern_for(args.brief), "--json"]
    plan += ["--brief", str(spec)] if spec else ["--brief-text", args.brief]
    run = [
        sys.executable, str(root / "scripts" / "swarm_run.py"),
        "--repo", str(repo), "--runtime", args.runtime, "--json",
    ]
    if args.dry_run:
        run.append("--dry-run")
    try:
        p1 = subprocess.run(plan, cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
        try:
            plan_json = json.loads(p1.stdout)
        except ValueError:
            plan_json = {}
        if p1.returncode != 0 or plan_json.get("status") != "ok" or not plan_json.get("correlation_id"):
            err = plan_json.get("error") if isinstance(plan_json.get("error"), dict) else {}
            log.write_text(json.dumps({
                "brief": args.brief[:500],
                "repo": str(repo),
                "spec": str(spec) if spec else None,
                "plan_rc": p1.returncode,
                "plan_out": p1.stdout[-4000:],
                "plan_err": p1.stderr[-2000:],
                "skipped_run": True,
                "error": {"code": err.get("code"), "message": err.get("message")},
            }, indent=2))
            return 0
        run += ["--correlation-id", plan_json["correlation_id"]]
        # SWARM_AUTONOMOUS_RUN_CAP_S bounds the run (default 3600). The grace is how long the runner has to end
        # its sessions after SIGTERM before this process SIGKILLs it (T-06-26).
        p2 = run_capped(
            run, cwd=str(root), env=env,
            timeout=_env_seconds("SWARM_AUTONOMOUS_RUN_CAP_S", 3600),
            grace=_env_seconds("SWARM_AUTONOMOUS_RUN_GRACE_S", 15),
        )
        log.write_text(
            json.dumps({
                "brief": args.brief[:500],
                "repo": str(repo),
                "spec": str(spec) if spec else None,
                "plan_rc": p1.returncode,
                "run_rc": p2.returncode,
                "plan_out": p1.stdout[-4000:],
                "run_out": p2.stdout[-8000:],
                "run_err": p2.stderr[-2000:],
            }, indent=2)
        )
        return 0 if p2.returncode in (0, 1) else p2.returncode
    except Exception as exc:
        log.write_text(json.dumps({"error": str(exc)}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
