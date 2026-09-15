#!/usr/bin/env python3
"""A08 — run the repository's test suites and emit a signed quality gate verdict.

Detects pytest / npm test / go test / cargo test, runs the tiers required by the
risk class, parses pass/fail, and writes a signed `gate.verdict` envelope to
.swarm/verdicts/<task_id>.quality.json (also recorded in the Task Store when the
task exists there).
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.script_base import AgentScript, sh, which  # noqa: E402
from swarm.gates import make_verdict, make_finding  # noqa: E402
from swarm.taskstore import TaskStore  # noqa: E402
from swarm.runlog import SWARM_DIR  # noqa: E402
from swarm.errors import SwarmError, ErrorCode  # noqa: E402

TIERS_BY_RISK = {"low": ["unit"], "medium": ["unit", "integration"],
                 "high": ["unit", "integration", "e2e", "perf"]}


def detect_runners(root: Path) -> list[dict]:
    runners = []
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or list(root.glob("tests/test_*.py")):
        if which("pytest"):
            runners.append({"name": "pytest", "cmd": [sys.executable, "-m", "pytest", "-q", "--maxfail=50", "-p", "no:cacheprovider"],
                            "pass_re": r"(\d+) passed", "fail_re": r"(\d+) failed|(\d+) error"})
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text()).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if "test" in scripts and which("npm"):
            runners.append({"name": "npm test", "cmd": ["npm", "test", "--silent", "--", "--ci"],
                            "pass_re": r"Tests:\s+(\d+) passed|(\d+) passing", "fail_re": r"(\d+) failed|(\d+) failing"})
    if (root / "go.mod").exists() and which("go"):
        runners.append({"name": "go test", "cmd": ["go", "test", "./..."], "pass_re": r"^ok", "fail_re": r"^FAIL|--- FAIL"})
    if (root / "Cargo.toml").exists() and which("cargo"):
        runners.append({"name": "cargo test", "cmd": ["cargo", "test", "-q"], "pass_re": r"(\d+) passed", "fail_re": r"(\d+) failed"})
    return runners


def _count(pattern: str, text: str) -> int:
    total = 0
    for m in re.finditer(pattern, text, re.M):
        groups = [g for g in m.groups() if g] if m.groups() else ["1"]
        total += int(groups[0]) if groups and groups[0].isdigit() else 1
    return total


def run(args, ctx) -> dict:
    risk = args.risk_class
    tiers = args.tier.split(",") if args.tier else TIERS_BY_RISK[risk]
    findings, runs, executed = [], {t: "skipped:not-selected" for t in ("unit", "integration", "e2e", "perf")}, []

    if ctx.dry_run:
        runs.update({t: "pass" for t in tiers})
        verdict_env = make_verdict(gate="quality", task_id=args.task_id or "T-dry", agent_id="A08@dry",
                                   findings=[], runs=runs, correlation_id=ctx.correlation_id)
        return {"status": "ok", "verdict": "pass", "runs": runs, "findings": [], "dry_run": True,
                "envelope": verdict_env, "summary": "dry-run: canned pass verdict"}

    runners = detect_runners(ctx.root)
    if not runners:
        findings.append(make_finding("QF-000", "major" if risk != "low" else "minor", "coverage",
                                     "no test runner detected in repository", evidence=str(ctx.root),
                                     owner_suggestion="A05"))
        for t in tiers:
            runs[t] = "skipped:infra"
    else:
        for r in runners:
            proc = sh(r["cmd"], cwd=ctx.root, timeout=args.timeout)
            out = proc.stdout + proc.stderr
            passed, failed = _count(r["pass_re"], out), _count(r["fail_re"], out)
            ok = proc.returncode == 0 and failed == 0
            executed.append({"runner": r["name"], "returncode": proc.returncode, "passed": passed, "failed": failed,
                             "tail": out[-1500:]})
            if "unit" in tiers:
                runs["unit"] = "pass" if ok else "fail"
            if "integration" in tiers and runs["integration"].startswith("skipped"):
                runs["integration"] = "pass" if ok else "fail"
            if not ok:
                findings.append(make_finding(f"QF-{len(findings)+1:03d}", "major", "functional",
                                             f"{r['name']} failed ({failed} failing)", evidence=out[-800:],
                                             owner_suggestion="A05"))
        for t in ("e2e", "perf"):
            if t in tiers:
                runs[t] = "skipped:infra"
                if risk == "high":
                    findings.append(make_finding(f"QF-{len(findings)+1:03d}", "major", t,
                                                 f"{t} tier required for risk_class=high but no harness configured",
                                                 owner_suggestion="A11"))

    # optional coverage (pytest-cov) threshold
    if args.min_coverage and any(e["runner"] == "pytest" for e in executed):
        proc = sh([sys.executable, "-m", "pytest", "-q", "--cov", "--cov-report=term", "-p", "no:cacheprovider"], cwd=ctx.root, timeout=args.timeout)
        m = re.search(r"TOTAL.*?(\d+)%", proc.stdout)
        if m and int(m.group(1)) < args.min_coverage:
            findings.append(make_finding(f"QF-{len(findings)+1:03d}", "major", "coverage",
                                         f"coverage {m.group(1)}% < {args.min_coverage}%", owner_suggestion="A05"))

    env = make_verdict(gate="quality", task_id=args.task_id or "T-unassigned", agent_id="A08@local",
                       findings=findings, runs=runs, correlation_id=ctx.correlation_id)
    verdict = env["payload"]["verdict"]
    out_dir = SWARM_DIR / "verdicts"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.task_id or 'T-unassigned'}.quality.json").write_text(json.dumps(env, indent=2))
    if args.task_id:
        try:
            TaskStore().record_verdict(args.task_id, "quality", verdict, "A08", findings)
        except SwarmError as e:
            if e.code is not ErrorCode.E_INPUT:
                raise
    return {"status": "ok" if verdict == "pass" else "fail", "verdict": verdict, "runs": runs,
            "findings": findings, "executed": executed, "envelope": env,
            "summary": f"quality gate {verdict.upper()} — runners: {[e['runner'] for e in executed] or 'none'}"}


def add_args(p):
    p.add_argument("--risk-class", choices=["low", "medium", "high"], default="medium")
    p.add_argument("--tier", help="comma list overriding risk-based tier selection (unit,integration,e2e,perf)")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--min-coverage", type=int, default=0, help="fail if pytest-cov TOTAL below this percent")


if __name__ == "__main__":
    sys.exit(AgentScript("A08", "qa_gate", run, description=__doc__, add_args=add_args).main())
