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


def test_autonomous_skips_trivia(tmp_path):
    env = {**os.environ, "SWARM_DIR": str(tmp_path / ".swarm")}
    r = subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(tmp_path), "--brief", "what is a monad", "--swarm-root", str(ROOT), "--dry-run"],
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
