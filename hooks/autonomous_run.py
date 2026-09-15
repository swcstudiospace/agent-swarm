#!/usr/bin/env python3
"""Detached AgentSwarm runner used after the all-in-one Prompt Uplift hook.

Fail-open. Dedupes concurrent kicks with a lock file. Always writes a log under
$SWARM_DIR/autonomous.log.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

POSITIVE = (
    "implement", "feature", "bug", "fix", "refactor", "release", "deploy", "hotfix",
    "requirements", "architecture", "code review", "security audit", "run the swarm",
    "agent-swarm", "a01-orchestrator", "build a", "build me",
)
NEGATIVE = ("/uplift", "/think", "what is", "explain only", "/all-in-one:")


def classify(prompt: str) -> bool:
    low = prompt.lower()
    if any(n in low for n in NEGATIVE):
        return False
    return any(p in low for p in POSITIVE)


def pattern_for(brief: str) -> str:
    low = brief.lower()
    if "hotfix" in low or "cve" in low or "incident" in low:
        return "hotfix"
    if "dependenc" in low or "bump" in low:
        return "dependency"
    return "feature"


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
    ap.add_argument("--brief", required=True)
    ap.add_argument("--runtime", default="auto", choices=["auto", "claude", "grok"])
    ap.add_argument("--swarm-root", default=os.environ.get("SWARM_ROOT", str(Path(__file__).resolve().parent.parent)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", action="store_true", help="run in-process (tests); default is already in-process for this script")
    args = ap.parse_args()
    if not classify(args.brief):
        return 0
    root = Path(args.swarm_root).resolve()
    repo = Path(args.cwd).resolve()
    swarm_dir = Path(os.environ.get("SWARM_DIR", str(root / ".swarm")))
    swarm_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{repo}|{args.brief}".encode()).hexdigest()[:16]
    lock = swarm_dir / "kickoffs" / f"{digest}.lock"
    log = swarm_dir / "autonomous.log"
    if not acquire(lock):
        log.write_text((log.read_text() if log.exists() else "") + f"skip duplicate {digest}\n")
        return 0
    env = {**os.environ, "SWARM_DIR": str(swarm_dir)}
    plan = [
        sys.executable, str(root / "scripts" / "orch_plan.py"),
        "--brief-text", args.brief, "--pattern", pattern_for(args.brief), "--json",
    ]
    run = [
        sys.executable, str(root / "scripts" / "swarm_run.py"),
        "--repo", str(repo), "--runtime", args.runtime, "--json",
    ]
    if args.dry_run:
        run.append("--dry-run")
    try:
        p1 = subprocess.run(plan, cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
        p2 = subprocess.run(run, cwd=str(root), env=env, capture_output=True, text=True, timeout=3600)
        log.write_text(
            json.dumps({
                "brief": args.brief[:500],
                "repo": str(repo),
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
