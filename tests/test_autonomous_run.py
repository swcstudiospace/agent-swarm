import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "hooks" / "autonomous_run.py"


def test_autonomous_dry_run(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--cwd",
            str(tmp_path),
            "--brief",
            "implement a billing feature",
            "--runtime",
            "auto",
            "--swarm-root",
            str(ROOT),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    log = json.loads((tmp_path / ".swarm" / "autonomous.log").read_text())
    assert log["plan_rc"] == 0
    assert log["run_rc"] == 0
    assert "DONE" in log["run_out"] or "complete" in log["run_out"].lower() or '"complete": true' in log["run_out"]


def test_autonomous_runs_trivia(tmp_path):
    """Auto-run for all prompts: trivia still plans + dry-runs."""
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "what is a monad", "--swarm-root", str(ROOT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    log = json.loads((tmp_path / ".swarm" / "autonomous.log").read_text())
    assert log["plan_rc"] == 0
    assert log["run_rc"] == 0


def test_autonomous_skips_slash(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "/uplift last", "--swarm-root", str(ROOT), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert r.returncode == 0
    assert not (tmp_path / ".swarm" / "autonomous.log").exists()


def test_autonomous_plans_from_uplifted_spec(tmp_path):
    """The brief classifies the kick; the uplifted spec (when given) is what A01 plans from."""
    spec = tmp_path / "spec.xml"
    spec.write_text("<task><goal>Add invoice PDF export</goal><acceptance>PDF matches fixture</acceptance></task>\n")
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "implement invoice export",
         "--spec", str(spec), "--swarm-root", str(ROOT), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    log = json.loads((tmp_path / ".swarm" / "autonomous.log").read_text())
    assert log["plan_rc"] == 0
    assert log["spec"] == str(spec)
    corr = (tmp_path / ".swarm" / "latest_correlation").read_text().strip()
    plan = json.loads((tmp_path / ".swarm" / "plans" / f"{corr}.json").read_text())
    assert plan["brief"].strip() == spec.read_text().strip()


def test_autonomous_skips_run_on_plan_failure(tmp_path):
    import shutil
    root = tmp_path / "swarm-root"
    shutil.copytree(ROOT / "swarm", root / "swarm")
    (root / "scripts").mkdir()
    sentinel = tmp_path / "ran"
    (root / "scripts" / "orch_plan.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'status': 'error', 'error': {'code': 'E-CONTRACT', 'message': 'prefix taken'}}))\n"
        "sys.exit(2)\n")
    (root / "scripts" / "swarm_run.py").write_text(f"open({str(sentinel)!r}, 'w').write('x')\n")
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "implement a billing feature",
         "--swarm-root", str(root), "--dry-run"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert not sentinel.exists()
    log = json.loads((tmp_path / ".swarm" / "autonomous.log").read_text())
    assert log["plan_rc"] == 2 and log["skipped_run"] is True
    assert log["error"]["code"] == "E-CONTRACT"
    assert "run_rc" not in log


def test_autonomous_forwards_runtime_omp(tmp_path):
    import shutil
    root = tmp_path / "swarm-root"
    shutil.copytree(ROOT / "swarm", root / "swarm")
    (root / "scripts").mkdir()
    events = tmp_path / "events.jsonl"
    (root / "scripts" / "orch_plan.py").write_text(
        "import json, sys\n"
        f"open({str(events)!r}, 'a').write(json.dumps(['plan', sys.argv[1:]]) + '\\n')\n"
        "print(json.dumps({'status': 'ok', 'correlation_id': 'corr-omp-1'}))\n")
    (root / "scripts" / "swarm_run.py").write_text(
        "import json, sys\n"
        f"open({str(events)!r}, 'a').write(json.dumps(['run', sys.argv[1:]]) + '\\n')\n")
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "implement a billing feature",
         "--swarm-root", str(root), "--runtime", "omp", "--dry-run"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    calls = [json.loads(line) for line in events.read_text().splitlines()]
    assert [name for name, _ in calls] == ["plan", "run"]
    run_argv = calls[1][1]
    assert run_argv[run_argv.index("--runtime") + 1] == "omp"
    assert run_argv[run_argv.index("--correlation-id") + 1] == "corr-omp-1"
    assert "--dry-run" in run_argv
    log = json.loads((tmp_path / ".swarm" / "autonomous.log").read_text())
    assert log["plan_rc"] == 0 and log["run_rc"] == 0
